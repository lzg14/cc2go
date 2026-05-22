"""
cc2go - Claude Code → OpenCode Go 格式适配器
Claude Code (Anthropic) -> OpenAI 格式 -> OpenCode Go
支持多轮对话中的工具调用循环
"""

from __future__ import annotations
import os
import sys
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime
from typing import List, Dict

import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from streaming import convert_openai_stream_to_anthropic
from mcp_bypass import should_bypass, handle_bypass, extract_query
from error_handler import (
    classify_and_suggest_action,
    get_backoff_delay,
    parse_upstream_error,
    RetryStrategy,
    _archive_limiter as error_archive_limiter,
)


def verify_master_key(request: Request) -> None:
    """Verify that the request has a valid Authorization header matching the configured master key"""
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        scheme, token = authorization.split(" ", 1)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication scheme",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if token != config.master_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_base_dir():
    """项目根目录，兼容 PyInstaller onefile 打包"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 确保运行时目录存在
for _rd in ("data", "logs"):
    os.makedirs(os.path.join(get_base_dir(), _rd), exist_ok=True)

# 错误现场归档
def save_error_archive(timestamp, model, request_body, openai_payload, response_text, status_code):
    """400 错误时自动保存完整上下文到 error-archive/，便于事后复盘"""
    archive_dir = config.error_archive_dir
    os.makedirs(archive_dir, exist_ok=True)
    safe_ts = timestamp.replace(":", "").replace("/", "-")
    path = os.path.join(archive_dir, f"{safe_ts}-{model}-{status_code}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp,
                "model": model,
                "status": status_code,
                "anthropic_request": request_body,
                "openai_request": openai_payload,
                "upstream_response": response_text,
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"[Error Archive] 已保存错误现场: {os.path.basename(path)}")
    except Exception as e:
        logger.warning(f"[Error Archive] 归档失败: {e}")


def update_archive_limiter(interval_seconds: int):
    """更新错误归档限速间隔"""
    error_archive_limiter.update(max(interval_seconds, 1))


VERSION = "0.7.4"

# ============ 配置 ============
DEFAULT_MODELS = {
    "glm-5.1": {"id": "glm-5.1", "endpoint": "/v1/chat/completions"},
    "glm-5": {"id": "glm-5", "endpoint": "/v1/chat/completions"},
    "kimi-k2.6": {"id": "kimi-k2.6", "endpoint": "/v1/chat/completions"},
    "kimi-k2.5": {"id": "kimi-k2.5", "endpoint": "/v1/chat/completions"},
    "qwen3.6-plus": {"id": "qwen3.6-plus", "endpoint": "/v1/chat/completions"},
    "qwen3.5-plus": {"id": "qwen3.5-plus", "endpoint": "/v1/chat/completions"},
    "deepseek-v4-pro": {"id": "deepseek-v4-pro", "endpoint": "/v1/chat/completions"},
    "deepseek-v4-flash": {"id": "deepseek-v4-flash", "endpoint": "/v1/chat/completions"},
    "mimo-v2.5": {"id": "mimo-v2.5", "endpoint": "/v1/chat/completions"},
    "mimo-v2.5-pro": {"id": "mimo-v2.5-pro", "endpoint": "/v1/chat/completions"},
    "minimax-m2.7": {"id": "minimax-m2.7", "endpoint": "/v1/messages"},
    "minimax-m2.5": {"id": "minimax-m2.5", "endpoint": "/v1/messages"},
}

CUSTOM_MODELS_FILE = os.path.join(get_base_dir(), "data", "custom_models.json")

def load_custom_models():
    try:
        with open(CUSTOM_MODELS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_custom_models(models):
    with open(CUSTOM_MODELS_FILE, "w") as f:
        json.dump(models, f, indent=2, ensure_ascii=False)

def merge_models(upstream, custom):
    """合并上游模型和自定义模型，自定义模型优先"""
    merged = dict(upstream)
    for m in custom:
        mid = m["id"]
        ep = m.get("endpoint", "/v1/chat/completions")
        merged[mid] = {"id": mid, "endpoint": ep}
    return merged


class Config:
    def __init__(self):
        self.opencode_base_url = os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/go")
        self.opencode_api_key = os.getenv("OPENCODE_API_KEY", "")
        self.router_port = int(os.getenv("ROUTER_PORT", "4001"))
        self.router_host = os.getenv("ROUTER_HOST", "127.0.0.1")
        self.master_key = os.getenv("ROUTER_MASTER_KEY", "sk-cc2go-local")
        self.max_retry = int(os.getenv("MAX_RETRY", "3"))
        self.retry_delay = float(os.getenv("RETRY_DELAY", "1.0"))
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        _log_file = os.getenv("LOG_FILE", os.path.join(get_base_dir(), "logs", "router.log"))
        if not os.path.isabs(_log_file):
            _log_file = os.path.join(get_base_dir(), _log_file)
        self.log_file = _log_file
        self.disable_thinking = os.getenv("DISABLE_THINKING", "true").lower() == "true"
        self.detailed_logging = os.getenv("DETAILED_LOGGING", "true").lower() == "true"
        self.selected_model = os.getenv("SELECTED_MODEL", "")
        self.claude_model_alias = os.getenv("CLAUDE_MODEL_ALIAS", "")
        self.claude_settings_path = os.getenv("CLAUDE_SETTINGS_PATH", os.path.expanduser("~/.claude/settings.json"))
        _archive_dir = os.getenv("ERROR_ARCHIVE_DIR", os.path.join(get_base_dir(), "error-archive"))
        if not os.path.isabs(_archive_dir):
            _archive_dir = os.path.join(get_base_dir(), _archive_dir)
        self.error_archive_dir = _archive_dir
        self.error_archive_interval = int(os.getenv("ERROR_ARCHIVE_INTERVAL", "30"))
        self.fallback_models = [
            m.strip() for m in os.getenv("FALLBACK_MODELS", "").split(",")
            if m.strip()
        ]
        self.models = merge_models(DEFAULT_MODELS, load_custom_models())

    def reload(self):
        load_dotenv(override=True)
        self.__init__()
        logger.setLevel(getattr(logging, config.log_level.upper()))
        update_archive_limiter(config.error_archive_interval)

load_dotenv()
config = Config()
update_archive_limiter(config.error_archive_interval)

_update_cache: Dict = {"latest_version": "", "checked": False}
_UPDATE_CHECK_LOCK = asyncio.Lock()

# ============ 模型模糊匹配 ============
# 当客户端请求的模型名在 config.models 中无精确匹配时，
# 按 haiku/sonnet/opus 等关键词模糊匹配到可用模型
DEFAULT_MODEL_HAIKU = os.getenv("MODEL_HAIKU", "deepseek-v4-flash")
DEFAULT_MODEL_SONNET = os.getenv("MODEL_SONNET", "deepseek-v4-pro")
DEFAULT_MODEL_OPUS = os.getenv("MODEL_OPUS", "glm-5.1")

# 优先使用已配置模型中匹配层级的关键词，否则用环境变量指定的默认值
_MODEL_TIER_KEYWORDS = {
    "haiku": DEFAULT_MODEL_HAIKU,
    "sonnet": DEFAULT_MODEL_SONNET,
    "opus": DEFAULT_MODEL_OPUS,
}


def resolve_model_name(model_name: str, available_models: dict) -> str | None:
    """
    模型名解析：精确匹配 → 模糊匹配（按关键词）→ None
    优先级：
    1. 精确匹配 available_models 中的 key
    2. 按 haiku/sonnet/opus 关键词从 available_models 中选取
       （仅在 available_models 中有该模型时使用，否则退回到默认值）
    3. 返回 None（由调用方决定 fallback 行为）
    """
    # 1. 精确匹配
    if model_name in available_models:
        return model_name

    # 2. 关键词模糊匹配
    lower_name = model_name.lower()

    for keyword, default_model in _MODEL_TIER_KEYWORDS.items():
        if keyword in lower_name:
            # 优先在 available_models 中找 keyword 匹配项（例如请求 "anything-haiku"
            # 时有 "claude-haiku-3" 才真正匹配，而不是直接跳到默认值 deepseek-v4-flash）
            for avail in available_models:
                if keyword in avail.lower():
                    return avail
            # available_models 中没有 keyword 匹配的模型，再检查默认值是否在其中
            if default_model in available_models:
                return default_model
            # 默认值也不在 available_models 中，返回 None 让调用方走通用 fallback
            return None

    return None


def setup_logger():
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("llm_router")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, config.log_level.upper()))
    file_handler = RotatingFileHandler(config.log_file, encoding="utf-8", maxBytes=5*1024*1024, backupCount=3)
    console_handler = logging.StreamHandler()
    file_handler.setLevel(getattr(logging, config.log_level.upper()))
    console_handler.setLevel(getattr(logging, config.log_level.upper()))
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

logger = setup_logger()

if not config.opencode_api_key:
    logger.warning("[!] OPENCODE_API_KEY 未配置！请在 Web UI 的「连接」设置中配置，或创建 .env 文件")
else:
    logger.info("[OK] OpenCode Go API Key 已配置")

# ============ FastAPI ============
app = FastAPI(title="cc2go", description="Claude Code → OpenCode Go 格式适配器")

# 所有 API 响应禁用浏览器缓存
@app.middleware("http")
async def add_nocache_headers(request, call_next):
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

try:
    _sd = os.path.join(get_base_dir(), "static")
    if os.path.exists(_sd):
        app.mount("/static", StaticFiles(directory=_sd), name="static")
except Exception:
    pass

# 请求统计（持久化到文件）
STATS_FILE = os.path.join(get_base_dir(), "data", "stats.json")
_stats_dirty = 0
_stats_lock = threading.Lock()

def load_stats():
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"requests": 0, "errors": 0}

def increment_requests():
    global request_count
    with _stats_lock:
        request_count += 1
        save_stats_unlocked()

def increment_errors():
    global error_count
    with _stats_lock:
        error_count += 1
        save_stats_unlocked()

def save_stats_unlocked():
    """无锁版 save_stats，供内部调用"""
    global _stats_dirty
    try:
        _stats_dirty += 1
        if _stats_dirty < 10:
            return
        with open(STATS_FILE, "w") as f:
            json.dump({"requests": request_count, "errors": error_count}, f)
        _stats_dirty = 0
    except Exception:
        pass

stats = load_stats()
request_count = stats["requests"]
error_count = stats["errors"]


def strip_system_reminder(text: str) -> str:
    """移除用户消息中的 <system-reminder> 块（Claude Code 注入的技能提示），防止触发上游内容过滤"""
    return re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL).strip()


def strip_reasoning(text: str) -> str:
    """移除 [思考过程]/[思考] 前缀块，省 token。只保留实际内容"""
    for prefix in ("[思考过程]", "[思考]"):
        if text.startswith(prefix):
            rest = text[len(prefix):].strip()
            if "\n" in rest:
                _, after = rest.split("\n", 1)
                return after.strip()
            return ""
    return text


def strip_thinking_from_messages(messages: List[Dict]) -> List[Dict]:
    """移除消息中所有 thinking 类型的内容块"""
    result = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            cleaned = [b for b in content if not isinstance(b, dict) or b.get("type") != "thinking"]
            if len(cleaned) != len(content):
                msg = dict(msg)
                msg["content"] = cleaned
        result.append(msg)
    return result


def convert_anthropic_messages_to_openai(messages: List[Dict]) -> List[Dict]:
    """
    将 Claude 格式的消息转换为 OpenAI 格式
    处理 tool_use 和 tool_result
    """
    openai_messages = []

    for idx, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # 检查消息顶层是否已携带 OpenAI 格式字段（兼容半转换消息）
        top_tool_calls = msg.get("tool_calls")
        top_reasoning = msg.get("reasoning_content")
        top_tool_call_id = msg.get("tool_call_id")

        # 处理已是 OpenAI 格式的 tool 消息（role=tool + tool_call_id）
        if role == "tool" and top_tool_call_id:
            openai_messages.append({
                "role": "tool",
                "tool_call_id": top_tool_call_id,
                "content": content if isinstance(content, str) else str(content),
            })
            continue

        # 处理已是 OpenAI 格式的 assistant 消息（带顶层 tool_calls 或 reasoning_content）
        if (isinstance(content, str) or content is None) and (top_tool_calls or top_reasoning is not None):
            result = {"role": role}
            c = content if content else None
            if c is not None and role == "assistant":
                c = strip_reasoning(strip_system_reminder(c))
            result["content"] = c
            if top_tool_calls:
                result["tool_calls"] = top_tool_calls
                if top_reasoning is None and role == "assistant":
                    result["reasoning_content"] = ""
            if top_reasoning:
                result["reasoning_content"] = top_reasoning
            openai_messages.append(result)
            continue

        # 处理 Claude 的 content 数组结构
        if isinstance(content, list):
            content_items = []  # [{type, text/url}, ...]
            has_image = False
            tool_calls_list = []
            tool_results = []
            reasoning_content = None

            for item in content:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type", "")

                if item_type == "thinking" and role == "assistant":
                    text = item.get("thinking", "") or item.get("text", "")
                    reasoning_content = text

                elif item_type == "text":
                    t = item.get("text", "")
                    t = strip_system_reminder(t)
                    if role == "assistant":
                        t = strip_reasoning(t)
                    content_items.append({"type": "text", "text": t})

                elif item_type == "image":
                    has_image = True
                    src = item.get("source", {})
                    content_items.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                        }
                    })

                elif item_type == "tool_use":
                    tool_data = item.get("tool_use") or item
                    tool_id = (
                        tool_data.get("id")
                        or tool_data.get("tool_use_id")
                        or tool_data.get("call_id")
                        or f"tc_{idx}_{len(tool_calls_list)}"
                    )
                    tool_calls_list.append({
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": tool_data.get("name", ""),
                            "arguments": json.dumps(tool_data.get("input", {}), ensure_ascii=False)
                        }
                    })
                    if config.detailed_logging:
                        logger.debug(f"[Tool] id={tool_id}, name={tool_data.get('name', '')}")

                elif item_type == "tool_result":
                    tool_data = item.get("tool_result") or item
                    result_content = tool_data.get("content", "")

                    if isinstance(result_content, list):
                        text_parts_result = []
                        for part in result_content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text_parts_result.append(part.get("text", ""))
                        result_content = "\n".join(text_parts_result)

                    tool_use_id = (
                        tool_data.get("tool_use_id")
                        or tool_data.get("tool_call_id")
                        or tool_data.get("id")
                        or f"tc_{idx}_{len(tool_results)}"
                    )
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tool_use_id,
                        "content": str(result_content) if result_content else ""
                    })

            # 合并 content_items 和 tool_calls 到一条消息
            if content_items or tool_calls_list or reasoning_content:
                msg_dict = {"role": role}
                if content_items:
                    if has_image:
                        msg_dict["content"] = content_items
                    else:
                        texts = [c["text"] for c in content_items]
                        msg_dict["content"] = "\n".join(texts)
                else:
                    msg_dict["content"] = None
                if tool_calls_list:
                    msg_dict["tool_calls"] = tool_calls_list
                if reasoning_content is not None:
                    msg_dict["reasoning_content"] = reasoning_content
                elif tool_calls_list and role == "assistant":
                    msg_dict["reasoning_content"] = ""
                openai_messages.append(msg_dict)
            elif isinstance(content, list) and not tool_results:
                # content 数组处理后没有任何产出（如 user 消息仅含 thinking 块），且无 tool_result
                openai_messages.append({"role": role, "content": ""})
            # else: content_items 等都为空但有 tool_results 时，
            # 不单独发 user 消息，直接 extend tool_results
            #（避免产生 content=None 的空 user 消息）

            # 添加 tool 结果（跟在用户消息之后）
            openai_messages.extend(tool_results)

        elif content is not None:
            # content="" 不应被丢弃：assistant 空 content 消息在多轮 tool_use 对话中常见，
            # 丢弃会导致 DeepSeek 等 API 报错 "reasoning_content must be passed back"
            c = content
            if c:
                c = strip_system_reminder(c)
                if role == "assistant":
                    c = strip_reasoning(c)
            openai_messages.append({"role": role, "content": c})

    return openai_messages


def sanitize_tool_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r'[^a-zA-Z0-9_.\-]', '_', name)
    name = re.sub(r'_+', '_', name)
    name = name.strip('_')
    return name if name else "unknown_tool"


def clean_schema(obj: Any) -> Any:
    if isinstance(obj, dict):
        obj.pop("$schema", None)
        additional_props = obj.get("additionalProperties")
        if additional_props is False:
            obj.pop("additionalProperties", None)
        return {k: clean_schema(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_schema(item) for item in obj]
    return obj


def convert_tools(tools: List[Dict]) -> List[Dict]:
    """将 Claude 格式的工具转换为 OpenAI 格式"""
    if not tools:
        return []

    openai_tools = []
    for tool in tools:
        func = tool.get("function", {})
        name = func.get("name", "") or tool.get("name", "")
        name = name.strip()

        if not name:
            logger.warning(f"[Tool] Skipping tool with empty name: {tool}")
            continue

        name = sanitize_tool_name(name)

        description = func.get("description", "") or tool.get("description", "") or ""
        parameters = func.get("parameters", {}) or tool.get("input_schema", {}) or {"type": "object", "properties": {}}
        parameters = clean_schema(parameters)

        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": str(description),
                "parameters": parameters
            }
        })
    return openai_tools


def convert_response_to_anthropic(result: Dict, model: str) -> Dict:
    """
    将 OpenAI 响应转换为 Claude 格式
    正确处理 reasoning_content (思考模式) 和 tool_calls
    """
    choices = result.get("choices", [])

    if not choices:
        return {
            "type": "message",
            "id": f"msg-{int(time.time() * 1000)}",
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "model": model,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }

    choice = choices[0]
    message = choice.get("message", {})
    finish_reason = choice.get("finish_reason", "stop")

    # 构建 content
    content_items = []

    # 处理 reasoning_content (思考模式的模型: DeepSeek, MiMo, Xiaomi 等)
    # 保留在响应中，请求转换侧会提取回 reasoning_content 字段
    reasoning = message.get("reasoning_content", "")
    if reasoning:
        content_items.append({
            "type": "text",
            "text": f"[思考过程]\n{reasoning}"
        })

    # 处理普通文本内容
    text_content = message.get("content", "")
    if text_content:
        content_items.append({
            "type": "text",
            "text": text_content
        })

    # 处理 tool_calls
    tool_calls = message.get("tool_calls", [])
    if tool_calls:
        for tc in tool_calls:
            func = tc.get("function", {})
            call_id = (
                tc.get("id")
                or tc.get("tool_call_id")
                or tc.get("call_id")
                or f"tc_{int(time.time() * 1000)}"
            )
            content_items.append({
                "type": "tool_use",
                "id": call_id,
                "name": func.get("name", ""),
                "input": json.loads(func.get("arguments", "{}")) if isinstance(func.get("arguments"), str) else func.get("arguments", {})
            })

    # 如果没有内容，给一个空文本
    if not content_items:
        content_items = [{"type": "text", "text": ""}]

    # 决定 stop_reason
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
    }
    mapped_stop_reason = stop_reason_map.get(finish_reason, finish_reason)

    return {
        "type": "message",
        "id": result.get("id", f"msg-{int(time.time() * 1000)}"),
        "role": "assistant",
        "content": content_items,
        "model": model,
        "stop_reason": mapped_stop_reason,
        "usage": {
            "input_tokens": result.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": result.get("usage", {}).get("completion_tokens", 0)
        }
    }


async def call_opencode(endpoint: str, payload: dict, base_url: str = None, api_key: str = None, full_url: str = None) -> httpx.Response:
    """调用 API，带重试。连接异常时重建 client 避免复用坏连接"""
    url = full_url or f"{base_url or config.opencode_base_url}{endpoint}"
    key = api_key or config.opencode_api_key
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "x-api-key": key
    }
    fallback_idx = 0  # <-- 初始化在循环外
    client = httpx.AsyncClient(timeout=180.0)
    try:
        for attempt in range(config.max_retry):
            try:
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code == 200:
                    return response

                # 解析响应体
                try:
                    raw_body = json.loads(response.text)
                except Exception:
                    raw_body = response.text

                strategy, log_msg, hint = classify_and_suggest_action(
                    response.status_code, raw_body, attempt, config.max_retry
                )
                logger.warning(log_msg)

                # 限速归档
                if response.status_code >= 400 and error_archive_limiter.archive():
                    save_error_archive(
                        datetime.now().isoformat(),
                        payload.get("model", "unknown"),
                        payload,
                        None,
                        response.text,
                        response.status_code
                    )

                if strategy == RetryStrategy.FAIL_FAST:
                    raise HTTPException(status_code=response.status_code, detail=parse_upstream_error(raw_body))

                if strategy == RetryStrategy.SWITCH_MODEL:
                    if fallback_idx < len(config.fallback_models):
                        fallback_model = config.fallback_models[fallback_idx]
                        fallback_idx += 1
                        logger.info(f"[Fallback] 切换到模型: {fallback_model}")
                        payload = dict(payload, model=fallback_model)
                        continue
                    else:
                        raise HTTPException(status_code=response.status_code, detail=parse_upstream_error(raw_body))

                if strategy == RetryStrategy.RETRY_WITH_BACKOFF:
                    delay = get_backoff_delay(attempt)
                    logger.info(f"[Retry] 退避 {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue

            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} error: {e}")
                if attempt < config.max_retry - 1:
                    await asyncio.sleep(get_backoff_delay(attempt))
                else:
                    raise HTTPException(status_code=500, detail=str(e))
    finally:
        await client.aclose()

    raise HTTPException(status_code=500, detail="OpenCode API 调用失败")


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    """
    Claude 格式入口 - 完整支持 tool_calls 循环
    """
    # Verify master key authentication
    verify_master_key(request)
    global request_count, error_count
    start_time = time.time()
    model_name = None

    try:
        body = await request.json()
        logger.debug(f"[ENTER] raw_body_model={body.get('model')}, stream={body.get('stream')}, msgs={len(body.get('messages',[]))}")

        # MCP 工具短路检测
        bypass, tool_name = should_bypass(body)
        if bypass:
            logger.info(f"[Bypass] tool={tool_name}, shortcutting request")
            query = extract_query(body.get("messages", []))
            result = await handle_bypass(tool_name, query)
            return JSONResponse(content=result)

        model_name = body.get("model", "glm-5.1")
        # 如果管理员在页面选了模型，覆盖客户端传来的模型名
        if config.selected_model:
            model_name = config.selected_model
        messages = body.get("messages", [])
        tools = body.get("tools", [])

        logger.info(f"[Request] model={model_name}, messages={len(messages)}, tools={len(tools) if tools else 0}, stream={body.get('stream')}")

        request_body_stream = body.get("stream")

        # 获取模型配置（含模糊匹配：haiku/sonnet/opus → 最接近的可用模型）
        model_config = config.models.get(model_name)
        if model_config:
            model_id = model_config["id"]
            endpoint = model_config["endpoint"]
        else:
            resolved = resolve_model_name(model_name, config.models)
            if resolved:
                logger.info(f"[Model] 模糊匹配 {model_name} → {resolved}")
                model_config = config.models[resolved]
                model_id = model_config["id"]
                endpoint = model_config["endpoint"]
            else:
                model_id = model_name
                endpoint = "/v1/chat/completions"

        # 如果是自定义模型，使用其独立连接信息
        custom_base = None
        custom_key = None
        custom_ep = None
        custom_upstream_model = None
        for cm in load_custom_models():
            if cm["id"] == model_name:
                raw_base = cm.get("base_url") or cm.get("url") or ""
                if raw_base:
                    custom_base = raw_base.rstrip("/")
                    custom_ep = cm.get("endpoint", "")
                    custom_upstream_model = cm.get("model") or cm.get("upstream_model") or model_name
                    if cm.get("api_key"):
                        custom_key = cm["api_key"]
                break

        logger.debug(f"[Route] model={model_name}, endpoint={endpoint}, custom_base={custom_base}, custom_ep={custom_ep}, custom_upstream={custom_upstream_model}, model_id={model_id}")

        # 清除 output_config（某些 API 拒绝 empty tools + json_schema）
        if not tools and body.get("output_config", {}).get("format", {}).get("type") == "json_schema":
            body.pop("output_config", None)

        # 自定义模型透传：仅对 Anthropic 格式端点保留直传，OpenAI 格式走正常转换路径
        if custom_base and custom_ep == "/v1/messages":
            body["model"] = custom_upstream_model
            body["stream"] = False
            full_url = custom_base + custom_ep
            logger.debug(f"[Passthrough] model={custom_upstream_model}, url={full_url}, stream={body.get('stream')}, body_stream_orig={request_body_stream}")
            response = await call_opencode("", body, api_key=custom_key, full_url=full_url)
            raw_text = response.text
            is_sse = raw_text.strip().startswith("event:") if raw_text else False
            logger.debug(f"[Passthrough Response] model={model_name}, status={response.status_code}, is_sse={is_sse}, content_type={response.headers.get('content-type','?')}, len={len(raw_text)}")
            if response.status_code != 200:
                logger.error(f"[Passthrough] {model_name} status={response.status_code}: {raw_text[:500]}")
                if response.status_code >= 400 and error_archive_limiter.archive():
                    save_error_archive(
                        datetime.now().isoformat(), model_name, body, None, raw_text, response.status_code
                    )
                raise HTTPException(status_code=response.status_code, detail=raw_text[:2000])
            if config.detailed_logging:
                logger.info(f"[Raw Response] model={model_name}, body={raw_text[:2000]}")
            try:
                result = json.loads(raw_text) if raw_text else {}
                return JSONResponse(content=result)
            except Exception:
                return PlainTextResponse(raw_text)

        # MiniMax 用 /v1/messages 端点
        if endpoint == "/v1/messages":
            body["thinking"] = {"type": "disabled"}
            body["stream"] = False
            body["messages"] = strip_thinking_from_messages(body.get("messages", []))
            logger.debug(f"[MiniMax] model={model_name}, endpoint={endpoint}, stream={body.get('stream')}, custom_base={custom_base}, custom_key={'***' if custom_key else 'None'}")
            if config.detailed_logging:
                logger.debug(f"[MiniMax Payload] {json.dumps(body, ensure_ascii=False)[:1000]}")
            logger.debug(f"[Payload] Direct forward to {endpoint} with thinking disabled, stream={body.get('stream')}")
            response = await call_opencode(endpoint, body, api_key=custom_key, base_url=custom_base)

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"[Error] OpenCode API status={response.status_code}: {error_detail[:500]}")
                if response.status_code >= 400 and error_archive_limiter.archive():
                    save_error_archive(
                        datetime.now().isoformat(), model_name, body, None, error_detail, response.status_code
                    )
                raise HTTPException(status_code=response.status_code, detail=error_detail)

            raw_text = response.text
            is_sse = raw_text.strip().startswith("event:") if raw_text else False
            logger.debug(f"[MiniMax Response] model={model_name}, status={response.status_code}, is_sse={is_sse}, content_type={response.headers.get('content-type','?')}, len={len(raw_text)}")
            if config.detailed_logging:
                logger.info(f"[Raw Response] model={model_name}, body={raw_text[:2000]}")
            try:
                result = json.loads(raw_text) if raw_text else {}
            except Exception:
                result = {"type": "message", "content": [{"type": "text", "text": raw_text}]}

            duration = time.time() - start_time
            increment_requests()
            logger.info(f"[OK] {model_name} ({duration:.2f}s)")

            return JSONResponse(content=result)

        # 其他端点需要转换格式
        logger.debug(f"[ConvertOpenAI] model={model_name}, endpoint={endpoint}, is_stream={body.get('stream', False)}, custom_base={custom_base}")
        openai_messages = convert_anthropic_messages_to_openai(messages)
        openai_tools = convert_tools(tools) if tools else None

        # 构建请求
        openai_payload = {
            "model": custom_upstream_model or model_id,
            "messages": openai_messages,
        }
        if openai_tools:
            openai_payload["tools"] = openai_tools

        # 全量日志：发往上游的请求
        if config.detailed_logging:
            logger.info(f"[Request Payload] model={model_name}, endpoint={endpoint}, "
                         f"payload={json.dumps(openai_payload, ensure_ascii=False)[:3000]}")

        # 检查是否流式请求
        is_stream = body.get("stream", False)
        if is_stream:
            logger.debug(f"[Stream] model={model_name}, streaming enabled")
            response = await call_opencode(endpoint, openai_payload, api_key=custom_key, base_url=custom_base)
            if response.status_code != 200:
                logger.error(f"[Error] OpenCode API: status={response.status_code}")
                raise HTTPException(status_code=response.status_code, detail=response.text)
            increment_requests()
            return StreamingResponse(
                convert_openai_stream_to_anthropic(response, model_name),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Request-ID": f"req-{int(time.time() * 1000)}",
                }
            )

        # 调用 API（非流式）
        response = await call_opencode(endpoint, openai_payload, api_key=custom_key, base_url=custom_base)

        # 全量日志：上游原始响应
        try:
            raw_text = response.text
            if config.detailed_logging:
                logger.info(f"[Raw Response] model={model_name}, status={response.status_code}, "
                             f"body={raw_text[:3000]}")
            result = json.loads(raw_text)
        except Exception as e:
            if config.detailed_logging:
                logger.error(f"[Raw Response] model={model_name}, parse error: {e}, "
                             f"body={response.text[:3000]}")
            raise HTTPException(status_code=500, detail=f"上游响应解析失败: {e}")

        if response.status_code != 200:
            logger.error(f"[Error] OpenCode API: status={response.status_code}, body={raw_text[:2000]}")
            if response.status_code >= 400 and error_archive_limiter.archive():
                save_error_archive(
                    datetime.now().isoformat(), model_name, body, openai_payload, raw_text, response.status_code
                )
            raise HTTPException(status_code=response.status_code, detail=raw_text[:2000])

        # 转换响应
        anthropic_response = convert_response_to_anthropic(result, model_name)

        # 全量日志：转换后的 Anthropic 格式响应
        if config.detailed_logging:
            logger.info(f"[Anthropic Response] model={model_name}, "
                         f"body={json.dumps(anthropic_response, ensure_ascii=False)[:2000]}")

        duration = time.time() - start_time
        increment_requests()
        logger.info(f"[OK] {model_name} ({duration:.2f}s)")

        return JSONResponse(content=anthropic_response)

    except HTTPException:
        increment_errors()
        raise
    except Exception as e:
        increment_errors()
        duration = time.time() - start_time
        logger.error(f"[Error] {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI 格式入口（透传）"""
    # Verify master key authentication
    verify_master_key(request)
    global request_count
    start_time = time.time()

    try:
        body = await request.json()
        model = body.get("model", "glm-5.1")

        model_config = config.models.get(model)
        endpoint = model_config["endpoint"] if model_config else "/v1/chat/completions"

        response = await call_opencode(endpoint, body)

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)

        duration = time.time() - start_time
        increment_requests()
        logger.info(f"[OK] {model} OpenAI format ({duration:.2f}s)")

        return JSONResponse(content=response.json())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Error] {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/v1/models")
async def list_models():
    return JSONResponse(content={
        "object": "list",
        "data": [
            {"id": name, "object": "model", "created": 1700000000, "owned_by": "opencode-go"}
            for name in config.models.keys()
        ]
    })


@app.get("/health")
async def health():
    return JSONResponse(content={
        "status": "ok",
        "uptime": "running",
        "requests": request_count,
        "errors": error_count,
        "models": list(config.models.keys())
    })


@app.get("/stats")
async def get_stats():
    return JSONResponse(content={
        "total_requests": request_count,
        "total_errors": error_count,
        "error_rate": f"{min((error_count / max(request_count, 1)) * 100, 100):.1f}%"
    })


@app.post("/reload")
async def reload_config():
    config.reload()
    return JSONResponse(content={"status": "ok"})


# ============ 配置管理 API ============
def mask_key(key: str) -> str:
    """对密钥脱敏，只显示首尾各 4 位"""
    if not key or len(key) < 8:
        return ""
    return key[:4] + "***" + key[-4:]


def update_env_file(**kwargs):
    """更新 .env 文件中的键值对，保留注释和格式"""
    env_path = ".env"
    keys_updated = set()
    lines = []

    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#"):
                    key = stripped.split("=", 1)[0].strip()
                    if key in kwargs:
                        lines.append(f"{key}={kwargs[key]}\n")
                        keys_updated.add(key)
                    else:
                        lines.append(line)
                else:
                    lines.append(line)

    for key, value in kwargs.items():
        if key not in keys_updated:
            lines.append(f"\n{key}={value}\n")

    tmp = env_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, env_path)

    config.reload()


def backup_claude_settings():
    """首次修改前备份原始 ~/.claude/settings.json，仅备份一次"""
    path = config.claude_settings_path
    if not path or not os.path.exists(path):
        logger.debug("[Backup] settings.json 不存在，跳过备份")
        return

    backup_dir = os.path.join(get_base_dir(), "data", "claude-backups")
    os.makedirs(backup_dir, exist_ok=True)

    # 检查是否已有备份文件
    try:
        existing = [f for f in os.listdir(backup_dir) if f.endswith(".bak")]
        if existing:
            logger.debug(f"[Backup] 备份已存在 ({len(existing)} 个)，跳过")
            return
    except OSError:
        pass

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(backup_dir, f"settings.json.{timestamp}.bak")
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[Backup] 已备份原始配置: {backup_path}")
    except Exception as e:
        logger.warning(f"[Backup] 备份失败: {e}")


def sync_claude_settings():
    """同步路由器配置到 Claude Code 的 settings.json（模型名、Base URL、Auth Token）"""
    # 修改前备份原始配置（仅首次）
    backup_claude_settings()

    model_name = config.selected_model
    display_name = config.claude_model_alias or model_name
    base_url = f"http://{config.router_host}:{config.router_port}"
    auth_token = config.master_key
    path = config.claude_settings_path
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        env = settings.setdefault("env", {})
        if model_name:
            settings["model"] = display_name
            env["ANTHROPIC_MODEL"] = display_name
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = display_name
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = display_name
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = display_name
        env["ANTHROPIC_BASE_URL"] = base_url
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
        logger.info(f"[Config] Claude Code synced: model={display_name}, base_url={base_url}")
        return True
    except Exception as e:
        logger.warning(f"[Config] Failed to sync Claude Code settings: {e}")
        return False


async def _check_github_update():
    async with _UPDATE_CHECK_LOCK:
        if _update_cache["checked"]:
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get("https://api.github.com/repos/lzg14/cc2go/releases/latest")
                if r.status_code == 200:
                    latest = r.json().get("tag_name", "").lstrip("v")
                    _update_cache["latest_version"] = latest
        except Exception:
            pass
        _update_cache["checked"] = True


def _is_newer_version(latest: str, current: str) -> bool:
    try:
        l_parts = [int(x) for x in latest.split(".")]
        c_parts = [int(x) for x in current.split(".")]
        for l, c in zip(l_parts, c_parts):
            if l != c:
                return l > c
        return len(l_parts) > len(c_parts)
    except Exception:
        return False


@app.get("/api/config")
async def get_config_api():
    """返回当前配置（密钥脱敏）"""
    return {
        "version": VERSION,
        "setup_complete": bool(config.opencode_api_key),
        "opencode_base_url": config.opencode_base_url,
        "opencode_api_key": mask_key(config.opencode_api_key),
        "router_host": config.router_host,
        "router_port": config.router_port,
        "master_key": mask_key(config.master_key),
        "max_retry": config.max_retry,
        "retry_delay": config.retry_delay,
        "log_level": config.log_level,
        "disable_thinking": config.disable_thinking,
        "detailed_logging": config.detailed_logging,
        "error_archive_dir": config.error_archive_dir,
        "error_archive_interval": config.error_archive_interval,
        "selected_model": config.selected_model,
        "claude_model_alias": config.claude_model_alias,
        "models": sorted(config.models.keys()),
        "stats": {
            "requests": request_count,
            "errors": error_count,
            "error_rate": f"{min((error_count / max(request_count, 1)) * 100, 100):.1f}%"
        }
    }


@app.get("/api/check-update")
async def check_update_api():
    """检查 GitHub 是否有新版本（1 小时缓存）"""
    await _check_github_update()
    latest = _update_cache["latest_version"]
    return {
        "update_available": bool(latest) and _is_newer_version(latest, VERSION),
        "latest_version": latest or "",
        "current_version": VERSION,
    }


@app.get("/api/logs")
async def get_logs(limit: int = 100):
    """返回最近 N 行日志"""
    log_path = config.log_file
    if not os.path.exists(log_path):
        return {"lines": [], "total": 0}
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        tail = lines[-limit:]
        return {"lines": tail, "total": len(lines)}
    except Exception as e:
        return {"lines": [f"读取日志失败: {e}"], "total": 0}


@app.put("/api/config")
async def update_config_api(updates: dict):
    """更新配置并 reload"""
    mapping = {
        "opencode_base_url": "OPENCODE_BASE_URL",
        "opencode_api_key": "OPENCODE_API_KEY",
        "router_host": "ROUTER_HOST",
        "router_port": "ROUTER_PORT",
        "master_key": "ROUTER_MASTER_KEY",
        "max_retry": "MAX_RETRY",
        "retry_delay": "RETRY_DELAY",
        "log_level": "LOG_LEVEL",
        "disable_thinking": "DISABLE_THINKING",
        "detailed_logging": "DETAILED_LOGGING",
        "error_archive_dir": "ERROR_ARCHIVE_DIR",
        "error_archive_interval": "ERROR_ARCHIVE_INTERVAL",
        "selected_model": "SELECTED_MODEL",
        "claude_model_alias": "CLAUDE_MODEL_ALIAS",
    }

    env_updates = {}
    for api_key, env_key in mapping.items():
        if api_key not in updates:
            continue
        val = updates[api_key]
        # 跳过未修改的脱敏密钥
        if api_key in ("opencode_api_key", "master_key") and isinstance(val, str) and "***" in val:
            continue
        if val is not None and val != "":
            env_updates[env_key] = str(val).strip()

    if env_updates:
        update_env_file(**env_updates)

    # 同步到 Claude Code 配置：模型名、BASE_URL、Auth Token
    # 同步到 Claude Code 配置（仅当影响 CC 的字段变更时）
    cc_env_keys = {"SELECTED_MODEL", "CLAUDE_MODEL_ALIAS", "ROUTER_HOST", "ROUTER_PORT", "ROUTER_MASTER_KEY"}
    if env_updates and any(k in env_updates for k in cc_env_keys):
        try:
            sync_claude_settings()
        except Exception as e:
            logger.warning(f"[Config] 同步 Claude Code 配置失败: {e}")

    return {"status": "ok", "updated": list(env_updates.keys())}


@app.post("/api/open-folder")
async def open_folder(data: dict = Body(...)):
    """在资源管理器中打开指定文件夹"""
    folder_type = data.get("type", "log")
    if folder_type == "archive":
        folder = config.error_archive_dir
    else:
        folder = os.path.dirname(config.log_file)
    try:
        os.startfile(folder)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@app.get("/api/custom-models")
async def get_custom_models():
    return load_custom_models()


@app.put("/api/custom-models")
async def save_custom_models_api(models: list = Body(...)):
    save_custom_models(models)
    config.models = merge_models(DEFAULT_MODELS, load_custom_models())
    return {"status": "ok", "count": len(models)}


@app.post("/api/config/restore")
async def restore_claude_config():
    """从备份恢复原始 Claude Code 配置"""
    backup_dir = os.path.join(get_base_dir(), "data", "claude-backups")
    if not os.path.exists(backup_dir):
        raise HTTPException(status_code=404, detail="无备份文件，无法恢复")

    try:
        backups = sorted([f for f in os.listdir(backup_dir) if f.endswith(".bak")])
    except OSError:
        backups = []
    if not backups:
        raise HTTPException(status_code=404, detail="无备份文件，无法恢复")

    backup_path = os.path.join(backup_dir, backups[-1])  # 取最新的备份
    target_path = config.claude_settings_path

    if not target_path:
        raise HTTPException(status_code=400, detail="Claude Code 配置文件路径未配置")

    try:
        with open(backup_path, "r", encoding="utf-8") as f:
            backup_content = f.read()

        # 检查是否与当前配置相同
        if os.path.exists(target_path):
            with open(target_path, "r", encoding="utf-8") as f:
                current = f.read()
            if current == backup_content:
                return {"status": "ok", "message": "当前配置已与备份一致，无需恢复"}

        with open(target_path, "w", encoding="utf-8") as f:
            f.write(backup_content)

        # 清除选中模型，使 CC 恢复为未选中状态
        config.selected_model = ""
        config.reload()

        logger.info(f"[Restore] 已从 {backup_path} 恢复原始配置")
        return {"status": "ok", "message": "已恢复原始配置"}
    except Exception as e:
        logger.error(f"[Restore] 恢复失败: {e}")
        raise HTTPException(status_code=500, detail=f"恢复失败: {e}")


@app.get("/", include_in_schema=False)
async def serve_index():
    """配置管理页面"""
    from starlette.responses import FileResponse
    return FileResponse("static/index.html")


# ============ 启动 ============
def main():
    """cc2go CLI entry point: start the FastAPI server"""
    config.models = merge_models(DEFAULT_MODELS, load_custom_models())
    print()
    print("=" * 60)
    print(f"  cc2go v{VERSION}  --  Claude Code -> OpenCode Go")
    print("=" * 60)
    print(f"  监听: http://{config.router_host}:{config.router_port}")
    print(f"  API:  {config.opencode_base_url}")
    print("-" * 60)
    print("  模型:")
    for i, name in enumerate(list(config.models.keys())):
        print(f"    - {name}")
    print("=" * 60)
    print()

    uvicorn.run(app, host=config.router_host, port=config.router_port, log_level=config.log_level.lower())


if __name__ == "__main__":
    main()