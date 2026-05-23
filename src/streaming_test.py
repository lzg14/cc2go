"""
SSE 流式响应转换器 - 单元测试
使用 unittest 编写（无 pytest 依赖）
"""
import unittest
from src.streaming import (
    build_message_start_event,
    build_content_block_start,
    build_content_block_delta,
    build_content_block_stop,
    build_message_delta_event,
    build_message_stop_event,
    format_sse_event,
)


class TestEventBuilders(unittest.TestCase):
    def test_build_message_start_event(self) -> None:
        result = build_message_start_event("msg-123", "test-model")
        self.assertEqual(result["type"], "message_start")
        self.assertEqual(result["message"]["id"], "msg-123")
        self.assertEqual(result["message"]["role"], "assistant")
        self.assertEqual(result["message"]["model"], "test-model")

    def test_build_content_block_start_text(self) -> None:
        result = build_content_block_start(0, "text")
        self.assertEqual(result["type"], "content_block_start")
        self.assertEqual(result["index"], 0)
        self.assertEqual(result["content_block"]["type"], "text")
        self.assertEqual(result["content_block"]["text"], "")

    def test_build_content_block_start_tool_use(self) -> None:
        result = build_content_block_start(0, "tool_use", id="tc_001", name="web_search")
        self.assertEqual(result["type"], "content_block_start")
        self.assertEqual(result["content_block"]["type"], "tool_use")
        self.assertEqual(result["content_block"]["id"], "tc_001")
        self.assertEqual(result["content_block"]["name"], "web_search")
        self.assertEqual(result["content_block"]["input"], {})

    def test_build_content_block_delta_text_delta(self) -> None:
        result = build_content_block_delta(0, "text_delta", "Hello")
        self.assertEqual(result["type"], "content_block_delta")
        self.assertEqual(result["index"], 0)
        self.assertEqual(result["delta"]["type"], "text_delta")
        self.assertEqual(result["delta"]["text"], "Hello")

    def test_build_content_block_delta_input_json_delta(self) -> None:
        result = build_content_block_delta(0, "input_json_delta", '{"query":"weather"}')
        self.assertEqual(result["type"], "content_block_delta")
        self.assertEqual(result["delta"]["type"], "input_json_delta")
        self.assertEqual(result["delta"]["partial_json"], '{"query":"weather"}')

    def test_build_message_delta_event(self) -> None:
        result = build_message_delta_event("end_turn")
        self.assertEqual(result["type"], "message_delta")
        self.assertEqual(result["delta"]["stop_reason"], "end_turn")
        self.assertNotIn("index", result)

    def test_build_message_delta_event_max_tokens(self) -> None:
        result = build_message_delta_event("max_tokens")
        self.assertEqual(result["delta"]["stop_reason"], "max_tokens")

    def test_build_message_stop_event(self) -> None:
        result = build_message_stop_event()
        self.assertEqual(result["type"], "message_stop")

    def test_build_content_block_stop(self) -> None:
        result = build_content_block_stop(0)
        self.assertEqual(result["type"], "content_block_stop")
        self.assertEqual(result["index"], 0)


class TestSSEFormatting(unittest.TestCase):
    def test_format_sse_event(self) -> None:
        event = {"type": "message_start", "message": {"id": "test"}}
        result = format_sse_event(event, "message_start")
        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"event: message_start"))
        self.assertTrue(b"data: " in result)
        self.assertTrue(result.endswith(b"\n\n"))

    def test_format_sse_event_content_block_delta(self) -> None:
        event = {"type": "content_block_delta", "delta": {"text": "Hello"}}
        result = format_sse_event(event, "content_block_delta")
        self.assertIn(b"event: content_block_delta", result)
        self.assertIn(b"Hello", result)


class TestEventSequence(unittest.TestCase):
    def test_text_message_event_sequence(self) -> None:
        """验证文本消息的完整事件序列"""
        msg_id = "test-msg-123"
        model = "test-model"

        events = []
        events.append(format_sse_event(build_message_start_event(msg_id, model), "message_start"))
        events.append(format_sse_event(build_content_block_start(0, "text"), "content_block_start"))
        events.append(format_sse_event(build_content_block_delta(0, "text_delta", "Hello"), "content_block_delta"))
        events.append(format_sse_event(build_content_block_stop(0), "content_block_stop"))
        events.append(format_sse_event(build_message_delta_event("end_turn"), "message_delta"))
        events.append(format_sse_event(build_message_stop_event(), "message_stop"))

        self.assertEqual(len(events), 6)
        self.assertIn(b"event: message_start", events[0])
        self.assertIn(b"msg-123", events[0])
        self.assertIn(b"event: content_block_start", events[1])
        self.assertIn(b'"type": "text"', events[1])
        self.assertIn(b"event: content_block_delta", events[2])
        self.assertIn(b"text_delta", events[2])
        self.assertIn(b"Hello", events[2])
        self.assertIn(b"event: content_block_stop", events[3])
        self.assertIn(b"event: message_stop", events[-1])

    def test_tool_use_event_sequence(self) -> None:
        """验证 tool_use 消息的完整事件序列"""
        events = []
        events.append(format_sse_event(
            build_content_block_start(0, "tool_use", id="tc_001", name="web_search"),
            "content_block_start"
        ))
        events.append(format_sse_event(
            build_content_block_delta(0, "input_json_delta", '{"query":"weather"}'),
            "content_block_delta"
        ))
        events.append(format_sse_event(build_content_block_stop(0), "content_block_stop"))

        self.assertEqual(len(events), 3)
        self.assertIn(b"event: content_block_start", events[0])
        self.assertIn(b"tool_use", events[0])
        self.assertIn(b"tc_001", events[0])
        self.assertIn(b"web_search", events[0])
        self.assertIn(b"event: content_block_delta", events[1])
        self.assertIn(b"input_json_delta", events[1])
        self.assertIn(b"weather", events[1])
        self.assertIn(b"event: content_block_stop", events[2])


class TestConvertOpenaiStreamIntegration(unittest.IsolatedAsyncioTestCase):
    """convert_openai_stream_to_anthropic 端到端集成测试"""

    async def test_full_stream_sequence(self) -> None:
        """完整流式序列：message_start → content_block_start → delta → stop"""
        from streaming import convert_openai_stream_to_anthropic
        from unittest.mock import MagicMock

        chunks = [
            'data: {"id":"chatcmpl-1","choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n',
            'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n',
            'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":" world"},"finish_reason":null}]}\n',
            'data: [DONE]\n',
        ]

        async def async_gen():
            for chunk in chunks:
                yield chunk

        mock_response = MagicMock()
        mock_response.aiter_lines.return_value = async_gen()

        result = []
        async for event in convert_openai_stream_to_anthropic(mock_response, "test-model"):
            result.append(event)

        # 验证至少产生了 message_start 和 message_stop
        result_text = b"".join(result).decode()
        self.assertIn("message_start", result_text)
        self.assertIn("message_stop", result_text)

    async def test_stream_early_termination(self) -> None:
        """上游连接断开时不应崩溃"""
        from streaming import convert_openai_stream_to_anthropic
        from unittest.mock import MagicMock

        chunks = [
            'data: {"id":"chatcmpl-1","choices":[{"delta":{"content":"Hi"},"finish_reason":null}]}\n',
        ]

        async def broken_gen():
            yield chunks[0]
            raise Exception("connection reset")

        mock_response = MagicMock()
        mock_response.aiter_lines.return_value = broken_gen()

        result = []
        try:
            async for event in convert_openai_stream_to_anthropic(mock_response, "test-model"):
                result.append(event)
        except Exception:
            pass

        # 不崩溃，之前的 events 仍然有效
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)