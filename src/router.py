"""
cc2go - Claude Code → OpenCode Go 格式适配器
Claude Code (Anthropic) -> OpenAI 格式 -> OpenCode Go
支持多轮对话中的工具调用循环
"""

import os
import sys
import re
import json
import time
import logging
import asyncio
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, PlainTextResponse
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

load_dotenv()


def get_base_dir():
    """项目根目录，兼容 PyInstaller onefile 打包"""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
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


VERSION = "0.6.0"

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
        self.router_port = int(os.getenv("ROUTER_PORT", "4000"))
        self.router_host = os.getenv("ROUTER_HOST", "0.0.0.0")
        self.master_key = os.getenv("ROUTER_MASTER_KEY", "sk-litellm-local")
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

config = Config()
update_archive_limiter(config.error_archive_interval)


def setup_logger():
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("llm_router")
    logger.setLevel(getattr(logging, config.log_level.upper()))
    file_handler = RotatingFileHandler(config.log_file, encoding="utf-8", maxBytes=5*1024*1024, backupCount=3)
    console_handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger

logger = setup_logger()

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

def save_stats(force=False):
    global _stats_dirty
    with _stats_lock:
        _stats_dirty += 1
        if not force and _stats_dirty < 10:
            return
        try:
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

            # 添加 tool 结果（必须在用户文本之前，满足 OpenAI tool 消息紧跟 tool_calls 的要求）
            openai_messages.extend(tool_results)

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


def convert_tools(tools: List[Dict]) -> List[Dict]:
    """将 Claude 格式的工具转换为 OpenAI 格式"""
    if not tools:
        return []

    openai_tools = []
    for tool in tools:
        # Claude 格式可能是 function.name 或者 直接是 name
        func = tool.get("function", {})
        name = func.get("name", "") or tool.get("name", "")
        name = name.strip()

        if not name:
            logger.warning(f"[Tool] Skipping tool with empty name: {tool}")
            continue

        # 获取描述和参数
        description = func.get("description", "") or tool.get("description", "") or ""
        parameters = func.get("parameters", {}) or tool.get("input_schema", {}) or {"type": "object", "properties": {}}

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
    global request_count, error_count
    start_time = time.time()
    model_name = None

    try:
        body = await request.json()

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

        logger.info(f"[Request] model={model_name}, messages={len(messages)}, tools={len(tools) if tools else 0}")

        # 获取模型配置
        model_config = config.models.get(model_name)
        if model_config:
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

        # 清除 output_config（某些 API 拒绝 empty tools + json_schema）
        if not tools and body.get("output_config", {}).get("format", {}).get("type") == "json_schema":
            del body["output_config"]

        # 自定义模型透传：仅对 Anthropic 格式端点保留直传，OpenAI 格式走正常转换路径
        if custom_base and custom_ep == "/v1/messages":
            body["model"] = custom_upstream_model
            full_url = custom_base + custom_ep
            logger.info(f"[Passthrough] model={custom_upstream_model}, url={full_url}")
            response = await call_opencode("", body, api_key=custom_key, full_url=full_url)
            raw_text = response.text
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
            body["messages"] = strip_thinking_from_messages(body.get("messages", []))
            if config.detailed_logging:
                logger.debug(f"[MiniMax Payload] {json.dumps(body, ensure_ascii=False)[:1000]}")
            logger.debug(f"[Payload] Direct forward to {endpoint} with thinking disabled")
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

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    config.reload()


def sync_claude_settings():
    """同步路由器配置到 Claude Code 的 settings.json（模型名、Base URL、Auth Token）"""
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


@app.get("/api/config")
async def get_config_api():
    """返回当前配置（密钥脱敏）"""
    return {
        "version": VERSION,
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


@app.get("/", include_in_schema=False)
async def admin_page():
    """配置管理页面"""
    return HTMLResponse(content=ADMIN_HTML)


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>cc2go 管理</title>
<link rel="icon" type="image/x-icon" href="/static/favicon.ico">
<link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','SF Pro Display',system-ui,sans-serif;background:#f5f5f7;color:#1d1d1f;padding:32px 24px;font-size:15px;-webkit-font-smoothing:antialiased}
.container{max-width:680px;margin:0 auto}
.header{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:36px}
.header-left{display:flex;align-items:center;gap:14px}
.header-left img{width:32px;height:32px}
.header-left h1{font-size:24px;font-weight:600;letter-spacing:-.3px}
.header-right{text-align:right;font-size:13px;color:#86868b}
.header-right select{font-size:12px;padding:4px 8px;border:1px solid #d2d2d7;border-radius:6px;background:transparent;color:inherit;cursor:pointer}
.header-stats{display:flex;gap:16px;margin-top:6px;font-size:12px;color:#86868b}
.header-stats strong{color:#1d1d1f;font-weight:600}
.card{background:#fff;border-radius:16px;padding:20px 24px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04)}
.card h2{font-size:15px;font-weight:600;margin-bottom:14px;letter-spacing:-.2px;color:#1d1d1f}
.model-list{display:flex;flex-wrap:wrap;gap:8px}
.model-tag{background:#f0f0f5;padding:7px 16px;border-radius:10px;font-size:14px;font-weight:500;color:#515154;cursor:pointer;transition:all .2s;border:1.5px solid transparent;line-height:1.4;display:inline-flex;align-items:center;gap:4px}
.model-tag.custom{padding-right:12px}
.model-tag:hover{background:#e5e5ea;border-color:#d2d2d7}
.model-tag.selected{background:#0071e3;color:#fff;border-color:#0071e3}
.model-tag.selected:hover{background:#0062c4}
.model-tag.custom{border-color:#34c759;background:transparent}
.model-tag.custom.selected{background:#0071e3;border-color:#34c759;color:#fff}
.tag-action{opacity:.7;transition:opacity .2s}
.tag-action:hover{opacity:1;text-decoration:underline}
.btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.btn{flex:1;padding:8px 16px;border:none;border-radius:10px;font-size:13px;font-weight:500;cursor:pointer;transition:all .2s;line-height:1.4;min-width:0;white-space:nowrap}
.btn-primary{background:#0071e3;color:#fff}
.btn-primary:hover{opacity:.88}
.btn-secondary{background:#e8e8ed;color:#1d1d1f}
.btn-secondary:hover{background:#dddde3}
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.35);z-index:100;display:none;align-items:center;justify-content:center;overflow-y:auto;backdrop-filter:blur(4px)}
.modal-overlay.open{display:flex}
.modal{background:#fff;border-radius:18px;padding:28px;width:90%;max-width:520px;max-height:95vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.15);animation:modalIn .2s ease}
@keyframes modalIn{from{opacity:0;transform:scale(.95)}to{opacity:1;transform:scale(1)}}
.modal h2{font-size:17px;font-weight:600;margin-bottom:18px;letter-spacing:-.2px}
.modal .form-row{margin-bottom:14px}
.modal-actions{display:flex;gap:10px;margin-top:20px;justify-content:flex-end}
.form-group{flex:1;min-width:200px}
.form-group label{display:block;font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:4px}
.form-group input,.form-group select{width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:10px;font-size:14px;outline:none;transition:border-color .2s;box-sizing:border-box;background:#fff}
.form-group input:focus,.form-group select:focus{border-color:#0071e3;box-shadow:0 0 0 3px rgba(0,113,227,.12)}
.form-group input[type=checkbox]{width:auto;margin-right:6px}
.checkbox-row{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block;vertical-align:middle}
.status-dot.ok{background:#30d158}
.status-dot.err{background:#ff3b30}
.toast{position:fixed;bottom:28px;left:50%;transform:translateX(-50%);background:#1d1d1f;color:#fff;padding:12px 24px;border-radius:12px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none;z-index:200;box-shadow:0 8px 30px rgba(0,0,0,.2)}
.toast.show{opacity:1}
@media(prefers-color-scheme:dark){
 body{background:#1c1c1e;color:#f5f5f7}
 .card{background:#2c2c2e;box-shadow:0 1px 3px rgba(0,0,0,.2)}
 .card h2,.header-stats strong{color:#f5f5f7}
 .form-group label{color:#a1a1a6}
 .form-group input,.form-group select{background:#3a3a3c;border-color:#48484a;color:#f5f5f7}
 .form-group input:focus,.form-group select:focus{border-color:#0a84ff;box-shadow:0 0 0 3px rgba(10,132,255,.15)}
 .model-tag{background:#3a3a3c;color:#d1d1d6}
 .model-tag:hover{background:#444446}
 .model-tag.selected{background:#0a84ff;border-color:#0a84ff}
 .model-tag.custom{background:transparent;border-color:#34c759}
 .model-tag.custom.selected{background:#0a84ff;border-color:#34c759}
 #logViewer2{background:#1c1c1e!important;color:#d1d1d6!important}
 .btn-secondary{background:#3a3a3c;color:#f5f5f7}
 .btn-secondary:hover{background:#444446}
 .modal{background:#2c2c2e}
 .toast{background:#f5f5f7;color:#1d1d1f}
 .modal-overlay{background:rgba(0,0,0,.6)}
}
</style>
</head>
<body>
<div class="container">
<div class="header">
<div class="header-left">
<img src="/static/favicon-32x32.png" style="width:28px;height:28px">
<h1>cc2go</h1>
</div>
<div class="header-right">
<div>
<select id="langSwitch" onchange="setLang(this.value)">
<option value="zh">中文</option>
<option value="en">English</option>
</select>
<span class="status-dot" id="statusDot" style="margin-left:8px"></span>
<span id="statusText" style="margin-left:4px">...</span>
</div>
<div class="header-stats">
<span><span data-i18n="requests">请求</span>: <strong id="statRequests">-</strong></span>
<span><span data-i18n="errors">错误</span>: <strong id="statErrors">-</strong></span>
<span><span data-i18n="errRate">错误率</span>: <strong id="statErrorRate">-</strong></span>
</div>
</div>
</div>

<div class="card">
<div style="margin-top:4px;font-size:12px;color:#86868b;font-weight:500" data-i18n="presetModels">预置模型</div>
<div class="model-list" id="presetModelList" style="margin-top:4px"></div>
<div style="margin-top:10px;font-size:12px;color:#86868b;font-weight:500" data-i18n="customModels">自定义模型</div>
<div class="model-list" id="customModelList2" style="margin-top:4px"></div>
</div>

<div class="btn-row" style="margin-bottom:6px">
<button class="btn btn-secondary" onclick="clearCustomModalFields();openModal('customModal')">➕ <span data-i18n="addModel">新增模型</span></button>
<button class="btn btn-secondary" onclick="editSelectedCustom()">✎ <span data-i18n="editModel">编辑模型</span></button>
</div>
<div class="btn-row">
<button class="btn btn-secondary" onclick="openModal('connModal')">🔗 <span data-i18n="opencode">连接</span></button>
<button class="btn btn-secondary" onclick="openModal('serviceModal')">⚙️ <span data-i18n="service">服务</span></button>
<button class="btn btn-secondary" onclick="openModal('advancedModal')">🔧 <span data-i18n="advanced">高级</span></button>
<button class="btn btn-secondary" onclick="openModal('logsModal')">📋 <span data-i18n="logs">日志</span></button>
</div>

<!-- Modals -->
<div class="modal-overlay" id="connModal">
<div class="modal"><h2 data-i18n="opencode">OpenCode Go 连接</h2>
<div class="form-row"><div class="form-group"><label>Base URL</label><input id="baseUrl" placeholder="https://opencode.ai/zen/go"></div></div>
<div class="form-row"><div class="form-group"><label>API Key</label>
<div style="display:flex;align-items:center;gap:4px">
<input id="apiKey" type="password" placeholder="sk-..." style="flex:1">
<a href="https://opencode.ai/zh/go" target="_blank" style="font-size:11px;color:#0071e3;text-decoration:none;white-space:nowrap" data-i18n="getKey">获取 Key</a>
</div>
</div></div>
<div class="modal-actions"><button class="btn btn-secondary" style="flex:none;padding:8px 20px" onclick="closeModal('connModal')" data-i18n="cancel">取消</button><button class="btn btn-primary" style="flex:none;padding:8px 20px" onclick="saveConnModal()" data-i18n="save">保存</button></div>
</div></div>
<div class="modal-overlay" id="serviceModal">
<div class="modal"><h2 data-i18n="service">服务配置</h2>
<div style="font-size:13px;color:#86868b;margin-bottom:12px" data-i18n="serviceDesc">修改后自动同步到 Claude Code 的连接配置</div>
<div class="form-row"><div class="form-group" data-i18n-label="host"><label>监听主机</label><input id="host2" placeholder="0.0.0.0"></div></div>
<div class="form-row"><div class="form-group" data-i18n-label="port"><label>端口</label><input id="port2" type="number" placeholder="4000"></div></div>
<div class="form-row"><div class="form-group" data-i18n-label="masterKey"><label>Master Key</label><input id="masterKey2" type="password" placeholder="sk-..."></div></div>
<div class="modal-actions"><button class="btn btn-secondary" style="flex:none;padding:6px 16px" onclick="closeModal('serviceModal')" data-i18n="cancel">取消</button><button class="btn btn-primary" style="flex:none;padding:6px 16px" onclick="saveServiceModal()" data-i18n="save">保存</button></div>
</div></div>

<div class="modal-overlay" id="advancedModal">
<div class="modal"><h2 data-i18n="advanced">高级选项</h2>
<div style="font-size:13px;color:#86868b;margin-bottom:12px" data-i18n="advancedDesc">重试、思考模式等高级设置</div>
<div class="form-row"><div class="form-group" data-i18n-label="maxRetry"><label>最大重试次数</label><input id="maxRetry2" type="number" min="0" max="10"></div></div>
<div class="form-row"><div class="form-group" data-i18n-label="retryDelay"><label>重试间隔（秒）</label><input id="retryDelay2" type="number" step="0.5" min="0"></div></div>
<div class="form-row"><div class="checkbox-row"><input id="disableThinking2" type="checkbox"><label for="disableThinking2" data-i18n="disableThinking">禁用思考模式</label></div></div>
<div class="form-row"><div class="form-group"><label data-i18n="alias">CC 模型名</label><input id="claudeAlias2" data-i18n="aliasPlaceholder" placeholder="留空=使用实际模型名" style="width:100%;padding:8px 12px;border:1px solid #d2d2d7;border-radius:8px;font-size:14px;box-sizing:border-box"></div></div>
<div style="font-size:12px;color:#86868b;margin-top:-6px" data-i18n="aliasDesc">设成视觉模型名（如 claude-sonnet-4-20250514）可让 CC 放开图片发送</div>
<div class="modal-actions"><button class="btn btn-secondary" style="flex:none;padding:6px 16px" onclick="closeModal('advancedModal')" data-i18n="cancel">取消</button><button class="btn btn-primary" style="flex:none;padding:6px 16px" onclick="saveAdvancedModal()" data-i18n="save">保存</button></div>
</div></div>


<div class="modal-overlay" id="customModal">
<div class="modal" style="padding:28px"><h2 data-i18n="customModels" style="margin-bottom:18px">自定义模型</h2>
<div style="display:flex;flex-direction:column;gap:10px">
<label data-i18n="modelDisplayName" style="font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:-6px">显示名</label>
<input id="newModelDisplayName" data-i18n="modelDisplayPlaceholder" placeholder="显示名" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
<input id="newModelName" type="hidden">
<label data-i18n="modelUrl" style="font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:-6px">API 地址</label>
<input id="newModelUrl" data-i18n="modelUrlPlaceholder" placeholder="Base URL (https://...)" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
<label data-i18n="modelFormat" style="font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:-6px">格式</label>
<select id="newModelFormat" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;background:#fff;box-sizing:border-box">
<option value="openai">OpenAI (/v1/chat/completions)</option>
<option value="anthropic">Anthropic (/v1/messages)</option>
</select>
<label data-i18n="modelUpstream" style="font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:-6px">上游模型名</label>
<input id="newModelUpstream" data-i18n="modelUpstreamPlaceholder" placeholder="上游模型名（留空使用 ID）" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
<div style="font-size:11px;color:#86868b;margin-top:-8px" data-i18n="modelUpstreamDesc">发送给上游的实际模型名，如 deepseek-v4-flash</div>
<label data-i18n="modelApiKey" style="font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:-6px">API Key</label>
<input id="newModelApiKey" type="password" data-i18n="modelKeyPlaceholder" placeholder="API Key（留空使用全局）" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
</div>
<div class="modal-actions" style="margin-top:20px"><button class="btn btn-secondary" style="flex:none;padding:8px 20px" onclick="closeModal('customModal');window._editingIdx=undefined" data-i18n="cancel">取消</button><button class="btn btn-primary" style="flex:none;padding:8px 20px" onclick="saveCustomModal()" data-i18n="save">保存</button></div>
</div></div>

<div class="modal-overlay" id="logsModal" onclick="if(event.target===this)closeModal('logsModal')">
<div class="modal" style="max-width:520px;max-height:95vh"><h2 data-i18n="logs">运行日志</h2>
<div class="form-row"><div class="form-group" data-i18n-label="logLevel"><label>日志级别</label><select id="logLevelLogs" onchange="saveLogSettings()"><option>DEBUG</option><option selected>INFO</option><option>WARNING</option><option>ERROR</option></select></div></div>
<div class="form-row"><div class="checkbox-row"><input id="detailedLoggingLogs" type="checkbox" onchange="saveLogSettings()"><label for="detailedLoggingLogs" data-i18n="detailedLogging">记录详细日志</label></div></div>
<div style="margin:12px 0;border-top:1px solid #e8e8ed"></div>
<div class="form-row"><div class="form-group" data-i18n-label="archiveDir"><label>错误归档目录</label><input id="archiveDir" type="text" style="width:100%;padding:8px 12px;border:1px solid #d2d2d7;border-radius:8px;font-size:13px;box-sizing:border-box"><div style="font-size:11px;color:#86868b;margin-top:2px"><span data-i18n="archiveDirDesc">留空使用默认位置</span></div></div></div>
<div class="form-row"><div class="form-group" data-i18n-label="archiveInterval"><label>归档间隔（秒）</label><input id="archiveInterval" type="number" min="1" max="3600" style="width:100%;padding:8px 12px;border:1px solid #d2d2d7;border-radius:8px;font-size:13px;box-sizing:border-box"><div style="font-size:11px;color:#86868b;margin-top:2px"><span data-i18n="archiveIntervalDesc">同类型错误至少间隔指定秒数才再次归档</span></div></div></div>
<div style="display:flex;gap:8px;margin-top:4px;flex-wrap:wrap">
<button class="btn btn-secondary" style="padding:6px 14px;font-size:12px;flex:none" onclick="openFolder('log')" data-i18n="openLogFolder">打开日志文件夹</button>
<button class="btn btn-secondary" style="padding:6px 14px;font-size:12px;flex:none" onclick="openFolder('archive')" data-i18n="openArchiveFolder">打开错误归档文件夹</button>
</div>
</div></div>

<div class="toast" id="toast"></div>

<div class="modal-overlay" id="confirmModal">
<div class="modal" style="max-width:360px;text-align:center"><h2 id="confirmTitle" style="font-size:16px;margin-bottom:12px">确认</h2>
<p id="confirmText" style="font-size:14px;color:#515154;margin-bottom:20px"></p>
<div class="modal-actions" style="justify-content:center">
<button class="btn btn-secondary" style="flex:none;padding:8px 24px" onclick="closeConfirm()" data-i18n="cancel">取消</button>
<button class="btn btn-danger" style="flex:none;padding:8px 24px" id="confirmOkBtn" data-i18n="confirm">确定</button>
</div>
</div></div>

<div style="text-align:center;padding:16px 0 8px;font-size:12px;color:#a1a1a6">
<span id="versionDisplay"></span>
<a href="https://github.com/lzg14/cc2go" target="_blank" style="color:#a1a1a6;text-decoration:none">github.com/lzg14/cc2go</a>
</div>

<script>
const I18N = {
  zh: {
    opencode: "OpenCode Go 连接",
    service: "服务配置",
    advanced: "高级选项",
    alias: "CC 模型名",
    model: "当前模型",
    stats: "统计",
    logs: "日志管理",
    save: "保存配置",
    refresh: "刷新",
    reload: "重新加载",
    nosel: "未选择",
    running: "运行中",
    connfail: "无法连接",
    saved: "已保存并重新加载",
    savefail: "保存失败",
    loaded: "配置已加载",
    loadfail: "加载失败",
    switched: "已切换到",
    reloaded: "已重新加载",
    reloadfail: "重载失败",
    archiveDir: "错误归档目录",
    archiveDirDesc: "留空使用默认位置",
    archiveInterval: "归档间隔（秒）",
    archiveIntervalDesc: "同类型错误至少间隔指定秒数才再次归档",
    openLogFolder: "打开日志文件夹",
    openArchiveFolder: "打开错误归档文件夹",
    openfail: "打开失败",
    aliasDesc: "留空显示实际模型名。设成视觉模型名（如 claude-sonnet-4-20250514）可让 CC 放开图片发送",
    aliasPlaceholder: "留空=使用实际模型名",
    customModels: "自定义模型",
    presetModels: "预置模型",
    add: "添加",
    noCustomModels: "暂无自定义模型",
    customAdded: "已添加自定义模型",
    customDeleted: "已删除自定义模型",
    addModel: "新增模型",
    editModel: "编辑模型",
    cancel: "取消",
    confirm: "确定",
    close: "关闭",
    host: "监听主机",
    port: "端口",
    masterKey: "Master Key（客户端连接密钥）",
    requests: "请求",
    errors: "错误",
    errRate: "错误率",
    serviceDesc: "修改后自动同步到 Claude Code 的连接配置",
    advancedDesc: "重试、思考模式等高级设置",
    maxRetry: "最大重试次数",
    retryDelay: "重试间隔（秒）",
    logLevel: "日志级别",
    disableThinking: "禁用思考模式",
    detailedLogging: "记录详细日志",
    modelDisplayName: "显示名",
    modelUrl: "API 地址",
    modelFormat: "格式",
    modelUpstream: "上游模型名",
    modelUpstreamDesc: "发送给上游的实际模型名，如 deepseek-v4-flash",
    modelApiKey: "API Key",
    modelDisplayPlaceholder: "显示名",
    modelUrlPlaceholder: "API 地址 (https://...)",
    modelUpstreamPlaceholder: "上游模型名（留空使用 ID）",
    modelKeyPlaceholder: "API Key（留空使用全局）",
    getKey: "获取 Key",
  },
  en: {
    opencode: "Go Connection",
    service: "Service Config",
    advanced: "Advanced",
    alias: "CC Model Name",
    model: "Current Model",
    stats: "Stats",
    logs: "Logs",
    save: "Save Config",
    refresh: "Refresh",
    reload: "Reload",
    nosel: "Not selected",
    running: "Running",
    connfail: "Connection failed",
    saved: "Saved & reloaded",
    savefail: "Save failed",
    loaded: "Config loaded",
    loadfail: "Load failed",
    switched: "Switched to",
    reloaded: "Reloaded",
    reloadfail: "Reload failed",
    archiveDir: "Error Archive Dir",
    archiveDirDesc: "Leave empty for default location",
    archiveInterval: "Archive Interval (s)",
    archiveIntervalDesc: "Minimum seconds between archives of same error type",
    openLogFolder: "Open Log Folder",
    openArchiveFolder: "Open Archive Folder",
    openfail: "Failed to open",
    aliasDesc: "Leave empty to show actual model name. Set to a vision model name (e.g. claude-sonnet-4-20250514) to enable image input in CC.",
    aliasPlaceholder: "Leave empty = use actual model",
    customModels: "Custom Models",
    presetModels: "Preset Models",
    add: "Add",
    noCustomModels: "No custom models",
    customAdded: "Custom model added",
    customDeleted: "Custom model removed",
    addModel: "Add Model",
    editModel: "Edit Model",
    cancel: "Cancel",
    confirm: "OK",
    close: "Close",
    host: "Host",
    port: "Port",
    masterKey: "Master Key (client auth)",
    requests: "Requests",
    errors: "Errors",
    errRate: "Error rate",
    serviceDesc: "Changes sync to Claude Code automatically",
    advancedDesc: "Retry, thinking mode, etc.",
    maxRetry: "Max retries",
    retryDelay: "Retry delay (s)",
    logLevel: "Log level",
    disableThinking: "Disable thinking mode",
    detailedLogging: "Detailed request logging",
    modelDisplayName: "Display Name",
    modelUrl: "API URL",
    modelFormat: "Format",
    modelUpstream: "Upstream Model",
    modelUpstreamDesc: "Actual model name sent to upstream, e.g. deepseek-v4-flash",
    modelApiKey: "API Key",
    modelDisplayPlaceholder: "Display name",
    modelUrlPlaceholder: "API URL (https://...)",
    modelUpstreamPlaceholder: "Upstream model name (leave empty = use ID)",
    modelKeyPlaceholder: "API Key (leave empty = use global)",
    getKey: "Get Key",
  },
};
function t(key) { return (I18N[_lang]||I18N.zh)[key]||key; }
let _lang = localStorage.getItem('cc2go_lang') || 'zh';
document.getElementById('langSwitch').value = _lang;
function setLang(l) { _lang = l; localStorage.setItem('cc2go_lang', l); applyLang(); }
function applyLang() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    const tag = el.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') el.placeholder = t(key);
    else if (tag === 'SELECT') { if (el.options[0]) el.options[0].text = t(key); }
    else el.textContent = t(key);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => el.title = t(el.dataset.i18nTitle));
  document.querySelectorAll('[data-i18n-label]').forEach(el => {
    const label = el.querySelector('label');
    if (label) label.textContent = t(el.dataset.i18nLabel);
  });
}
async function api(method, path, body) {
  const opts = {method, headers:{'Content-Type':'application/json'}};
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json();
}
function toast(msg, ok=true) {
  const t = document.getElementById('toast');
  t.textContent = (ok?'✓ ':'✗ ')+msg;
  t.style.background = ok?'#1d1d1f':'#ff3b30';
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2500);
}
async function load() {
  try {
    const cfg = await api('GET','/api/config');
    document.getElementById('baseUrl').value = cfg.opencode_base_url||'';
    document.getElementById('apiKey').value = cfg.opencode_api_key||'';
    syncToModals(cfg);
    const sel = cfg.selected_model||'';
    const customIds = customModels.map(m => m.id);
    // 预置模型：只能切换
    const pEl = document.getElementById('presetModelList');
    if (pEl && cfg.models) {
      pEl.innerHTML = cfg.models.filter(m => !customIds.includes(m)).map(m =>
        '<span class="model-tag'+(m===sel?' selected':'')+'" data-model="'+m+'" onclick="selectModel(\''+m.replace(/'/g,"\\'")+'\')">'+m+'</span>'
      ).join('');
    }
    // 自定义模型：切换、编辑、删除
    const cEl = document.getElementById('customModelList2');
    if (cEl) {
      if (!customModels.length) {
        cEl.innerHTML = '<span style="color:#86868b;font-size:13px" data-i18n="noCustomModels">暂无自定义模型</span>';
      } else {
        cEl.innerHTML = customModels.map(m => {
          const label = m.display_name || m.id;
          return '<span class="model-tag'+(m.id===sel?' selected':'')+' custom" data-model="'+m.id+'" onclick="selectModel(\''+m.id+'\')" style="position:relative;padding-right:18px">' +
            label+' <sup style="font-size:10px;opacity:.7">C</sup>' +
            '<span onclick="event.stopPropagation();deleteCustomModelById(\''+m.id+'\')" style="cursor:pointer;color:#ff3b30;font-size:12px;position:absolute;top:2px;right:3px" title="Delete">✕</span></span>';
        }).join('');
      }
    }
    const vEl = document.getElementById('versionDisplay');
    if (vEl && cfg.version) vEl.textContent = 'v' + cfg.version + ' · ';
    if (cfg.stats) {
      document.getElementById('statRequests').textContent = cfg.stats.requests;
      document.getElementById('statErrors').textContent = cfg.stats.errors;
      document.getElementById('statErrorRate').textContent = cfg.stats.error_rate;
    }
    const dot = document.getElementById('statusDot');
    dot.className = 'status-dot ok';
    document.getElementById('statusText').textContent = t('running');
    toast(t('loaded'));
  } catch(e) {
    const dot = document.getElementById('statusDot');
    dot.className = 'status-dot err';
    document.getElementById('statusText').textContent = t('connfail');
    toast(t('loadfail')+': '+e.message, false);
  }
}
let customModels = [];
async function loadCustomModels() {
  try {
    customModels = await api('GET','/api/custom-models');
  } catch(e) {}
}
function clearCustomModalFields() {
  document.getElementById('newModelName').value = '';
  document.getElementById('newModelDisplayName').value = '';
  document.getElementById('newModelUrl').value = '';
  document.getElementById('newModelUpstream').value = '';
  document.getElementById('newModelApiKey').value = '';
  window._editingIdx = undefined;
}
function editCustomModel(i) {
  const m = customModels[i];
  document.getElementById('newModelName').value = m.id;
  document.getElementById('newModelDisplayName').value = m.display_name||'';
  document.getElementById('newModelUrl').value = m.base_url||m.url||'';
  document.getElementById('newModelFormat').value = (m.endpoint||'').includes('messages') ? 'anthropic' : 'openai';
  document.getElementById('newModelUpstream').value = m.model||m.upstream_model||'';
  document.getElementById('newModelApiKey').value = m.api_key||'';
  window._editingIdx = i;
}
async function deleteCustomModel(i) {
  customModels.splice(i, 1);
  await api('PUT','/api/custom-models', customModels);
  await load();
  toast(t('customDeleted'));
}
function saveConnModal() { closeModal('connModal'); save(); }
function openModal(id) {
  document.getElementById(id).classList.add('open');
  document.getElementById(id).classList.add('open');
}
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}
function syncToModals(cfg) {
  setVal('host2', cfg.router_host);
  setVal('port2', cfg.router_port);
  setVal('masterKey2', cfg.master_key);
  setVal('maxRetry2', cfg.max_retry);
  setVal('retryDelay2', cfg.retry_delay);
  setChecked('disableThinking2', cfg.disable_thinking);
  setVal('claudeAlias2', cfg.claude_model_alias);
  setVal('logLevelLogs', cfg.log_level);
  setChecked('detailedLoggingLogs', cfg.detailed_logging);
  setVal('archiveDir', cfg.error_archive_dir||'');
  setVal('archiveInterval', cfg.error_archive_interval||30);
}
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v||''; }
function setChecked(id, v) { const el = document.getElementById(id); if (el) el.checked = v!==false; }
function getVal(id) { const el = document.getElementById(id); return el ? el.value : ''; }
function getChecked(id) { const el = document.getElementById(id); return el ? el.checked : false; }
function saveServiceModal() { closeModal('serviceModal'); save(); }
function saveAdvancedModal() { closeModal('advancedModal'); save(); }
function saveAliasModal() { closeModal('aliasModal'); save(); }
function saveLogSettings() { save(['log_level','detailed_logging','error_archive_dir','error_archive_interval']); }
function openFolder(type) {
  api('POST','/api/open-folder',{type}).catch(e => toast(t('openfail')+': '+e.message, false));
}
async function save(keys) {
  const body = {};
  if (!keys) {
    body.opencode_base_url = document.getElementById('baseUrl').value;
    body.opencode_api_key = document.getElementById('apiKey').value;
    body.router_host = getVal('host2');
    body.router_port = parseInt(getVal('port2'))||4000;
    body.master_key = getVal('masterKey2');
    body.max_retry = parseInt(getVal('maxRetry2'))||3;
    body.retry_delay = parseFloat(getVal('retryDelay2'))||1;
    body.log_level = getVal('logLevel2')||'INFO';
    body.disable_thinking = getChecked('disableThinking2');
    body.detailed_logging = getChecked('detailedLogging2');
    body.claude_model_alias = getVal('claudeAlias2');
  } else {
    if (keys.includes('log_level')) body.log_level = getVal('logLevelLogs')||'INFO';
    if (keys.includes('detailed_logging')) body.detailed_logging = getChecked('detailedLoggingLogs');
    if (keys.includes('error_archive_dir')) body.error_archive_dir = getVal('archiveDir');
    if (keys.includes('error_archive_interval')) body.error_archive_interval = parseInt(getVal('archiveInterval'))||30;
  }
  try {
    const r = await api('PUT','/api/config', body);
    toast(t('saved'));
  } catch(e) {
    toast(t('savefail')+': '+e.message, false);
  }
}
async function selectModel(name) {
  await api('PUT','/api/config',{selected_model:name});
  await load();
  toast(t('switched')+': '+name);
}
function generateModelId(displayName) {
  const slug = displayName.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-').replace(/^-|-$/g, '') || 'custom';
  return slug + '-' + Date.now().toString(36).slice(-5);
}
function saveCustomModal() {
  const display = document.getElementById('newModelDisplayName').value.trim();
  const url = document.getElementById('newModelUrl').value.trim();
  const fmt = document.getElementById('newModelFormat').value;
  const upstream = document.getElementById('newModelUpstream').value.trim();
  const ak = document.getElementById('newModelApiKey').value.trim();
  const ep = fmt === 'anthropic' ? '/v1/messages' : '/v1/chat/completions';
  if (!display) return;
  const data = {display_name: display, base_url: url, endpoint: ep, model: upstream, api_key: ak};
  // 编辑已有模型
  if (window._editingIdx !== undefined && window._editingIdx < customModels.length) {
    customModels[window._editingIdx] = Object.assign({id: customModels[window._editingIdx].id}, data);
    window._editingIdx = undefined;
  } else {
    data.id = generateModelId(display);
    customModels.push(data);
  }
  api('PUT','/api/custom-models', customModels).then(() => {
    document.getElementById('newModelName').value = '';
    document.getElementById('newModelDisplayName').value = '';
    document.getElementById('newModelUrl').value = '';
    document.getElementById('newModelUpstream').value = '';
    document.getElementById('newModelApiKey').value = '';
    closeModal('customModal');
    load();
    toast(t('saved'));
  }).catch(e => toast(t('savefail')+': '+e.message, false));
}
function editCustomModelById(id) {
  const i = customModels.findIndex(m => m.id === id);
  if (i === -1) return;
  editCustomModel(i);
  openModal('customModal');
}
function deleteCustomModelById(id) {
  const i = customModels.findIndex(m => m.id === id);
  if (i === -1) return;
  showConfirm('删除自定义模型「'+(customModels[i].display_name||id)+'」？', () => {
    customModels.splice(i, 1);
    api('PUT','/api/custom-models', customModels).then(() => {
      load();
      toast(t('customDeleted'));
    });
  });
}
let _confirmCb = null;
function showConfirm(msg, cb) {
  document.getElementById('confirmText').textContent = msg;
  _confirmCb = cb;
  document.getElementById('confirmOkBtn').onclick = () => { closeConfirm(); if (_confirmCb) _confirmCb(); };
  document.getElementById('confirmModal').classList.add('open');
}
function closeConfirm() { document.getElementById('confirmModal').classList.remove('open'); }
function editSelectedCustom() {
  const sel = document.querySelector('#customModelList2 .model-tag.selected');
  if (!sel) { clearCustomModalFields(); openModal('customModal'); return; }
  const id = sel.getAttribute('data-model');
  const i = customModels.findIndex(m => m.id === id);
  if (i === -1) { clearCustomModalFields(); openModal('customModal'); return; }
  editCustomModel(i);
  openModal('customModal');
}
async function reload() {
  try {
    await api('POST','/reload');
    await load();
    toast(t('reloaded'));
  } catch(e) {
    toast(t('reloadfail')+': '+e.message, false);
  }
}
applyLang();
(async () => { await loadCustomModels(); await load(); })();

</script>
</body>
</html>"""

# ============ 启动 ============
if __name__ == "__main__":
    config.models = merge_models(DEFAULT_MODELS, load_custom_models())
    print()
    print("╔═══════════════════════════════════════════════════════════╗")
    version_str = f"cc2go v{VERSION}"
    print(f"║ {version_str.center(55)} ║")
    print("║          Claude Code → OpenCode Go 适配器               ║")
    print("╠═══════════════════════════════════════════════════════════╣")
    print(f"║  监听: http://{config.router_host}:{config.router_port}                              ║")
    print(f"║  API:  {config.opencode_base_url}           ║")
    print("╠═══════════════════════════════════════════════════════════╣")
    print("║  模型:                                                 ║")
    for i, name in enumerate(list(config.models.keys())):
        print(f"║    • {name:<22}                          ║")
    print("╚═══════════════════════════════════════════════════════════╝")
    print()

    uvicorn.run(app, host=config.router_host, port=config.router_port, log_level="info")