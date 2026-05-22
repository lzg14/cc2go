# Streaming 规范化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 参照 anyrouter2proxy 的 SSE 模板，统一 cc2go 的流式响应格式，确保 Claude Code 接收到的流事件完整正确。

**Architecture:** 新建 `src/streaming.py` 模块，实现 SSE 事件生成器，将 OpenAI 流式响应转换为标准 Anthropic SSE 格式。重构 `router.py` 中的流式处理逻辑调用新模块。

**Tech Stack:** FastAPI / httpx / asyncio / json

---

## 文件结构

```
src/
  router.py          # 修改: 引入 streaming.py 中的转换器
  streaming.py       # 新建: SSE 流式响应转换核心逻辑
  streaming_test.py  # 新建: 流式响应单元测试
```

---

### Task 1: SSE 流式响应转换器

**Files:**
- Create: `src/streaming.py`
- Create: `src/streaming_test.py`

- [ ] **Step 1: 写测试用例**

```python
# src/streaming_test.py
import pytest
import json

def test_build_message_start_event():
    from streaming import build_message_start_event
    result = build_message_start_event("msg-123", "test-model")
    assert result["type"] == "message_start"
    assert result["message"]["id"] == "msg-123"
    assert result["message"]["role"] == "assistant"

def test_build_content_block_start():
    from streaming import build_content_block_start
    result = build_content_block_start(0, "text")
    assert result["type"] == "content_block_start"
    assert result["index"] == 0
    assert result["content_block"]["type"] == "text"

def test_build_ping_event():
    from streaming import build_ping_event
    result = build_ping_event(12345)
    assert result["type"] == "ping"
    assert result["index"] == 12345

def test_format_sse_event():
    from streaming import format_sse_event
    event = {"type": "message_start", "message": {"id": "test"}}
    result = format_sse_event(event, "message_start")
    assert "event: message_start" in result
    assert "data: " in result
    assert result.endswith("\n\n")
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/streaming_test.py -v`
Expected: FAIL — streaming.py not found

- [ ] **Step 3: 实现 SSE 转换器**

```python
# src/streaming.py
"""
SSE 流式响应转换器
将 OpenAI chat/completions 流式响应转换为 Anthropic SSE 格式
"""

import json
import time
import uuid
from typing import AsyncGenerator, Dict, List, Optional


def build_message_start_event(msg_id: str, model: str) -> Dict:
    """构建 message_start 事件"""
    return {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
    }


def build_content_block_start(index: int, block_type: str = "text") -> Dict:
    """构建 content_block_start 事件"""
    content_block = {"type": block_type}
    if block_type == "text":
        content_block["text"] = ""
    elif block_type == "tool_use":
        content_block["id"] = ""
        content_block["name"] = ""
        content_block["input"] = {}
    return {
        "type": "content_block_start",
        "index": index,
        "content_block": content_block
    }


def build_content_block_delta(index: int, delta_type: str, content: str) -> Dict:
    """构建 content_block_delta 事件"""
    delta = {}
    if delta_type == "text":
        delta["text"] = content
    elif delta_type == "tool_use_input":
        # tool_use 的 input 增量
        delta["input"] = content  # 可能是 JSON 字符串
    elif delta_type == "tool_use_name":
        delta["name"] = content
    elif delta_type == "tool_use_id":
        delta["id"] = content
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": delta
    }


def build_message_delta_event(msg_id: str, stop_reason: str = "end_turn", usage: Dict = None) -> Dict:
    """构建 message_delta 事件"""
    return {
        "type": "message_delta",
        "index": 0,
        "delta": {"stop_sequence": None},
        "usage": usage or {"input_tokens": 0, "output_tokens": 0},
        "stop_reason": stop_reason
    }


def build_message_stop_event() -> Dict:
    """构建 message_stop 事件"""
    return {"type": "message_stop"}


def build_ping_event(index: int) -> Dict:
    """构建 ping 事件"""
    return {"type": "ping", "index": index}


def format_sse_event(event: Dict, event_type: str) -> bytes:
    """将事件字典格式化为 SSE 格式的 bytes"""
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")


async def convert_openai_stream_to_anthropic(
    response: httpx.Response,
    model: str
) -> AsyncGenerator[bytes, None]:
    """
    将 OpenAI 流式响应转换为 Anthropic SSE 格式
    逐行解析 OpenAI chunk，转换为 Anthropic 事件
    """
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    content_index = 0
    tool_index = 0
    has_sent_message_start = False
    current_block_type = "text"
    buffer = ""

    async for line in response.aiter_lines():
        line = line.strip()
        if not line:
            continue

        # OpenAI 流式响应格式: data: {...}
        if not line.startswith("data: "):
            continue

        data_str = line[6:]
        if data_str in ("[DONE]", ""):
            # 发送 message_stop
            yield format_sse_event(build_message_stop_event(), "message_stop")
            continue

        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue

        choices = chunk.get("choices", [])
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason")

        # message_start（首个 chunk 时发送）
        if not has_sent_message_start:
            has_sent_message_start = True
            yield format_sse_event(build_message_start_event(msg_id, model), "message_start")
            yield format_sse_event(build_content_block_start(content_index, "text"), "content_block_start")

        # 处理 delta 内容
        content = delta.get("content", "")
        if content:
            yield format_sse_event(
                build_content_block_delta(content_index, "text", content),
                "content_block_delta"
            )

        # 处理 tool_calls
        tool_calls = delta.get("tool_calls", [])
        for tc in tool_calls:
            func = tc.get("function", {})
            tc_id = tc.get("id", "")
            tc_name = func.get("name", "")
            tc_input = func.get("arguments", "")

            # 新 tool_use 开始
            if tc_id and tc_name:
                yield format_sse_event(
                    build_content_block_start(tool_index, "tool_use"),
                    "content_block_start"
                )
                yield format_sse_event(
                    build_content_block_delta(tool_index, "tool_use_id", tc_id),
                    "content_block_delta"
                )
                yield format_sse_event(
                    build_content_block_delta(tool_index, "tool_use_name", tc_name),
                    "content_block_delta"
                )

            if tc_input:
                # input 可能是增量 JSON
                try:
                    parsed = json.loads(tc_input) if isinstance(tc_input, str) else tc_input
                    input_str = json.dumps(parsed, ensure_ascii=False)
                except json.JSONDecodeError:
                    input_str = str(tc_input)
                yield format_sse_event(
                    build_content_block_delta(tool_index, "tool_use_input", input_str),
                    "content_block_delta"
                )
                tool_index += 1

        # 处理 finish_reason
        if finish_reason in ("stop", "length"):
            stop_reason = "end_turn" if finish_reason == "stop" else "max_tokens"
            usage = chunk.get("usage", {})
            yield format_sse_event(
                build_message_delta_event(msg_id, stop_reason, usage),
                "message_delta"
            )
            yield format_sse_event(build_message_stop_event(), "message_stop")
```

- [ ] **Step 4: 运行测试验证通过**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/streaming_test.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: 提交**

```bash
git add src/streaming.py src/streaming_test.py
git commit -m "feat: SSE流式响应转换器 - 统一Anthropic事件格式"
```

---

### Task 2: 重构 Router 流式处理调用新模块

**Files:**
- Modify: `src/router.py:580-595`（在 OpenAI 流式响应处理处引入 streaming.py）
- Create: `src/streaming_test.py` 补充端到端测试

- [ ] **Step 1: 添加 streaming 导入和流式处理分支**

在 `router.py` 顶部 import 区域添加：

```python
from streaming import convert_openai_stream_to_anthropic
```

在 `anthropic_messages()` 函数中，OpenAI API 调用后（line ~580），替换为：

```python
# 检查是否流式请求
is_stream = body.get("stream", False)

if is_stream:
    # 流式响应路径
    response = await call_opencode(endpoint, openai_payload, api_key=custom_key, base_url=custom_base)
    if response.status_code != 200:
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

# 非流式路径（现有逻辑）
response = await call_opencode(endpoint, openai_payload, api_key=custom_key, base_url=custom_base)
# ... 其余逻辑不变
```

- [ ] **Step 2: 写端到端流式测试**

```python
def test_streaming_converts_openai_to_anthropic_events():
    """模拟 OpenAI 流式 chunk 转换为 Anthropic SSE 事件"""
    from streaming import (
        build_message_start_event,
        build_content_block_start,
        build_content_block_delta,
        build_message_delta_event,
        build_message_stop_event,
        format_sse_event
    )

    msg_id = "test-msg-123"
    model = "test-model"

    events = []
    events.append(format_sse_event(build_message_start_event(msg_id, model), "message_start"))
    events.append(format_sse_event(build_content_block_start(0, "text"), "content_block_start"))
    events.append(format_sse_event(build_content_block_delta(0, "text", "Hello"), "content_block_delta"))
    events.append(format_sse_event(build_content_block_delta(0, "text", " world"), "content_block_delta"))
    events.append(format_sse_event(build_message_delta_event(msg_id, "end_turn"), "message_delta"))
    events.append(format_sse_event(build_message_stop_event(), "message_stop"))

    # 验证事件数量
    assert len(events) == 6

    # 验证首个事件
    first_event = events[0]
    assert b"event: message_start" in first_event
    assert b"msg-123" in first_event
```

- [ ] **Step 3: 运行测试**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/streaming_test.py -v`
Expected: PASS (5 tests)

- [ ] **Step 4: 提交**

```bash
git add src/router.py src/streaming_test.py
git commit -m "refactor: 重构流式响应处理使用streaming.py转换器"
```