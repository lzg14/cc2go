"""
MCP 工具短路模块 - 单元测试
使用 unittest 编写（无 pytest 依赖）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import unittest
from mcp_bypass import should_bypass, extract_query, BYPASS_TOOLS


class TestShouldBypass(unittest.TestCase):
    def test_should_not_bypass_no_tool_use(self):
        """仅 tools 参数中有 web_search 定义，没有实际 tool_use → 不短路"""
        body = {
            "model": "qwen3.6-plus",
            "messages": [{"role": "user", "content": "你好"}],
            "tools": [{"name": "web_search", "description": "Web search"}]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_not_bypass_no_messages(self):
        body = {
            "tools": [{"name": "web_search"}]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_not_bypass_empty_messages(self):
        body = {
            "messages": [],
            "tools": [{"name": "web_search"}]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_not_bypass_unknown_tool_use(self):
        body = {
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "name": "my_custom_tool", "input": {}}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_bypass_tool_use_in_assistant(self):
        """assistant 消息中有 tool_use: web_search → 短路"""
        body = {
            "messages": [
                {"role": "user", "content": "搜索今天天气"},
                {"role": "assistant", "content": [{"type": "tool_use", "name": "web_search", "input": {"query": "今天天气"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "..."}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (True, "web_search"))

    def test_should_bypass_mcp_prefix_tool_use(self):
        """mcp__MiniMax__web_search 格式的 tool_use → 短路"""
        body = {
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "name": "mcp__MiniMax__web_search", "input": {"query": "天气"}}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (True, "web_search"))

    def test_should_bypass_function_style(self):
        """旧版 function 格式（罕见，但兼容）"""
        body = {
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "name": "web_search", "input": {}}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (True, "web_search"))


class TestExtractQuery(unittest.TestCase):
    def test_extract_from_string_content(self):
        messages = [{"role": "user", "content": "今天天气怎么样"}]
        result = extract_query(messages)
        self.assertEqual(result, "今天天气怎么样")

    def test_extract_from_text_block(self):
        messages = [{"role": "user", "content": [{"type": "text", "text": "搜索 Python 教程"}]}]
        result = extract_query(messages)
        self.assertEqual(result, "搜索 Python 教程")

    def test_extract_from_last_user_message(self):
        messages = [
            {"role": "assistant", "content": "你好"},
            {"role": "user", "content": "明天会下雨吗"}
        ]
        result = extract_query(messages)
        self.assertEqual(result, "明天会下雨吗")

    def test_extract_empty_messages(self):
        result = extract_query([])
        self.assertEqual(result, "")

    def test_extract_empty_content(self):
        messages = [{"role": "user", "content": ""}]
        result = extract_query(messages)
        self.assertEqual(result, "")


class TestBypassToolsConfig(unittest.TestCase):
    def test_bypass_tools_contains_expected_tools(self):
        self.assertIn("web_search", BYPASS_TOOLS)
        self.assertIn("mcp__MiniMax__web_search", BYPASS_TOOLS)

    def test_bypass_tool_has_mmx_type(self):
        for tool_name, handler in BYPASS_TOOLS.items():
            self.assertEqual(handler["type"], "mmx")


if __name__ == "__main__":
    unittest.main(verbosity=2)