"""
路由器核心格式转换 - 单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import unittest
from router import convert_anthropic_messages_to_openai


class TestConvertMessages(unittest.TestCase):
    """convert_anthropic_messages_to_openai 格式转换测试"""

    def test_text_message(self):
        messages = [{"role": "user", "content": "hello"}]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[0]["content"], "hello")

    def test_thinking_converted_to_reasoning_content(self):
        messages = [{
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me think step by step"},
                {"type": "text", "text": "Here is the answer"}
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "assistant")
        self.assertEqual(result[0]["content"], "Here is the answer")
        self.assertEqual(result[0]["reasoning_content"], "Let me think step by step")

    def test_thinking_without_text(self):
        messages = [{
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Just thinking"}
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "assistant")
        self.assertEqual(result[0].get("content"), None)
        self.assertEqual(result[0]["reasoning_content"], "Just thinking")

    def test_no_thinking_no_reasoning_content(self):
        messages = [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "direct answer"}
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertNotIn("reasoning_content", result[0])
        self.assertEqual(result[0]["content"], "direct answer")

    def test_thinking_only_on_assistant_role(self):
        messages = [{
            "role": "user",
            "content": [
                {"type": "thinking", "thinking": "user thinking"}
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertNotIn("reasoning_content", result[0])
        self.assertEqual(result[0].get("content"), "")

    def test_tool_use_conversion(self):
        messages = [{
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_123",
                    "name": "bash",
                    "input": {"command": "ls"}
                }
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "assistant")
        self.assertEqual(result[0]["content"], None)
        self.assertIn("tool_calls", result[0])
        self.assertEqual(result[0]["tool_calls"][0]["id"], "call_123")
        self.assertEqual(result[0]["tool_calls"][0]["function"]["name"], "bash")

    def test_tool_result_conversion(self):
        messages = [{
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_123",
                    "content": "output text"
                }
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "tool")
        self.assertEqual(result[0]["tool_call_id"], "call_123")
        self.assertEqual(result[0]["content"], "output text")

    def test_thinking_and_tool_use_mixed(self):
        messages = [{
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "I need to search"},
                {"type": "text", "text": "Let me look that up"},
                {
                    "type": "tool_use",
                    "id": "call_search",
                    "name": "web_search",
                    "input": {"query": "test"}
                }
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "assistant")
        self.assertEqual(result[0]["reasoning_content"], "I need to search")
        self.assertEqual(result[0]["content"], "Let me look that up")
        self.assertEqual(len(result[0]["tool_calls"]), 1)

    def test_system_reminder_stripped(self):
        messages = [{
            "role": "user",
            "content": "hello <system-reminder>some reminder</system-reminder> world"
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(result[0]["content"], "hello  world")


if __name__ == "__main__":
    unittest.main(verbosity=2)
