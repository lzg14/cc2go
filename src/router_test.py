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

    # ---- 以下是从 error-archive 实际错误场景回归的测试 ----

    def test_assistant_empty_content_with_tool_calls_not_dropped(self):
        """assistant content='' 带 tool_calls 不应被丢弃（DeepSeek 400 错误根因）"""
        messages = [{
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_00", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
                {"id": "call_01", "type": "function", "function": {"name": "read", "arguments": "{}"}}
            ]
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1, "assistant empty content + tool_calls 消息不应被丢弃")
        self.assertEqual(result[0]["role"], "assistant")
        self.assertIn("tool_calls", result[0])
        self.assertEqual(len(result[0]["tool_calls"]), 2)

    def test_assistant_empty_content_preserved(self):
        """assistant content='' 无 tool_calls 时也应保留，避免消息序列断裂"""
        messages = [{"role": "assistant", "content": ""}]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1, "空 content 的 assistant 消息不应被丢弃")
        self.assertEqual(result[0]["role"], "assistant")

    def test_tool_message_with_tool_call_id(self):
        """已是 OpenAI 格式的 tool 消息应直接透传"""
        messages = [{
            "role": "tool",
            "content": "command output",
            "tool_call_id": "call_00"
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["role"], "tool")
        self.assertEqual(result[0]["tool_call_id"], "call_00")
        self.assertEqual(result[0]["content"], "command output")

    def test_assistant_with_reasoning_content_openai_format(self):
        """已是 OpenAI 格式的 assistant 消息带 reasoning_content 应保留"""
        messages = [{
            "role": "assistant",
            "content": "Here is my answer",
            "reasoning_content": "I thought about this carefully"
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["reasoning_content"], "I thought about this carefully")

    def test_assistant_empty_content_with_reasoning_content(self):
        """assistant content='' + reasoning_content 不应被丢弃"""
        messages = [{
            "role": "assistant",
            "content": "",
            "reasoning_content": "Deep thinking process"
        }]
        result = convert_anthropic_messages_to_openai(messages)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["reasoning_content"], "Deep thinking process")

    def test_multi_turn_conversation_with_empty_assistant(self):
        """多轮对话中 assistant 空 content + tool_calls 的完整消息序列"""
        messages = [
            {"role": "user", "content": "Check the logs"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_00", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"ls\"}"}}
            ]},
            {"role": "tool", "content": "file1.txt\nfile2.txt", "tool_call_id": "call_00"},
            {"role": "assistant", "content": [{"type": "text", "text": "Found the files"}]}
        ]
        result = convert_anthropic_messages_to_openai(messages)
        # 应该有 4 条消息，assistant 空 content 那条不应被丢弃
        self.assertEqual(len(result), 4, "4条消息都应保留，assistant 空 content 不应被丢弃")
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[1]["role"], "assistant")
        self.assertIn("tool_calls", result[1])
        self.assertEqual(result[2]["role"], "tool")
        self.assertEqual(result[3]["role"], "assistant")


if __name__ == "__main__":
    unittest.main(verbosity=2)
