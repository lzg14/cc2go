"""
SSE 流式响应转换器
将 OpenAI chat/completions 流式响应转换为 Anthropic SSE 格式
"""

import json
import uuid
from typing import AsyncGenerator, Dict


def build_message_start_event(msg_id: str, model: str) -> Dict:
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


def build_content_block_start(index: int, block_type: str = "text", **kwargs) -> Dict:
    content_block = {"type": block_type}
    if block_type == "text":
        content_block["text"] = ""
    elif block_type == "tool_use":
        content_block["id"] = kwargs.get("id", "")
        content_block["name"] = kwargs.get("name", "")
        content_block["input"] = {}
    return {
        "type": "content_block_start",
        "index": index,
        "content_block": content_block
    }


def build_content_block_delta(index: int, delta_type: str, content: str) -> Dict:
    delta = {}
    if delta_type == "text_delta":
        delta = {"type": "text_delta", "text": content}
    elif delta_type == "input_json_delta":
        delta = {"type": "input_json_delta", "partial_json": content}
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": delta
    }


def build_content_block_stop(index: int) -> Dict:
    return {"type": "content_block_stop", "index": index}


def build_message_delta_event(stop_reason: str = "end_turn", usage: Dict = None) -> Dict:
    return {
        "type": "message_delta",
        "delta": {
            "stop_reason": stop_reason,
            "stop_sequence": None
        },
        "usage": usage or {"input_tokens": 0, "output_tokens": 0}
    }


def build_message_stop_event() -> Dict:
    return {"type": "message_stop"}


def format_sse_event(event: Dict, event_type: str) -> bytes:
    data = json.dumps(event, ensure_ascii=False)
    return f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")


async def convert_openai_stream_to_anthropic(
    response,
    model: str
) -> AsyncGenerator[bytes, None]:
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    block_index = 0
    has_sent_message_start = False
    current_block_type = None
    # 按 block_index 累积 tool_call arguments（OpenAI 流式每个 chunk 发增量片断，
    # Anthropic input_json_delta 期望完整 partial_json）
    _args_accumulator: Dict[int, str] = {}

    async for line in response.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data: "):
            continue

        data_str = line[6:]
        if data_str in ("[DONE]", ""):
            if current_block_type is not None:
                yield format_sse_event(build_content_block_stop(block_index), "content_block_stop")
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

        content = delta.get("content", "")
        tool_calls = delta.get("tool_calls", [])

        if content:
            if current_block_type != "text":
                if current_block_type is not None:
                    yield format_sse_event(build_content_block_stop(block_index), "content_block_stop")
                    block_index += 1
                current_block_type = "text"
                yield format_sse_event(
                    build_content_block_start(block_index, "text"), "content_block_start"
                )
            yield format_sse_event(
                build_content_block_delta(block_index, "text_delta", content),
                "content_block_delta"
            )

        for tc in tool_calls:
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            tc_name = func.get("name", "")
            tc_input = func.get("arguments", "")

            if tc_id and tc_name:
                if current_block_type is not None:
                    yield format_sse_event(build_content_block_stop(block_index), "content_block_stop")
                    block_index += 1
                current_block_type = "tool_use"
                # 新 block 重置累积器
                _args_accumulator.pop(block_index, None)
                yield format_sse_event(
                    build_content_block_start(block_index, "tool_use", id=tc_id, name=tc_name),
                    "content_block_start"
                )

            if tc_input:
                prev = _args_accumulator.get(block_index, "")
                _args_accumulator[block_index] = prev + tc_input
                yield format_sse_event(
                    build_content_block_delta(block_index, "input_json_delta", _args_accumulator[block_index]),
                    "content_block_delta"
                )

        if finish_reason in ("stop", "length", "tool_calls"):
            if current_block_type is not None:
                yield format_sse_event(build_content_block_stop(block_index), "content_block_stop")
            stop_reason_map = {
                "stop": "end_turn",
                "length": "max_tokens",
                "tool_calls": "tool_use",
            }
            stop_reason = stop_reason_map.get(finish_reason, finish_reason)
            usage = chunk.get("usage", {})
            yield format_sse_event(
                build_message_delta_event(stop_reason, usage),
                "message_delta"
            )
            yield format_sse_event(build_message_stop_event(), "message_stop")
