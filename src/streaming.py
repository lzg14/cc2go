"""
SSE 流式响应转换器
将 OpenAI chat/completions 流式响应转换为 Anthropic SSE 格式
"""

import json
import uuid
from typing import AsyncGenerator, Dict


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
        delta["input"] = content
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
    response,
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

    async for line in response.aiter_lines():
        line = line.strip()
        if not line:
            continue

        if not line.startswith("data: "):
            continue

        data_str = line[6:]
        if data_str in ("[DONE]", ""):
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

        if not has_sent_message_start:
            has_sent_message_start = True
            yield format_sse_event(build_message_start_event(msg_id, model), "message_start")
            yield format_sse_event(build_content_block_start(content_index, "text"), "content_block_start")

        content = delta.get("content", "")
        if content:
            yield format_sse_event(
                build_content_block_delta(content_index, "text", content),
                "content_block_delta"
            )

        tool_calls = delta.get("tool_calls", [])
        for tc in tool_calls:
            func = tc.get("function", {})
            tc_id = tc.get("id", "")
            tc_name = func.get("name", "")
            tc_input = func.get("arguments", "")

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

        if finish_reason in ("stop", "length"):
            stop_reason = "end_turn" if finish_reason == "stop" else "max_tokens"
            usage = chunk.get("usage", {})
            yield format_sse_event(
                build_message_delta_event(msg_id, stop_reason, usage),
                "message_delta"
            )
            yield format_sse_event(build_message_stop_event(), "message_stop")