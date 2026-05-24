"""
MCP 工具短路模块
检测特定工具调用并直接处理，避免绕道 LLM 后端
"""

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("llm_router")

if sys.platform == "win32":
    MMX_PATH = shutil.which("mmx") or shutil.which("mmx.cmd") or "mmx"
else:
    MMX_PATH = "mmx"

# 可短路的工具及其处理器
BYPASS_TOOLS = {
    "web_search": {"type": "mmx", "args": ["search", "query"]},
    "mcp__MiniMax__web_search": {"type": "mmx", "args": ["search", "query"]},
}


def should_bypass(body: Dict) -> Tuple[bool, Optional[str]]:
    """
    判断请求是否应短路（请求层检查）
    仅检查最后一条消息是否为 assistant 且包含 tool_use。
    若最后一条消息是 tool_result，说明工具已被执行，不应绕过。
    """
    messages = body.get("messages", [])
    if not messages:
        logger.debug("[Bypass] no messages, skip")
        return False, None

    last_msg = messages[-1]
    role = last_msg.get("role", "")
    content = last_msg.get("content", [])

    # 只有最后一条消息是 assistant 角色时才可能触发 bypass
    if role != "assistant":
        logger.debug(f"[Bypass] last msg role={role}, not assistant, skip")
        return False, None

    if not isinstance(content, list):
        return False, None

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type", "")
        block_name = block.get("name", "")
        if block_type in ("tool_use", "tool_use_block"):
            if not block_name:
                continue
            # MCP 格式: mcp__Provider__tool_name → 归一化到 base
            if block_name.startswith("mcp__"):
                base = block_name.split("__", 2)[-1]
                if base in BYPASS_TOOLS:
                    logger.info(f"[Bypass] HIT! name={block_name} → base={base}")
                    return True, base
            if block_name in BYPASS_TOOLS:
                logger.info(f"[Bypass] HIT! name={block_name}")
                return True, block_name

    logger.debug("[Bypass] last msg has no matching tool_use")
    return False, None


SEARCH_KEYWORDS = [
    "search", "search for", "search the web", "web search", "google",
    "搜索", "搜一下", "搜", "查找", "查一下", "查询", "找一下",
    "find", "look up", "lookup",
]

BUILTIN_TOOL_PREFIXES = ["web_search"]


def _has_search_intent(messages: List[Dict]) -> bool:
    """检测用户消息是否有搜索意图"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content.lower().strip()
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict)]
                text = " ".join(parts).lower().strip()
            else:
                continue
            if not text:
                continue
            for kw in SEARCH_KEYWORDS:
                if text.startswith(kw) or text == kw:
                    return True
            return False
    return False


def has_anthropic_builtin_tool(body: Dict) -> bool:
    """检查 tools 数组中是否含有 Anthropic 内置工具声明（如 web_search_20250305）"""
    tools = body.get("tools", [])
    for t in tools:
        if not isinstance(t, dict):
            continue
        ttype = t.get("type", "")
        for prefix in BUILTIN_TOOL_PREFIXES:
            if ttype.startswith(prefix):
                return True
    return False


def should_tool_declaration_bypass(body: Dict) -> Tuple[bool, Optional[str]]:
    """
    检测 tools 数组中的 bypass 工具声明（请求层）
    只有用户消息有搜索意图时才 bypass，防止误拦截正常对话。
    """
    tools = body.get("tools", [])
    if not tools:
        return False, None

    # 仅当用户消息有搜索意图时才触发 bypass
    if not _has_search_intent(body.get("messages", [])):
        return False, None

    for t in tools:
        if not isinstance(t, dict):
            continue
        tname = t.get("name", "")
        ttype = t.get("type", "")
        if tname in BYPASS_TOOLS:
            logger.info(f"[Bypass/ToolDecl] HIT! name={tname}")
            return True, tname
        for prefix in BUILTIN_TOOL_PREFIXES:
            if ttype.startswith(prefix):
                logger.info(f"[Bypass/ToolDecl] HIT! type={ttype}, mapping to web_search")
                return True, tname or "web_search"

    return False, None


def find_bypass_tool_uses(anthropic_response: Dict) -> List[Dict]:
    """查找 Anthropic 响应中包含 bypass 工具的 tool_use 块"""
    found = []
    content = anthropic_response.get("content", [])
    if not isinstance(content, list):
        return []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") in BYPASS_TOOLS:
            found.append(item)
    return found


async def apply_response_bypass(anthropic_response: Dict) -> Dict:
    """
    响应层 bypass：将 Anthropic 响应中的 bypass tool_use 替换为搜索结果文本。
    若没有 bypass 项，返回原响应不变。
    """
    bypass_items = find_bypass_tool_uses(anthropic_response)
    if not bypass_items:
        return anthropic_response

    new_content = []
    for item in anthropic_response.get("content", []):
        if item in bypass_items:
            query = item.get("input", {}).get("query", "")
            logger.info(f"[Bypass/Response] intercepting tool_use: {item['name']}, query={query!r}")
            bypass_result = await handle_bypass(item["name"], query)
            new_content.extend(bypass_result.get("content", []))
        else:
            new_content.append(item)

    result = dict(anthropic_response)
    result["content"] = new_content
    result["stop_reason"] = "end_turn"
    logger.info("[Bypass/Response] tool_use handled locally, returning text")
    return result


def extract_query(messages: List[Dict]) -> str:
    """从消息列表中提取用户查询文本"""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content.strip()
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt = part.get("text", "").strip()
                        if txt:
                            return txt
    # fallback: 最后一条消息
    if messages:
        content = messages[-1].get("content", "")
        if isinstance(content, str):
            return content.strip()
    return ""


async def handle_bypass(tool_name: str, query: str) -> Dict:
    """
    执行短路处理，返回 Anthropic 格式的响应
    """
    logger.info(f"[Bypass] handle_bypass called: tool={tool_name}, query={query!r}")
    handler = BYPASS_TOOLS.get(tool_name)
    if not handler:
        raise ValueError(f"Unknown bypass tool: {tool_name}")

    if handler["type"] == "mmx":
        result = await handle_mmx_search(tool_name, query)
        logger.debug(f"[Bypass] mmx result: type={result['type']}, content_len={len(result.get('content',[]))}")
        return result

    raise ValueError(f"Unknown handler type: {handler['type']}")


async def handle_mmx_search(tool_name: str, query: str) -> Dict:
    """通过 mmx CLI 执行搜索并返回 Anthropic 格式"""
    logger.info(f"[Bypass/mmx] starting search: query={query!r}, mmx_path={MMX_PATH}")
    result = {
        "type": "message",
        "id": f"bypass-{int(time.time() * 1000)}",
        "role": "assistant",
        "content": [],
        "model": "bypass",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0}
    }

    if not query:
        result["content"] = [{"type": "text", "text": "No query provided"}]
        return result

    try:
        proc = await asyncio.create_subprocess_exec(
            MMX_PATH, "search", "query", query,
            "--output", "json",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=30.0
            )
        except asyncio.TimeoutError:
            proc.kill()
            result["content"] = [{"type": "text", "text": "Search timed out after 30s"}]
            return result

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            result["content"] = [{"type": "text", "text": f"Search failed: {err_msg}"}]
        else:
            output = stdout.decode("utf-8", errors="replace")
            try:
                data = json.loads(output)
                lines = []
                for i, item in enumerate(data.get("organic", [])[:5]):
                    title = item.get("title", "")
                    link = item.get("link", "")
                    snippet = item.get("snippet", "")
                    snippet_short = snippet[:100] + "..." if len(snippet) > 100 else snippet
                    lines.append(f"{i+1}. {title}\n   {link}")
                    if snippet_short:
                        lines.append(f"   {snippet_short}")
                if not lines:
                    result["content"] = [{"type": "text", "text": f"搜索 [{query}] 无结果"}]
                else:
                    result["content"] = [{"type": "text", "text": f"搜索 [{query}]:\n\n" + "\n\n".join(lines)}]
            except json.JSONDecodeError:
                result["content"] = [{"type": "text", "text": output[:2000]}]
    except Exception as e:
        result["content"] = [{"type": "text", "text": f"Search error: {str(e)}"}]

    return result