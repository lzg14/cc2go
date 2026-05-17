"""
MCP 工具短路模块
检测特定工具调用并直接处理，避免绕道 LLM 后端
"""

import asyncio
import json
import logging
import subprocess
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("llm_router")

# 可短路的工具及其处理器
BYPASS_TOOLS = {
    "web_search": {"type": "mmx", "args": ["search", "query"]},
    "mcp__MiniMax__web_search": {"type": "mmx", "args": ["search", "query"]},
}


def should_bypass(body: Dict) -> Tuple[bool, Optional[str]]:
    """
    判断请求是否应短路
    Returns: (should_bypass, tool_name)
    """
    tools = body.get("tools", [])
    if not tools:
        return False, None

    for tool in tools:
        name = tool.get("name", "") or (tool.get("function", {}) or {}).get("name", "")
        if not name:
            continue
        # 处理 MCP 格式: mcp__ProviderName__tool_name → 归一化到 base
        is_mcp_format = name.startswith("mcp__")
        base_name = name.split("__", 2)[-1] if is_mcp_format else name
        if base_name in BYPASS_TOOLS:
            return True, base_name
        # 完整名称也在配置中时（如 mcp__MiniMax__web_search）
        if name in BYPASS_TOOLS:
            return True, base_name

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
            "mmx", "search", "query", query,
            "--output", "json",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

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