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
    判断请求是否应短路
    仅当消息中模型已实际调用了 bypass 工具（tool_use）才触发，
    不因 tools 参数中有该工具定义就短路。
    Returns: (should_bypass, tool_name)
    """
    messages = body.get("messages", [])
    if not messages:
        return False, None

    # 遍历消息，查找 assistant 消息中的 tool_use 块
    for msg in reversed(messages):
        content = msg.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("tool_use", "tool_use_block"):
                name = block.get("name", "")
                if not name:
                    continue
                # MCP 格式: mcp__Provider__tool_name → 归一化到 base
                if name.startswith("mcp__"):
                    base = name.split("__", 2)[-1]
                    if base in BYPASS_TOOLS:
                        return True, base
                if name in BYPASS_TOOLS:
                    return True, name
    return False, None


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
    handler = BYPASS_TOOLS.get(tool_name)
    if not handler:
        raise ValueError(f"Unknown bypass tool: {tool_name}")

    if handler["type"] == "mmx":
        return await handle_mmx_search(tool_name, query)

    raise ValueError(f"Unknown handler type: {handler['type']}")


async def handle_mmx_search(tool_name: str, query: str) -> Dict:
    """通过 mmx CLI 执行搜索并返回 Anthropic 格式"""
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