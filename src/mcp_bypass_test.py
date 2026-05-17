import pytest
from mcp_bypass import should_bypass


def test_should_bypass_websearch():
    """检测到 web_search 工具时应返回 bypass"""
    body = {
        "model": "qwen3.6-plus",
        "messages": [{"role": "user", "content": "搜索今天天气"}],
        "tools": [{"name": "web_search", "description": "Web search"}]
    }
    result = should_bypass(body)
    assert result == (True, "web_search")


def test_should_not_bypass_no_tools():
    """普通对话不应短路"""
    body = {
        "model": "qwen3.6-plus",
        "messages": [{"role": "user", "content": "你好"}]
    }
    result = should_bypass(body)
    assert result == (False, None)


def test_should_not_bypass_unknown_tool():
    """未知工具名不应短路"""
    body = {
        "model": "qwen3.6-plus",
        "messages": [{"role": "user", "content": "你好"}],
        "tools": [{"name": "my_custom_tool"}]
    }
    result = should_bypass(body)
    assert result == (False, None)


def test_should_bypass_mmx_underscore_format():
    """MCP 格式工具名应识别并归一化"""
    body = {
        "tools": [{"name": "mcp__MiniMax__web_search"}]
    }
    result = should_bypass(body)
    assert result == (True, "web_search")


def test_should_bypass_function_style_tool():
    """函数格式工具名应正确识别"""
    body = {
        "tools": [{"function": {"name": "web_search", "description": "Web search"}}]
    }
    result = should_bypass(body)
    assert result == (True, "web_search")


def test_should_not_bypass_empty_tools():
    """空 tools 列表不应短路"""
    body = {
        "model": "qwen3.6-plus",
        "messages": [{"role": "user", "content": "你好"}],
        "tools": []
    }
    result = should_bypass(body)
    assert result == (False, None)