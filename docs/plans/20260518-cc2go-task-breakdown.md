# cc2go 任务分解 - 20260518

## 总览

| 提案 | 任务 | 文件 | 预估 | 依赖 |
|------|------|------|------|------|
| 提案1 | T1.1 写失败测试 | src/router_test.py | 1h | 无 |
| 提案1 | T1.2 修复转换顺序 | src/router.py | 1h | T1.1 |
| 提案4 | T2.1 写失败测试 | src/streaming_test.py | 1h | 无 |
| 提案4 | T2.2 实现累积器 | src/streaming.py | 1.5h | T2.1 |
| 提案5 | T3.1 写失败测试 | src/router_test.py | 1h | 无 |
| 提案5 | T3.2 添加 DEFAULT_MODELS | src/router.py | 0.5h | T3.1 |
| 提案5 | T3.3 实现模糊匹配函数 | src/router.py | 1h | T3.2 |
| 提案5 | T3.4 替换调用处 | src/router.py | 0.5h | T3.3 |

---

## Task T1.1: 提案1 - 写失败测试

**文件:** `src/router_test.py`
**预估:** 1小时
**依赖:** 无

### 任务描述

在 `TestConvertMessages` 类中添加测试用例 `test_text_and_tool_result_same_content_array`，验证当 user 消息的 content 数组同时包含 `text` 和 `tool_result` 时，text 内容应在 tool_result 消息之前。

### 实现步骤

1. 在 `TestConvertMessages` 类末尾添加测试方法：
```python
def test_text_and_tool_result_same_content_array(self):
    """当 user 消息 content 数组同时包含 text 和 tool_result 时，text 应在该 tool_result 消息之前"""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "请执行命令"},
            {
                "type": "tool_result",
                "tool_use_id": "call_001",
                "content": "命令输出"
            }
        ]
    }]
    result = convert_anthropic_messages_to_openai(messages)
    self.assertEqual(len(result), 2)
    self.assertEqual(result[0]["role"], "user")
    self.assertEqual(result[0]["content"], "请执行命令")
    self.assertEqual(result[1]["role"], "tool")
    self.assertEqual(result[1]["tool_call_id"], "call_001")
```

2. 运行测试确认失败：
```bash
python -m unittest src.router_test.TestConvertMessages.test_text_and_tool_result_same_content_array -v
```
**预期结果:** FAIL - 当前实现会丢失 text 或顺序错误

---

## Task T1.2: 提案1 - 修复转换顺序

**文件:** `src/router.py:397-417`
**预估:** 1小时
**依赖:** T1.1

### 任务描述

修改 `convert_anthropic_messages_to_openai` 函数中消息构建顺序：先 append 含 text 的消息，再 extend tool_results。

### 实现步骤

1. 将 `openai_messages.extend(tool_results)` 从 line 397 移到消息构建之后

2. 修改后的逻辑：
```python
# 先将 content_items 构建成消息并 append（仅当有内容时）
if content_items or tool_calls_list or reasoning_content:
    msg_dict = {"role": role}
    if content_items:
        if has_image:
            msg_dict["content"] = content_items
        else:
            texts = [c["text"] for c in content_items]
            msg_dict["content"] = "\n".join(texts)
    else:
        msg_dict["content"] = None
    if tool_calls_list:
        msg_dict["tool_calls"] = tool_calls_list
    if reasoning_content is not None:
        msg_dict["reasoning_content"] = reasoning_content
    elif tool_calls_list and role == "assistant":
        msg_dict["reasoning_content"] = ""
    openai_messages.append(msg_dict)

# 然后添加 tool_results（满足 OpenAI tool 消息紧跟 tool_calls 的要求）
openai_messages.extend(tool_results)
```

3. 运行测试确认通过：
```bash
python -m unittest src.router_test.TestConvertMessages.test_text_and_tool_result_same_content_array -v
```
**预期结果:** PASS

---

## Task T2.1: 提案4 - 写失败测试

**文件:** `src/streaming_test.py`
**预估:** 1小时
**依赖:** 无

### 任务描述

在 `streaming_test.py` 中添加测试用例 `test_input_json_delta_batching`，验证 input_json_delta 可以批量累积。

### 实现步骤

1. 在 `streaming_test.py` 中添加测试类：
```python
class TestStreamingBatching(unittest.TestCase):
    def test_input_json_delta_batching(self):
        """验证 input_json_delta 可以批量累积，仅在 block 结束时发送"""
        from streaming import convert_openai_stream_to_anthropic
        import asyncio

        mock_chunks = [
            '{"choices":[{"delta":{"tool_calls":[{"id":"tc_001","function":{"name":"web_search","arguments":"{"}}],"content":""}}]}',
            '{"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"q"}}]}}]}',
            '{"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"u"}}]}}]}',
            '{"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"e":"test"}]}}]}',
            '{"choices":[{"delta":{"tool_calls":[{"function":{"arguments":"}"}}]}}]}',
            '{"choices":[{"finish_reason":"tool_calls","delta":{}}]}',
        ]

        async def mock_aiter_lines():
            for chunk in mock_chunks:
                yield f"data: {chunk}"

        async def test():
            events = []
            async for event in convert_openai_stream_to_anthropic(mock_aiter_lines(), "test-model"):
                events.append(event)
            return events

        events = asyncio.run(test())
        delta_events = [e for e in events if b"input_json_delta" in e]
        self.assertEqual(len(delta_events), 1)
```

2. 运行测试确认失败：
```bash
python -m unittest src.streaming_test.TestStreamingBatching.test_input_json_delta_batching -v
```
**预期结果:** FAIL - 当前实现每个 delta 都单独 yield

---

## Task T2.2: 提案4 - 实现累积器

**文件:** `src/streaming.py:79-171`
**预估:** 1.5小时
**依赖:** T2.1

### 任务描述

修改 `convert_openai_stream_to_anthropic` 函数，为每个 tool_use block 维护输入累积器，仅在 block 结束时一次性发送 partial_json。

### 实现步骤

1. 在函数开头添加累积器：
```python
msg_id = f"msg_{uuid.uuid4().hex[:24]}"
block_index = 0
has_sent_message_start = False
current_block_type = None
tool_input_accumulator = {}
```

2. 修改 tool_calls 处理逻辑：替换 lines 134-154 的逻辑

3. 在 finish 时输出累积的完整 input_json_delta

4. 运行测试确认通过：
```bash
python -m unittest src.streaming_test.TestStreamingBatching.test_input_json_delta_batching -v
```
**预期结果:** PASS

---

## Task T3.1: 提案5 - 写失败测试

**文件:** `src/router_test.py`
**预估:** 1小时
**依赖:** 无

### 任务描述

在 `router_test.py` 中添加 `find_model_config` 函数的测试用例。

### 实现步骤

1. 添加测试方法：
```python
def test_find_model_config_exact_match(self):
    """精确匹配优先"""
    from router import find_model_config
    result = find_model_config("minimax-m2.7")
    self.assertIsNotNone(result)
    self.assertEqual(result["id"], "minimax-m2.7")

def test_find_model_config_fuzzy_match_sonnet(self):
    """带日期后缀的 sonnet 模型应能模糊匹配"""
    from router import find_model_config
    result = find_model_config("claude-sonnet-4-20250514")
    self.assertIsNotNone(result)

def test_find_model_config_fuzzy_match_haiku(self):
    """带日期后缀的 haiku 模型应能模糊匹配"""
    from router import find_model_config
    result = find_model_config("claude-haiku-3-20250514")
    self.assertIsNotNone(result)

def test_find_model_config_fuzzy_match_opus(self):
    """带日期后缀的 opus 模型应能模糊匹配"""
    from router import find_model_config
    result = find_model_config("claude-opus-4-20250514")
    self.assertIsNotNone(result)
```

2. 运行测试确认失败：
```bash
python -m unittest src.router_test.TestConvertMessages -v 2>&1 | head -50
```
**预期结果:** FAIL - `find_model_config` not defined

---

## Task T3.2: 提案5 - 添加 DEFAULT_MODELS 映射

**文件:** `src/router.py:78-91`
**预估:** 0.5小时
**依赖:** T3.1

### 任务描述

在 `DEFAULT_MODELS` 中添加 sonnet/haiku/opus 系列映射项。

### 实现步骤

1. 在 DEFAULT_MODELS 中添加：
```python
# Anthropic 模型模糊映射
"claude-sonnet-4": {"id": "claude-sonnet-4", "endpoint": "/v1/chat/completions"},
"claude-sonnet-3": {"id": "claude-sonnet-3", "endpoint": "/v1/chat/completions"},
"claude-haiku-3": {"id": "claude-haiku-3", "endpoint": "/v1/chat/completions"},
"claude-opus-4": {"id": "claude-opus-4", "endpoint": "/v1/chat/completions"},
"claude-opus-3": {"id": "claude-opus-3", "endpoint": "/v1/chat/completions"},
```

2. 运行 lint 检查：
```bash
ruff check src/router.py
```

---

## Task T3.3: 提案5 - 实现模糊匹配函数

**文件:** `src/router.py` (Config 类之前)
**预估:** 1小时
**依赖:** T3.2

### 任务描述

实现 `find_model_config(model_name)` 函数，支持精确匹配和模糊前缀匹配。

### 实现步骤

1. 在 Config 类之前添加函数：
```python
def find_model_config(model_name: str):
    """
    模糊模型查找：
    1. 精确匹配
    2. 提取系列前缀匹配（如 claude-sonnet-4-20250514 -> claude-sonnet-4）
    3. 查找 config.models 中的匹配
    """
    # 精确匹配
    if model_name in config.models:
        return config.models[model_name]

    # 模糊匹配
    import re
    fuzzy_match = re.match(r'^(claude-(?:sonnet|haiku|opus)-\d+)', model_name)
    if fuzzy_match:
        prefix = fuzzy_match.group(1)
        if prefix in config.models:
            return config.models[prefix]

    return None
```

2. 运行测试确认通过：
```bash
python -m unittest src.router_test.TestConvertMessages -v
```
**预期结果:** PASS

---

## Task T3.4: 提案5 - 替换调用处

**文件:** `src/router.py:705-727`, `src/router.py:894-895`
**预估:** 0.5小时
**依赖:** T3.3

### 任务描述

将模型查找调用处 `config.models.get(model_name)` 替换为 `find_model_config(model_name)`。

### 实现步骤

1. 修改 `/v1/messages` endpoint (约 line 705-711)：
```python
model_config = find_model_config(model_name)
```

2. 修改 `/v1/chat/completions` endpoint (约 line 894-895)：
```python
model_config = find_model_config(model)
```

3. 运行全量测试：
```bash
python -m unittest discover src -v
ruff check src/
```
**预期结果:** 全部 PASS

---

## 依赖关系图

```
T1.1 (写失败测试) ──> T1.2 (修复转换顺序)
                        │
T2.1 (写失败测试) ──> T2.2 (实现累积器)
                        │
T3.1 (写失败测试) ──> T3.2 (添加 DEFAULT_MODELS)
                        │
                        ├──> T3.3 (实现模糊匹配函数) ──> T3.4 (替换调用处)
```

---

## 验收标准

- [ ] 所有测试通过 `python -m unittest discover src -v`
- [ ] ruff lint 通过 `ruff check src/`
- [ ] 每个任务完成后提交一次 commit