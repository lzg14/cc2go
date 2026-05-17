"""
SSE 流式响应转换器 - 单元测试
使用 unittest 编写（无 pytest 依赖）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import unittest
from streaming import (
    build_message_start_event,
    build_content_block_start,
    build_content_block_delta,
    build_message_delta_event,
    build_message_stop_event,
    build_ping_event,
    format_sse_event,
)


class TestEventBuilders(unittest.TestCase):
    def test_build_message_start_event(self):
        result = build_message_start_event("msg-123", "test-model")
        self.assertEqual(result["type"], "message_start")
        self.assertEqual(result["message"]["id"], "msg-123")
        self.assertEqual(result["message"]["role"], "assistant")
        self.assertEqual(result["message"]["model"], "test-model")

    def test_build_content_block_start_text(self):
        result = build_content_block_start(0, "text")
        self.assertEqual(result["type"], "content_block_start")
        self.assertEqual(result["index"], 0)
        self.assertEqual(result["content_block"]["type"], "text")
        self.assertEqual(result["content_block"]["text"], "")

    def test_build_content_block_start_tool_use(self):
        result = build_content_block_start(0, "tool_use")
        self.assertEqual(result["type"], "content_block_start")
        self.assertEqual(result["content_block"]["type"], "tool_use")
        self.assertEqual(result["content_block"]["id"], "")
        self.assertEqual(result["content_block"]["name"], "")
        self.assertEqual(result["content_block"]["input"], {})

    def test_build_content_block_delta_text(self):
        result = build_content_block_delta(0, "text", "Hello")
        self.assertEqual(result["type"], "content_block_delta")
        self.assertEqual(result["index"], 0)
        self.assertEqual(result["delta"]["text"], "Hello")

    def test_build_content_block_delta_tool_use_id(self):
        result = build_content_block_delta(0, "tool_use_id", "tc_001")
        self.assertEqual(result["delta"]["id"], "tc_001")

    def test_build_content_block_delta_tool_use_name(self):
        result = build_content_block_delta(0, "tool_use_name", "web_search")
        self.assertEqual(result["delta"]["name"], "web_search")

    def test_build_content_block_delta_tool_use_input(self):
        result = build_content_block_delta(0, "tool_use_input", '{"query":"weather"}')
        self.assertEqual(result["delta"]["input"], '{"query":"weather"}')

    def test_build_message_delta_event(self):
        result = build_message_delta_event("msg-123", "end_turn")
        self.assertEqual(result["type"], "message_delta")
        self.assertEqual(result["stop_reason"], "end_turn")
        self.assertEqual(result["index"], 0)

    def test_build_message_delta_event_max_tokens(self):
        result = build_message_delta_event("msg-123", "max_tokens")
        self.assertEqual(result["stop_reason"], "max_tokens")

    def test_build_message_stop_event(self):
        result = build_message_stop_event()
        self.assertEqual(result["type"], "message_stop")

    def test_build_ping_event(self):
        result = build_ping_event(12345)
        self.assertEqual(result["type"], "ping")
        self.assertEqual(result["index"], 12345)


class TestSSEFormatting(unittest.TestCase):
    def test_format_sse_event(self):
        event = {"type": "message_start", "message": {"id": "test"}}
        result = format_sse_event(event, "message_start")
        self.assertIsInstance(result, bytes)
        self.assertTrue(result.startswith(b"event: message_start"))
        self.assertTrue(b"data: " in result)
        self.assertTrue(result.endswith(b"\n\n"))

    def test_format_sse_event_content_block_delta(self):
        event = {"type": "content_block_delta", "delta": {"text": "Hello"}}
        result = format_sse_event(event, "content_block_delta")
        self.assertIn(b"event: content_block_delta", result)
        self.assertIn(b"Hello", result)


class TestEventSequence(unittest.TestCase):
    def test_text_message_event_sequence(self):
        """验证文本消息的完整事件序列"""
        msg_id = "test-msg-123"
        model = "test-model"

        events = []
        events.append(format_sse_event(build_message_start_event(msg_id, model), "message_start"))
        events.append(format_sse_event(build_content_block_start(0, "text"), "content_block_start"))
        events.append(format_sse_event(build_content_block_delta(0, "text", "Hello"), "content_block_delta"))
        events.append(format_sse_event(build_content_block_delta(0, "text", " world"), "content_block_delta"))
        events.append(format_sse_event(build_message_delta_event(msg_id, "end_turn"), "message_delta"))
        events.append(format_sse_event(build_message_stop_event(), "message_stop"))

        self.assertEqual(len(events), 6)

        # 验证首个事件包含 message_start
        self.assertIn(b"event: message_start", events[0])
        self.assertIn(b"msg-123", events[0])

        # 验证 content_block_start
        self.assertIn(b"event: content_block_start", events[1])
        self.assertIn(b'"type": "text"', events[1])

        # 验证增量事件
        self.assertIn(b"event: content_block_delta", events[2])
        self.assertIn(b"Hello", events[2])

        # 验证最后一个事件是 message_stop
        self.assertIn(b"event: message_stop", events[-1])

    def test_tool_use_event_sequence(self):
        """验证 tool_use 消息的完整事件序列"""
        events = []
        events.append(format_sse_event(build_content_block_start(0, "tool_use"), "content_block_start"))
        events.append(format_sse_event(build_content_block_delta(0, "tool_use_id", "tc_001"), "content_block_delta"))
        events.append(format_sse_event(build_content_block_delta(0, "tool_use_name", "web_search"), "content_block_delta"))
        events.append(format_sse_event(build_content_block_delta(0, "tool_use_input", '{"query":"weather"}'), "content_block_delta"))

        self.assertEqual(len(events), 4)

        # 验证 tool_use 块
        self.assertIn(b"event: content_block_start", events[0])
        self.assertIn(b"tool_use", events[0])

        # 验证 id
        self.assertIn(b"tc_001", events[1])

        # 验证 name
        self.assertIn(b"web_search", events[2])

        # 验证 input
        self.assertIn(b"weather", events[3])


if __name__ == "__main__":
    unittest.main(verbosity=2)