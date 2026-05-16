"""
cc2go - Claude Code → OpenCode Go 格式适配器
Claude Code (Anthropic) -> OpenAI 格式 -> OpenCode Go
支持多轮对话中的工具调用循环
"""

import os
import json
import time
import logging
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any

import httpx
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

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

CUSTOM_MODELS_FILE = os.path.join(os.path.dirname(__file__), "custom_models.json")

def load_custom_models():
    try:
        with open(CUSTOM_MODELS_FILE, "r") as f:
            return json.load(f)
    except:
        return []

def save_custom_models(models):
    with open(CUSTOM_MODELS_FILE, "w") as f:
        json.dump(models, f, indent=2, ensure_ascii=False)

def merge_models(upstream, custom):
    """合并上游模型和自定义模型，自定义模型优先"""
    merged = dict(upstream)
    for m in custom:
        mid = m["id"]
        merged[mid] = {"id": mid, "endpoint": "/v1/chat/completions"}
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
        self.log_file = os.getenv("LOG_FILE", "router.log")
        self.disable_thinking = os.getenv("DISABLE_THINKING", "true").lower() == "true"
        self.detailed_logging = os.getenv("DETAILED_LOGGING", "true").lower() == "true"
        self.selected_model = os.getenv("SELECTED_MODEL", "")
        self.claude_model_alias = os.getenv("CLAUDE_MODEL_ALIAS", "")
        self.claude_settings_path = os.getenv("CLAUDE_SETTINGS_PATH", os.path.expanduser("~/.claude/settings.json"))
        self.models = merge_models(DEFAULT_MODELS, load_custom_models())

    def reload(self):
        load_dotenv(override=True)
        self.__init__()
        logger.setLevel(getattr(logging, config.log_level.upper()))

config = Config()

# ============ 日志 ============
def setup_logger():
    from logging.handlers import RotatingFileHandler
    logger = logging.getLogger("llm_router")
    logger.setLevel(getattr(logging, config.log_level.upper()))
    file_handler = RotatingFileHandler(config.log_file, encoding="utf-8", maxBytes=5*1024*1024, backupCount=3)
    console_handler = logging.StreamHandler()
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

try:
    import os as _os
    _sd = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "static")
    if _os.path.exists(_sd):
        app.mount("/static", StaticFiles(directory=_sd), name="static")
except:
    pass

# 请求统计（持久化到文件）
STATS_FILE = os.path.join(os.path.dirname(__file__), "stats.json")
def load_stats():
    try:
        with open(STATS_FILE, "r") as f:
            return json.load(f)
    except:
        return {"requests": 0, "errors": 0}
def save_stats():
    try:
        with open(STATS_FILE, "w") as f:
            json.dump({"requests": request_count, "errors": error_count}, f)
    except:
        pass
stats = load_stats()
request_count = stats["requests"]
error_count = stats["errors"]


def strip_system_reminder(text: str) -> str:
    """移除用户消息中的 <system-reminder> 块（Claude Code 注入的技能提示），防止触发上游内容过滤"""
    import re
    return re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL).strip()


def extract_reasoning_text(text: str) -> tuple:
    """将 [思考过程]/[思考] 前缀文本分离为 (实际内容, reasoning_content)。
    返回 (cleaned_text, reasoning)，如果无前缀则 reasoning 为空字符串。"""
    cleaned = text
    reasoning = ""
    for prefix in ("[思考过程]", "[思考]"):
        if cleaned.startswith(prefix):
            rest = cleaned[len(prefix):].strip()
            if "\n" in rest:
                reasoning, cleaned = rest.split("\n", 1)
                reasoning = reasoning.strip()
                cleaned = cleaned.strip()
            else:
                reasoning = rest
                cleaned = ""
            break
    return cleaned, reasoning


def convert_anthropic_messages_to_openai(messages: List[Dict]) -> List[Dict]:
    """
    将 Claude 格式的消息转换为 OpenAI 格式
    处理 tool_use 和 tool_result
    """
    openai_messages = []

    for idx, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # 处理 Claude 的 content 数组结构
        if isinstance(content, list):
            content_items = []  # [{type, text/url}, ...]
            has_image = False
            tool_calls_list = []
            tool_results = []

            reasoning_extra = ""
            for item in content:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("type", "")

                if item_type == "text":
                    t = item.get("text", "")
                    t = strip_system_reminder(t)
                    if role == "assistant":
                        t, r = extract_reasoning_text(t)
                        if r:
                            reasoning_extra = r
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
                    tool_id = tool_data.get("id", f"tc_{idx}_{len(tool_calls_list)}")
                    tool_calls_list.append({
                        "id": tool_id,
                        "type": "function",
                        "function": {
                            "name": tool_data.get("name", ""),
                            "arguments": json.dumps(tool_data.get("input", {}), ensure_ascii=False)
                        }
                    })

                elif item_type == "tool_result":
                    tool_data = item.get("tool_result") or item
                    result_content = tool_data.get("content", "")

                    if isinstance(result_content, list):
                        text_parts_result = []
                        for part in result_content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                text_parts_result.append(part.get("text", ""))
                        result_content = "\n".join(text_parts_result)

                    tool_use_id = tool_data.get("tool_use_id", f"tc_{idx}_{len(tool_results)}")
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tool_use_id,
                        "content": str(result_content) if result_content else ""
                    })

            # 合并 content_items 和 tool_calls 到一条消息
            if content_items or tool_calls_list:
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
                if reasoning_extra:
                    msg_dict["reasoning_content"] = reasoning_extra
                openai_messages.append(msg_dict)

            # 添加 tool 结果
            openai_messages.extend(tool_results)

        elif content:
            c = strip_system_reminder(content)
            if role == "assistant":
                c, reasoning_extra = extract_reasoning_text(c)
            else:
                reasoning_extra = ""
            msg = {"role": role, "content": c}
            if reasoning_extra:
                msg["reasoning_content"] = reasoning_extra
            openai_messages.append(msg)

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
            content_items.append({
                "type": "tool_use",
                "id": tc.get("id", f"tc_{int(time.time() * 1000)}"),
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
    """调用 API，带重试"""
    url = full_url or f"{base_url or config.opencode_base_url}{endpoint}"
    key = api_key or config.opencode_api_key
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "x-api-key": key
    }

    for attempt in range(config.max_retry):
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code < 500:
                    return response
                logger.warning(f"Attempt {attempt + 1} failed: {response.status_code}")
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} error: {e}")

        if attempt < config.max_retry - 1:
            await asyncio.sleep(config.retry_delay * (attempt + 1))

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
        custom_full = None
        for cm in load_custom_models():
            if cm["id"] == model_name:
                if cm.get("base_url"):
                    custom_base = cm["base_url"]
                elif cm.get("url"):  # 兼容旧格式
                    base = cm["url"].rstrip("/")
                    custom_full = base + (cm.get("endpoint", "") or "/v1/chat/completions")
                if cm.get("endpoint"):
                    custom_ep = cm["endpoint"]
                if cm.get("api_key"):
                    custom_key = cm["api_key"]
                break

        # 自定义模型直接透传，不做格式转换
        if custom_base or custom_full:
            full_url = custom_full or (custom_base + (custom_ep or "/v1/chat/completions"))
            logger.info(f"[Passthrough] model={model_name}, url={full_url}")
            response = await call_opencode("", body, api_key=custom_key, full_url=full_url)
            raw_text = response.text
            if response.status_code != 200:
                logger.error(f"[Passthrough] {model_name} status={response.status_code}: {raw_text[:500]}")
                raise HTTPException(status_code=response.status_code, detail=raw_text[:2000])
            if config.detailed_logging:
                logger.info(f"[Raw Response] model={model_name}, body={raw_text[:2000]}")
            try:
                result = json.loads(raw_text) if raw_text else {}
                return JSONResponse(content=result)
            except:
                return PlainTextResponse(raw_text)

        # MiniMax 用 /v1/messages 端点
        if endpoint == "/v1/messages":
            # 添加思考模式禁用
            body["thinking"] = {"type": "disabled"}
            logger.debug(f"[Payload] Direct forward to {endpoint} with thinking disabled")
            response = await call_opencode(endpoint, body, api_key=custom_key, base_url=custom_base)

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"[Error] OpenCode API status={response.status_code}: {error_detail[:500]}")
                raise HTTPException(status_code=response.status_code, detail=error_detail)

            # 全量日志：上游原始响应
            if config.detailed_logging:
                try:
                    result = response.json()
                    logger.info(f"[Raw Response] model={model_name}, full={json.dumps(result, ensure_ascii=False)[:2000]}")
                except:
                    logger.error(f"[Raw Response] model={model_name}, not JSON: {response.text[:2000]}")
                    result = {"type": "message", "content": [{"type": "text", "text": response.text}]}
                return JSONResponse(content=result)

            duration = time.time() - start_time
            request_count += 1; save_stats()
            logger.info(f"[OK] {model_name} ({duration:.2f}s)")

            return JSONResponse(content=result)

        # 其他端点需要转换格式
        openai_messages = convert_anthropic_messages_to_openai(messages)
        openai_tools = convert_tools(tools) if tools else None

        # 构建请求
        openai_payload = {
            "model": model_id,
            "messages": openai_messages,
        }
        if openai_tools:
            openai_payload["tools"] = openai_tools

        # 全量日志：发往上游的请求
        if config.detailed_logging:
            logger.info(f"[Request Payload] model={model_name}, endpoint={endpoint}, "
                         f"payload={json.dumps(openai_payload, ensure_ascii=False)[:3000]}")

        # 调用 API
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
            raise HTTPException(status_code=response.status_code, detail=raw_text[:2000])

        # 转换响应
        anthropic_response = convert_response_to_anthropic(result, model_name)

        # 全量日志：转换后的 Anthropic 格式响应
        if config.detailed_logging:
            logger.info(f"[Anthropic Response] model={model_name}, "
                         f"body={json.dumps(anthropic_response, ensure_ascii=False)[:2000]}")

        duration = time.time() - start_time
        request_count += 1; save_stats()
        logger.info(f"[OK] {model_name} ({duration:.2f}s)")

        return JSONResponse(content=anthropic_response)

    except HTTPException:
        error_count += 1; save_stats()
        raise
    except Exception as e:
        error_count += 1; save_stats()
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
        request_count += 1; save_stats()
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
        if model_name:
            settings["model"] = display_name
            env = settings.setdefault("env", {})
            env["ANTHROPIC_MODEL"] = display_name
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = display_name
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = display_name
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = display_name
        env = settings.setdefault("env", {})
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
    try:
        sync_claude_settings()
    except Exception as e:
        logger.warning(f"[Config] 同步 Claude Code 配置失败: {e}")

    return {"status": "ok", "updated": list(env_updates.keys())}


@app.post("/api/refresh-models")
async def refresh_models_api():
    refresh_models()
    return {"status": "ok", "models": sorted(config.models.keys())}


@app.get("/api/custom-models")
async def get_custom_models():
    return load_custom_models()


@app.put("/api/custom-models")
async def save_custom_models_api(models: list = Body(...)):
    save_custom_models(models)
    config.models = merge_models(DEFAULT_MODELS if not hasattr(config, 'opencode_base_url') else config.models, models)
    if hasattr(config, 'opencode_base_url'):
        refresh_models()
    else:
        config.models = merge_models(DEFAULT_MODELS, models)
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
.modal-overlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.35);z-index:100;display:none;align-items:center;justify-content:center;backdrop-filter:blur(4px)}
.modal-overlay.open{display:flex}
.modal{background:#fff;border-radius:18px;padding:28px;width:90%;max-width:520px;max-height:90vh;overflow-y:auto;box-shadow:0 20px 60px rgba(0,0,0,.15);animation:modalIn .2s ease}
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
<h2 data-i18n="model">当前模型</h2>
<div class="model-list" id="modelList" style="margin-top:8px"></div>
</div>

<div class="btn-row" style="margin-bottom:6px">
<button class="btn btn-secondary" onclick="openModal('customModal')">➕ <span data-i18n="addModel">新增模型</span></button>
<button class="btn btn-secondary" onclick="editSelectedCustom()">✎ <span data-i18n="editModel">编辑模型</span></button>
<button class="btn btn-secondary" onclick="fetchModels()">🔄 <span data-i18n="refreshModels">刷新模型</span></button>
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
<div class="form-row"><button class="btn btn-secondary" style="flex:none;padding:8px 16px;font-size:13px" onclick="fetchModels()">🔄 <span data-i18n="refreshModels">刷新模型列表</span></button></div>
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
<div style="font-size:13px;color:#86868b;margin-bottom:12px" data-i18n="advancedDesc">重试、日志级别、思考模式等高级设置</div>
<div class="form-row"><div class="form-group" data-i18n-label="maxRetry"><label>最大重试次数</label><input id="maxRetry2" type="number" min="0" max="10"></div></div>
<div class="form-row"><div class="form-group" data-i18n-label="retryDelay"><label>重试间隔（秒）</label><input id="retryDelay2" type="number" step="0.5" min="0"></div></div>
<div class="form-row"><div class="form-group" data-i18n-label="logLevel"><label>日志级别</label><select id="logLevel2"><option>DEBUG</option><option selected>INFO</option><option>WARNING</option><option>ERROR</option></select></div></div>
<div class="form-row"><div class="checkbox-row"><input id="disableThinking2" type="checkbox"><label for="disableThinking2" data-i18n="disableThinking">禁用思考模式</label></div></div>
<div class="form-row"><div class="checkbox-row"><input id="detailedLogging2" type="checkbox"><label for="detailedLogging2" data-i18n="detailedLogging">记录详细日志</label></div></div>
<div class="form-row"><div class="form-group"><label data-i18n="alias">CC 模型名</label><input id="claudeAlias2" data-i18n="aliasPlaceholder" placeholder="留空=使用实际模型名" style="width:100%;padding:8px 12px;border:1px solid #d2d2d7;border-radius:8px;font-size:14px;box-sizing:border-box"></div></div>
<div style="font-size:12px;color:#86868b;margin-top:-6px" data-i18n="aliasDesc">设成视觉模型名（如 claude-sonnet-4-20250514）可让 CC 放开图片发送</div>
<div class="modal-actions"><button class="btn btn-secondary" style="flex:none;padding:6px 16px" onclick="closeModal('advancedModal')" data-i18n="cancel">取消</button><button class="btn btn-primary" style="flex:none;padding:6px 16px" onclick="saveAdvancedModal()" data-i18n="save">保存</button></div>
</div></div>


<div class="modal-overlay" id="customModal">
<div class="modal" style="padding:28px"><h2 data-i18n="customModels" style="margin-bottom:18px">自定义模型</h2>
<div style="display:flex;flex-direction:column;gap:10px">
<input id="newModelName" data-i18n="modelNamePlaceholder" placeholder="模型 ID" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
<input id="newModelDisplayName" data-i18n="modelDisplayPlaceholder" placeholder="显示名（留空=模型名）" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
<input id="newModelUrl" data-i18n="modelUrlPlaceholder" placeholder="Base URL (https://...)" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
<select id="newModelFormat" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;background:#fff;box-sizing:border-box">
<option value="openai">OpenAI (/v1/chat/completions)</option>
<option value="anthropic">Anthropic (/v1/messages)</option>
</select>
<input id="newModelApiKey" type="password" data-i18n="modelKeyPlaceholder" placeholder="API Key（留空使用全局）" style="width:100%;padding:10px 14px;border:1px solid #d2d2d7;border-radius:8px;font-size:15px;box-sizing:border-box">
</div>
<div class="modal-actions" style="margin-top:20px"><button class="btn btn-secondary" style="flex:none;padding:8px 20px" onclick="closeModal('customModal');window._editingIdx=undefined" data-i18n="cancel">取消</button><button class="btn btn-primary" style="flex:none;padding:8px 20px" onclick="saveCustomModal()" data-i18n="save">保存</button></div>
</div></div>

<div class="modal-overlay" id="logsModal" onclick="if(event.target===this)closeModal('logsModal')">
<div class="modal" style="max-width:800px;max-height:90vh"><h2 data-i18n="logs">运行日志</h2>
<div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap">
<div style="flex:1;min-width:140px">
<label data-i18n="logLevel" style="display:block;font-size:12px;font-weight:500;color:#6e6e73;margin-bottom:4px">日志级别</label>
<select id="logLevel2" style="width:100%;padding:8px 10px;border:1px solid #d2d2d7;border-radius:8px;font-size:13px;background:#fff;box-sizing:border-box"><option>DEBUG</option><option selected>INFO</option><option>WARNING</option><option>ERROR</option></select>
</div>
<div style="display:flex;align-items:flex-end;gap:12px;padding-bottom:4px">
<div class="checkbox-row"><input id="detailedLogging2" type="checkbox"><label for="detailedLogging2" data-i18n="detailedLogging">记录详细日志</label></div>
</div>
</div>
<div style="display:flex;gap:8px;margin-bottom:8px">
<button class="btn btn-secondary" style="padding:4px 12px;font-size:12px;flex:none" onclick="loadLogs()" data-i18n="refresh">刷新</button>
<label style="font-size:12px;color:#6e6e73;display:flex;align-items:center;gap:4px;cursor:pointer"><input type="checkbox" id="autoRefresh2" checked onchange="toggleAutoRefresh()" style="width:auto"><span data-i18n="autorefresh">自动刷新</span></label>
</div>
<pre id="logViewer2" style="background:#f0f0f5;border-radius:8px;padding:12px;font-size:12px;line-height:1.5;overflow-x:auto;max-height:60vh;overflow-y:auto;white-space:pre;margin:0;color:#1d1d1f;font-family:SFMono-Regular,Consolas,'Liberation Mono',monospace">加载中...</pre>
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
    logempty: "(空日志)",
    logfail: "加载失败",
    aliasDesc: "留空显示实际模型名。设成视觉模型名（如 claude-sonnet-4-20250514）可让 CC 放开图片发送",
    aliasPlaceholder: "留空=使用实际模型名",
    autorefresh: "自动刷新",
    customModels: "自定义模型",
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
    advancedDesc: "重试、日志、思考模式等高级设置",
    maxRetry: "最大重试次数",
    retryDelay: "重试间隔（秒）",
    logLevel: "日志级别",
    disableThinking: "禁用思考模式",
    detailedLogging: "记录详细日志",
    modelNamePlaceholder: "模型名",
    modelDisplayPlaceholder: "显示名（留空=模型名）",
    modelUrlPlaceholder: "API 地址 (https://...)",
    modelKeyPlaceholder: "API Key（留空使用全局）",
    getKey: "获取 Key",
    refreshModels: "刷新模型列表",
    refreshModelsDone: "模型列表已更新",
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
    logempty: "(empty log)",
    logfail: "Load failed",
    aliasDesc: "Leave empty to show actual model name. Set to a vision model name (e.g. claude-sonnet-4-20250514) to enable image input in CC.",
    aliasPlaceholder: "Leave empty = use actual model",
    autorefresh: "Auto refresh",
    customModels: "Custom Models",
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
    advancedDesc: "Retry, logging, thinking mode, etc.",
    maxRetry: "Max retries",
    retryDelay: "Retry delay (s)",
    logLevel: "Log level",
    disableThinking: "Disable thinking mode",
    detailedLogging: "Detailed request logging",
    modelNamePlaceholder: "Model ID",
    modelDisplayPlaceholder: "Display name (leave empty = use ID)",
    modelUrlPlaceholder: "API URL (https://...)",
    modelKeyPlaceholder: "API Key (leave empty = use global)",
    getKey: "Get Key",
    refreshModels: "Refresh model list",
    refreshModelsDone: "Model list updated",
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
    const ml = document.getElementById('modelList');
    if (ml && cfg.models) {
      const customIds = customModels.map(m => m.id);
      const isCustom = customIds.includes(sel);
      const dn = isCustom ? (customModels.find(cm => cm.id === sel)?.display_name || '') : '';
      ml.innerHTML = cfg.models.map(m => {
        const isCustom = customIds.includes(m);
        const dn = isCustom ? (customModels.find(cm => cm.id === m)?.display_name || '') : '';
        const label = dn || m;
        return '<span class="model-tag'+(m===sel?' selected':'')+(isCustom?' custom':'')+'" data-model="'+m+'" onclick="selectModel(\''+m.replace(/'/g,"\\'")+'\')" '+(isCustom?'style="position:relative;padding-right:18px"':'')+'>'+label+(isCustom?' <sup style="font-size:10px;opacity:.7">C</sup>':'')+
          (isCustom?'<span class="tag-action" onclick="event.stopPropagation();deleteCustomModelById(\''+m+'\')" style="cursor:pointer;color:#ff3b30;font-size:12px;position:absolute;top:2px;right:3px" title="Delete">✕</span>':'')+'</span>';
      }).join('');
    }
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
    renderCustomModels();
    // 重新渲染模型标签，应用自定义标记
    const mlEl = document.getElementById('modelList');
    if (mlEl && mlEl.children.length) {
      const customIds = customModels.map(m => m.id);
      const selectedEl = mlEl.querySelector('.selected');
      const selId = selectedEl ? selectedEl.getAttribute('data-model') : '';
      const isCustom = customIds.includes(selId);
      Array.from(mlEl.children).forEach(el => {
        const id = el.getAttribute('data-model');
        if (customIds.includes(id)) {
          el.classList.add('custom');
          const dn = customModels.find(m => m.id === id)?.display_name;
          if (dn && !el.querySelector('sup')) {
            el.innerHTML = dn + ' <sup style="font-size:10px;opacity:.7">C</sup>';
          }
          // 添加编辑/删除按钮
          if (!el.querySelector('.tag-action')) {
            const btnStyle = 'position:relative;padding-right:18px';
            if (!el.style.position) el.style.cssText = btnStyle;
            el.insertAdjacentHTML('beforeend', '<span class="tag-action" onclick="event.stopPropagation();deleteCustomModelById(\''+id+'\')" style="cursor:pointer;color:#ff3b30;font-size:12px;position:absolute;top:2px;right:3px" title="Delete">✕</span>');
          }
        }
      });
    }
  } catch(e) {}
}
function renderCustomModels() {
  const el = document.getElementById('customModelList');
  if (!customModels.length) { el.innerHTML = ''; return; }
  el.innerHTML = customModels.map((m,i) => '<div style="display:flex;align-items:center;gap:6px;background:#f0f0f5;padding:6px 10px;border-radius:8px;margin:4px 0;font-size:13px">'+
    '<span style="flex:1;min-width:0">'+(m.display_name||m.id)+'<span style="color:#86868b;font-size:11px;margin-left:6px">'+((m.base_url||m.url||'')+(m.endpoint||''))+'</span></span>'+
    '<span onclick="editCustomModel('+i+')" style="cursor:pointer;color:#0071e3;font-size:12px;white-space:nowrap">编辑</span>'+
    '<span onclick="deleteCustomModel('+i+')" style="cursor:pointer;color:#ff3b30;font-size:12px;white-space:nowrap">删除</span></div>').join('');
}
function editCustomModel(i) {
  const m = customModels[i];
  document.getElementById('newModelName').value = m.id;
  document.getElementById('newModelDisplayName').value = m.display_name||'';
  document.getElementById('newModelUrl').value = m.base_url||m.url||'';
  document.getElementById('newModelFormat').value = (m.endpoint||'').includes('messages') ? 'anthropic' : 'openai';
  document.getElementById('newModelApiKey').value = m.api_key||'';
  window._editingIdx = i;
}
async function deleteCustomModel(i) {
  customModels.splice(i, 1);
  await api('PUT','/api/custom-models', customModels);
  renderCustomModels();
  await load();
  toast(t('customDeleted'));
}
function saveConnModal() { closeModal('connModal'); save(); }
async function fetchModels() {
  toast(t('refreshModels')+'...');
  try {
    await api('POST','/api/refresh-models');
    await load();
    toast(t('refreshModelsDone'));
  } catch(e) {
    toast(t('savefail')+': '+e.message, false);
  }
}
function openModal(id) {
  document.getElementById(id).classList.add('open');
  if (id === 'logsModal') { loadLogs(); toggleAutoRefresh(); }
}
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
  if (id === 'logsModal') { if (logTimer) { clearInterval(logTimer); logTimer = null; } }
}
function syncToModals(cfg) {
  setVal('host2', cfg.router_host);
  setVal('port2', cfg.router_port);
  setVal('masterKey2', cfg.master_key);
  setVal('maxRetry2', cfg.max_retry);
  setVal('retryDelay2', cfg.retry_delay);
  setVal('logLevel2', cfg.log_level);
  setChecked('disableThinking2', cfg.disable_thinking);
  setChecked('detailedLogging2', cfg.detailed_logging);
  setVal('claudeAlias2', cfg.claude_model_alias);
}
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v||''; }
function setChecked(id, v) { const el = document.getElementById(id); if (el) el.checked = v!==false; }
function saveServiceModal() { closeModal('serviceModal'); save(); }
function saveAdvancedModal() { closeModal('advancedModal'); save(); }
function saveAliasModal() { closeModal('aliasModal'); save(); }
async function save() {
  const body = {
    opencode_base_url: document.getElementById('baseUrl').value,
    opencode_api_key: document.getElementById('apiKey').value,
    router_host: getVal('host2'),
    router_port: parseInt(getVal('port2'))||4000,
    master_key: getVal('masterKey2'),
    max_retry: parseInt(getVal('maxRetry2'))||3,
    retry_delay: parseFloat(getVal('retryDelay2'))||1,
    log_level: getVal('logLevel2')||'INFO',
    disable_thinking: getChecked('disableThinking2'),
    detailed_logging: getChecked('detailedLogging2'),
    claude_model_alias: getVal('claudeAlias2'),
  };
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
function saveCustomModal() {
  const name = document.getElementById('newModelName').value.trim();
  const display = document.getElementById('newModelDisplayName').value.trim() || name;
  const url = document.getElementById('newModelUrl').value.trim();
  const fmt = document.getElementById('newModelFormat').value;
  const ak = document.getElementById('newModelApiKey').value.trim();
  const ep = fmt === 'anthropic' ? '/v1/messages' : '/v1/chat/completions';
  if (!name) return;
  // 编辑已有模型
  if (window._editingIdx !== undefined && window._editingIdx < customModels.length) {
    customModels[window._editingIdx] = {id: name, display_name: display, base_url: url, endpoint: ep, api_key: ak};
    window._editingIdx = undefined;
  } else {
    customModels.push({id: name, display_name: display, base_url: url, endpoint: ep, api_key: ak});
  }
  api('PUT','/api/custom-models', customModels).then(() => {
    document.getElementById('newModelName').value = '';
    document.getElementById('newModelDisplayName').value = '';
    document.getElementById('newModelUrl').value = '';
    document.getElementById('newModelApiKey').value = '';
    closeModal('customModal');
    renderCustomModels();
    load();
    toast(t('saved'));
  }).catch(e => toast(t('savefail')+': '+e.message, false));
}
let logTimer = null;
function toggleAutoRefresh() {
  if (logTimer) { clearInterval(logTimer); logTimer = null; }
  if (document.getElementById('autoRefresh2') && document.getElementById('autoRefresh2').checked) {
    logTimer = setInterval(loadLogs, 5000);
  }
}
function editCustomModelByid(id) {
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
      renderCustomModels();
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
  const sel = document.querySelector('#modelList .model-tag.selected');
  if (!sel) { openModal('customModal'); return; }
  const id = sel.getAttribute('data-model');
  const i = customModels.findIndex(m => m.id === id);
  if (i === -1) { openModal('customModal'); return; }
  editCustomModel(i);
  openModal('customModal');
}
function deleteSelectedCustom() {
  const sel = document.querySelector('#modelList .model-tag.selected');
  if (!sel) return;
  const id = sel.getAttribute('data-model');
  const idx = customModels.findIndex(m => m.id === id);
  if (idx === -1) return;
  if (!confirm('删除自定义模型「'+(customModels[idx].display_name||id)+'」？')) return;
  customModels.splice(idx, 1);
  api('PUT','/api/custom-models', customModels).then(() => {
    renderCustomModels();
    load();
    toast(t('customDeleted'));
  });
}
async function loadLogs() {
  try {
    const r = await api('GET','/api/logs?limit=100');
    const v = document.getElementById('logViewer2');
    if (!v) return;
    v.textContent = r.lines.join('')||t('logempty');
    v.scrollTop = v.scrollHeight;
  } catch(e) {
    const v = document.getElementById('logViewer2');
    if (v) v.textContent = t('logfail')+': '+e.message;
  }
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
def refresh_models():
    """从上游拉取模型列表，成功则缓存到本地，失败用缓存或默认"""
    cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models_cache.json")
    url = f"{config.opencode_base_url}/v1/models"
    headers = {
        "Authorization": f"Bearer {config.opencode_api_key}",
        "x-api-key": config.opencode_api_key,
    }
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                ids = [m["id"] for m in data.get("data", [])]
                if ids:
                    new_models = {}
                    for mid in ids:
                        ep = "/v1/messages" if mid.startswith("minimax") else "/v1/chat/completions"
                        new_models[mid] = {"id": mid, "endpoint": ep}
                    config.models = merge_models(new_models, load_custom_models())
                    # 缓存到本地
                    try:
                        with open(cache_file, "w") as f:
                            json.dump(ids, f)
                    except:
                        pass
                    logger.info(f"[Models] 从上游加载 {len(new_models)} 个模型 + {len(load_custom_models())} 个自定义")
                    return
    except Exception as e:
        logger.warning(f"[Models] 上游拉取失败: {e}")
    # 失败时读缓存
    try:
        with open(cache_file, "r") as f:
            ids = json.load(f)
            if ids:
                cached = {}
                for mid in ids:
                    ep = "/v1/messages" if mid.startswith("minimax") else "/v1/chat/completions"
                    cached[mid] = {"id": mid, "endpoint": ep}
                config.models = merge_models(cached, load_custom_models())
                logger.info(f"[Models] 从缓存加载 {len(cached)} 个模型")
                return
    except:
        pass
    # 最终兜底：默认列表
    config.models = merge_models(DEFAULT_MODELS, load_custom_models())

if __name__ == "__main__":
    refresh_models()
    print()
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║                    cc2go v2.0.0                         ║")
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