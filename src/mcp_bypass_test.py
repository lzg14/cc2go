"""
MCP 工具短路模块 - 单元测试
使用 unittest 编写（无 pytest 依赖）
"""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from src.mcp_bypass import (
    should_bypass,
    should_tool_declaration_bypass,
    has_anthropic_builtin_tool,
    extract_query,
    BYPASS_TOOLS,
    find_bypass_tool_uses,
    apply_response_bypass,
)


class TestShouldBypass(unittest.TestCase):
    def test_should_not_bypass_no_tool_use(self) -> None:
        """仅 tools 参数中有 web_search 定义，没有实际 tool_use → 不短路"""
        body = {
            "model": "qwen3.6-plus",
            "messages": [{"role": "user", "content": "你好"}],
            "tools": [{"name": "web_search", "description": "Web search"}]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_not_bypass_no_messages(self) -> None:
        body = {
            "tools": [{"name": "web_search"}]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_not_bypass_empty_messages(self) -> None:
        body = {
            "messages": [],
            "tools": [{"name": "web_search"}]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_not_bypass_unknown_tool_use(self) -> None:
        body = {
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "name": "my_custom_tool", "input": {}}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_not_bypass_with_tool_result(self) -> None:
        """最后一条消息是 tool_result（用户已执行工具）→ 不应短路"""
        body = {
            "messages": [
                {"role": "user", "content": "搜索今天天气"},
                {"role": "assistant", "content": [{"type": "tool_use", "name": "web_search", "input": {"query": "今天天气"}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_1", "content": "..."}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (False, None))

    def test_should_bypass_last_msg_is_assistant(self) -> None:
        """最后一条消息是 assistant 且包含 tool_use: web_search → 短路"""
        body = {
            "messages": [
                {"role": "user", "content": "搜索今天天气"},
                {"role": "assistant", "content": [{"type": "tool_use", "name": "web_search", "input": {"query": "今天天气"}}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (True, "web_search"))

    def test_should_bypass_mcp_prefix_tool_use(self) -> None:
        """最后一条消息是 assistant，mcp__MiniMax__web_search 格式 → 短路"""
        body = {
            "messages": [
                {"role": "user", "content": "天气"},
                {"role": "assistant", "content": [{"type": "tool_use", "name": "mcp__MiniMax__web_search", "input": {"query": "天气"}}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (True, "web_search"))

    def test_should_bypass_function_style(self) -> None:
        """最后一条消息是 assistant，旧版 function 格式 → 短路"""
        body = {
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "name": "web_search", "input": {}}]}
            ]
        }
        result = should_bypass(body)
        self.assertEqual(result, (True, "web_search"))


class TestExtractQuery(unittest.TestCase):
    def test_extract_from_string_content(self) -> None:
        messages = [{"role": "user", "content": "今天天气怎么样"}]
        result = extract_query(messages)
        self.assertEqual(result, "今天天气怎么样")

    def test_extract_from_text_block(self) -> None:
        messages = [{"role": "user", "content": [{"type": "text", "text": "搜索 Python 教程"}]}]
        result = extract_query(messages)
        self.assertEqual(result, "搜索 Python 教程")

    def test_extract_from_last_user_message(self) -> None:
        messages = [
            {"role": "assistant", "content": "你好"},
            {"role": "user", "content": "明天会下雨吗"}
        ]
        result = extract_query(messages)
        self.assertEqual(result, "明天会下雨吗")

    def test_extract_empty_messages(self) -> None:
        result = extract_query([])
        self.assertEqual(result, "")

    def test_extract_empty_content(self) -> None:
        messages = [{"role": "user", "content": ""}]
        result = extract_query(messages)
        self.assertEqual(result, "")


class TestFindBypassToolUses(unittest.TestCase):
    """响应层 bypass：查找 tool_use 中的 bypass 工具"""

    def test_empty_content(self) -> None:
        result = find_bypass_tool_uses({})
        self.assertEqual(result, [])

    def test_content_not_list(self) -> None:
        result = find_bypass_tool_uses({"content": "just a string"})
        self.assertEqual(result, [])

    def test_text_only(self) -> None:
        resp = {"content": [{"type": "text", "text": "hello"}]}
        result = find_bypass_tool_uses(resp)
        self.assertEqual(result, [])

    def test_non_bypass_tool(self) -> None:
        """tool_use 名称不在 BYPASS_TOOLS 中 → 不返回"""
        resp = {"content": [{"type": "tool_use", "name": "my_custom_tool", "input": {}}]}
        result = find_bypass_tool_uses(resp)
        self.assertEqual(result, [])

    def test_bypass_web_search(self) -> None:
        resp = {"content": [{"type": "tool_use", "name": "web_search", "input": {"query": "天气"}}]}
        result = find_bypass_tool_uses(resp)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "web_search")
        self.assertEqual(result[0]["input"]["query"], "天气")

    def test_mcp_prefix_bypass(self) -> None:
        """mcp__MiniMax__web_search 也在 BYPASS_TOOLS 中"""
        resp = {"content": [{"type": "tool_use", "name": "mcp__MiniMax__web_search", "input": {"query": "天气"}}]}
        result = find_bypass_tool_uses(resp)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "mcp__MiniMax__web_search")

    def test_mixed_content(self) -> None:
        """text + 非 bypass tool_use + bypass tool_use → 只返回 bypass 项"""
        resp = {
            "content": [
                {"type": "text", "text": "让我搜索一下"},
                {"type": "tool_use", "name": "read_file", "input": {"path": "/tmp/x"}},
                {"type": "tool_use", "name": "web_search", "input": {"query": "新闻"}},
            ]
        }
        result = find_bypass_tool_uses(resp)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "web_search")

    def test_multiple_bypass(self) -> None:
        """多个 bypass tool_use → 全部返回"""
        resp = {
            "content": [
                {"type": "tool_use", "name": "web_search", "input": {"query": "A"}},
                {"type": "tool_use", "name": "web_search", "input": {"query": "B"}},
            ]
        }
        result = find_bypass_tool_uses(resp)
        self.assertEqual(len(result), 2)

    def test_tool_use_without_name(self) -> None:
        """tool_use 没有 name → 不匹配"""
        resp = {"content": [{"type": "tool_use", "input": {}}]}
        result = find_bypass_tool_uses(resp)
        self.assertEqual(result, [])


class TestApplyResponseBypass(unittest.TestCase):
    """响应层 bypass：替换 Anthropic 响应中的 tool_use"""

    def test_no_bypass_returns_original(self) -> None:
        """没有 bypass 项 → 原样返回"""
        resp = {"content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn"}
        result = asyncio.run(apply_response_bypass(resp))
        self.assertIs(result, resp)  # 同引用，未被修改

    def test_empty_content_returns_original(self) -> None:
        resp = {"content": [], "stop_reason": "end_turn"}
        result = asyncio.run(apply_response_bypass(resp))
        self.assertIs(result, resp)

    def test_non_bypass_tool_returns_original(self) -> None:
        resp = {"content": [{"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}}], "stop_reason": "tool_use"}
        result = asyncio.run(apply_response_bypass(resp))
        self.assertIs(result, resp)

    @patch("src.mcp_bypass.handle_bypass", new_callable=AsyncMock)
    def test_bypass_replaces_content_and_resets_stop_reason(self, mock_handle: AsyncMock) -> None:
        """bypass tool_use → content 被替换，stop_reason 改为 end_turn"""
        mock_handle.return_value = {
            "type": "message",
            "content": [{"type": "text", "text": "搜索结果：今天晴转多云"}],
        }
        resp = {
            "content": [{"type": "tool_use", "name": "web_search", "input": {"query": "天气"}}],
            "stop_reason": "tool_use",
        }
        result = asyncio.run(apply_response_bypass(resp))
        self.assertIsNot(result, resp)  # 返回新 dict
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(result["content"][0]["text"], "搜索结果：今天晴转多云")
        self.assertEqual(result["stop_reason"], "end_turn")
        mock_handle.assert_awaited_once_with("web_search", "天气")

    @patch("src.mcp_bypass.handle_bypass", new_callable=AsyncMock)
    def test_partial_bypass_preserves_non_bypass_items(self, mock_handle: AsyncMock) -> None:
        """部分 bypass：非 bypass 内容保持不变"""
        mock_handle.return_value = {
            "type": "message",
            "content": [{"type": "text", "text": "搜索结果"}],
        }
        resp = {
            "content": [
                {"type": "text", "text": "让我查查"},
                {"type": "tool_use", "name": "web_search", "input": {"query": "天气"}},
                {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}},
            ],
            "stop_reason": "tool_use",
        }
        result = asyncio.run(apply_response_bypass(resp))
        self.assertEqual(len(result["content"]), 3)
        # 第一条 text 不变
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(result["content"][0]["text"], "让我查查")
        # 第二条被替换为搜索结果
        self.assertEqual(result["content"][1]["type"], "text")
        self.assertEqual(result["content"][1]["text"], "搜索结果")
        # 第三条 bash 不变
        self.assertEqual(result["content"][2]["type"], "tool_use")
        self.assertEqual(result["content"][2]["name"], "bash")
        self.assertEqual(result["stop_reason"], "end_turn")


class TestToolDeclarationBypass(unittest.TestCase):
    def test_no_tools_returns_false(self) -> None:
        body = {"messages": [{"role": "user", "content": "hi"}]}
        result = should_tool_declaration_bypass(body)
        self.assertEqual(result, (False, None))

    def test_empty_tools_returns_false(self) -> None:
        body = {"messages": [{"role": "user", "content": "hi"}], "tools": []}
        result = should_tool_declaration_bypass(body)
        self.assertEqual(result, (False, None))

    def test_web_search_20250305_no_search_intent_does_not_bypass(self) -> None:
        """正常对话有 web_search 声明但不搜东西 → 不应 bypass"""
        body = {
            "messages": [{"role": "user", "content": "写一个 Python 脚本"}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
        }
        result = should_tool_declaration_bypass(body)
        self.assertEqual(result, (False, None))

    def test_web_search_20250305_with_search_intent_triggers_bypass(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "search for AI news"}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
        }
        bypass, tool_name = should_tool_declaration_bypass(body)
        self.assertTrue(bypass)
        self.assertEqual(tool_name, "web_search")

    def test_web_search_with_search_intent_triggers_bypass(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "search for latest technology"}],
            "tools": [{"name": "web_search", "description": "Web search tool"}]
        }
        bypass, tool_name = should_tool_declaration_bypass(body)
        self.assertTrue(bypass)
        self.assertEqual(tool_name, "web_search")

    def test_web_search_no_intent_does_not_bypass(self) -> None:
        """有 web_search 工具但用户说别的事 → 不应 bypass"""
        body = {
            "messages": [{"role": "user", "content": "解释一下量子计算"}],
            "tools": [{"name": "web_search", "description": "Web search tool"}]
        }
        result = should_tool_declaration_bypass(body)
        self.assertEqual(result, (False, None))

    def test_mcp_prefixed_bypass_tool_triggers_bypass(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "search for quantum computing"}],
            "tools": [{"name": "mcp__MiniMax__web_search", "description": "MCP search"}]
        }
        bypass, tool_name = should_tool_declaration_bypass(body)
        self.assertTrue(bypass)
        self.assertEqual(tool_name, "mcp__MiniMax__web_search")

    def test_non_bypass_tool_returns_false(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "write code"}],
            "tools": [{"name": "bash", "description": "Run bash commands"}]
        }
        result = should_tool_declaration_bypass(body)
        self.assertEqual(result, (False, None))

    def test_mixed_tools_finds_bypass(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "search for AI papers"}],
            "tools": [
                {"name": "bash", "description": "Run bash"},
                {"name": "web_search", "description": "Web search"}
            ]
        }
        bypass, tool_name = should_tool_declaration_bypass(body)
        self.assertTrue(bypass)
        self.assertEqual(tool_name, "web_search")

    def test_cn_search_intent_triggers_bypass(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "搜索一下最新的科技新闻"}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
        }
        bypass, tool_name = should_tool_declaration_bypass(body)
        self.assertTrue(bypass)
        self.assertEqual(tool_name, "web_search")

    def test_has_anthropic_builtin_tool_returns_true(self) -> None:
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}]
        }
        self.assertTrue(has_anthropic_builtin_tool(body))

    def test_has_anthropic_builtin_tool_no_tools_returns_false(self) -> None:
        body = {"messages": [{"role": "user", "content": "hi"}]}
        self.assertFalse(has_anthropic_builtin_tool(body))


class TestBypassToolsConfig(unittest.TestCase):
    def test_bypass_tools_contains_expected_tools(self) -> None:
        self.assertIn("web_search", BYPASS_TOOLS)
        self.assertIn("mcp__MiniMax__web_search", BYPASS_TOOLS)

    def test_bypass_tool_has_mmx_type(self) -> None:
        for tool_name, handler in BYPASS_TOOLS.items():
            self.assertEqual(handler["type"], "mmx")


if __name__ == "__main__":
    unittest.main(verbosity=2)