# MCP 工具短路实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 检测某些 MCP 工具调用（如 web_search）不经过 LLM 直接短路返回结果，避免被 cc2go 劫持转发到错误后端。

**Architecture:** 在 `/v1/messages` 入口处新增请求检测层，根据工具名/系统提示判断是否需要短路。短路时将请求直接派发给对应的处理器（本地 CLI 或独立 HTTP 服务），返回预格式化的 Anthropic 响应。

**Tech Stack:** FastAPI / httpx / asyncio / subprocess

---

## 文件结构

```
src/
  router.py          # 修改: 在 anthropic_messages() 入口注入短路逻辑
  mcp_bypass.py     # 新建: MCP 工具短路检测与处理器
  mcp_bypass_test.py # 新建: 短路逻辑单元测试
```

---

### Task 1: MCP 工具短路检测核心逻辑

**Files:**
- Create: `src/mcp_bypass.py`
- Modify: `src/router.py:462-473`（在请求解析后、转发前插入短路检测）

- [ ] **Step 1: 写测试用例**

```python
# src/mcp_bypass_test.py
import pytest
from mcp_bypass import should_bypass, build_bypass_response

def test_should_bypass_websearch():
    """检测到 web_search 工具时应返回 bypass"""
    body = {
        "model": "qwen3.6-plus",
        "messages": [{"role": "user", "content": "搜索今天天气"}],
        "tools": [{"name": "web_search", "description": "Web search"}]
    }
    result = should_bypass(body)
    assert result is True

def test_should_not_bypass_no_tools():
    """普通对话不应短路"""
    body = {
        "model": "qwen3.6-plus",
        "messages": [{"role": "user", "content": "你好"}]
    }
    result = should_bypass(body)
    assert result is False

def test_should_not_bypass_unknown_tool():
    """未知工具名不应短路"""
    body = {
        "model": "qwen3.6-plus",
        "messages": [{"role": "user", "content": "你好"}],
        "tools": [{"name": "my_custom_tool"}]
    }
    result = should_bypass(body)
    assert result is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/mcp_bypass_test.py -v`
Expected: FAIL — mcp_bypass.py not found

- [ ] **Step 3: 实现短路检测逻辑**

```python
# src/mcp_bypass.py
"""
MCP 工具短路模块
检测特定工具调用并直接处理，避免绕道 LLM 后端
"""

import json
import logging
import subprocess
from typing import Dict, Optional, Tuple

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
        if name in BYPASS_TOOLS:
            return True, name
        # 处理 MCP 格式: mcp__ProviderName__tool_name
        if name.startswith("mcp__"):
            base_name = name.split("__", 2)[-1] if "__" in name else name
            if base_name in BYPASS_TOOLS:
                return True, base_name

    return False, None


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
    from .router import config

    # 提取搜索 query（从最后一条 user 消息中获取）
    result = {
        "type": "message",
        "id": f"bypass-{int(__import__('time').time() * 1000)}",
        "role": "assistant",
        "content": [],
        "model": "bypass",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 0, "output_tokens": 0}
    }

    try:
        proc = await __import__('asyncio').create_subprocess_exec(
            "mmx", "search", "query", query,
            "--output", "json",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            result["content"] = [{"type": "text", "text": f"Search failed: {stderr.decode()}"}]
        else:
            output = stdout.decode("utf-8", errors="replace")
            try:
                data = json.loads(output)
                lines = []
                for i, item in enumerate(data.get("organic", [])[:5]):
                    lines.append(f"{i+1}. {item.get('title', '')}\n   {item.get('link', '')}")
                result["content"] = [{"type": "text", "text": f"搜索结果:\n\n" + "\n\n".join(lines)}]
            except json.JSONDecodeError:
                result["content"] = [{"type": "text", "text": output[:2000]}]
    except Exception as e:
        result["content"] = [{"type": "text", "text": f"Search error: {str(e)}"}]

    return result
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/mcp_bypass_test.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 集成到 router.py 入口**

在 `anthropic_messages()` 函数中，请求体解析后（line ~472）添加：

```python
# 在 body = await request.json() 后、model_name = body.get(...) 前插入
bypass, tool_name = should_bypass(body)
if bypass:
    logger.info(f"[Bypass] tool={tool_name}, shortcutting request")
    # 提取搜索 query
    messages = body.get("messages", [])
    query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                query = content
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        query = part.get("text", "")
            break
    if not query:
        query = messages[-1].get("content", "") if messages else ""

    result = await handle_bypass(tool_name, query)
    return JSONResponse(content=result)
```

- [ ] **Step 6: 验证集成**

Run: `curl -s http://127.0.0.1:4000/v1/models`
Expected: JSON 模型列表正常返回（确认 cc2go 仍正常运行）

- [ ] **Step 7: 提交**

```bash
git add src/mcp_bypass.py src/mcp_bypass_test.py src/router.py
git commit -m "feat: MCP工具短路 - web_search等工具直接处理不走LLM"
```

---

### Task 2: 支持更多短路工具（MCP 通用处理器）

**Files:**
- Modify: `src/mcp_bypass.py:10-30`（扩展 BYPASS_TOOLS 配置）
- Create: `src/mcp_bypass_test.py` 补充测试

- [ ] **Step 1: 添加 vision/understand_image 短路支持**

```python
# 在 BYPASS_TOOLS 中添加
BYPASS_TOOLS = {
    "web_search": {"type": "mmx", "args": ["search", "query"]},
    "mcp__MiniMax__web_search": {"type": "mmx", "args": ["search", "query"]},
    "mcp__MiniMax__understand_image": {"type": "mmx", "args": ["vision", "describe"]},
    # 未来可扩展更多工具...
}
```

- [ ] **Step 2: 写测试**

```python
def test_should_bypass_mmx_underscore_format():
    body = {
        "tools": [{"name": "mcp__MiniMax__web_search"}]
    }
    result, name = should_bypass(body)
    assert result is True
    assert name == "web_search"
```

- [ ] **Step 3: 运行测试**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/mcp_bypass_test.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add src/mcp_bypass.py src/mcp_bypass_test.py
git commit -m "feat: 扩展MCP工具短路支持vision等工具"
```