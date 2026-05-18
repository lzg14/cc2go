# cc2go 功能增强实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复三个独立功能增强：text+tool_result 同数组时 text 被丢弃、流式响应 input_json_delta 拼接优化、haiku/sonnet/opus 模糊模型映射

**Architecture:** 三个提案互相独立，各自在对应文件中做最小改动。Proposal 1 修复消息转换逻辑顺序，Proposal 4 优化拼接性能，Proposal 5 扩展模型查找函数

**Tech Stack:** Python 3, unittest, httpx

---

## 任务总览

| 提案 | 大小 | 文件 |
|------|------|------|
| 提案1: text + tool_result 同数组 text 被丢弃 | XS | src/router.py:398 |
| 提案4: input_json_delta 拼接优化 | XS | src/streaming.py:150-154 |
| 提案5: haiku/sonnet/opus 模糊模型映射 | S | src/router.py:705-711 |

---

## Task 1: 提案1 - 修复 text + tool_result 同数组时 text 被丢弃

**问题根因：** `convert_anthropic_messages_to_openai` 在处理 content 数组时，先遍历所有 item：text 放入 `content_items`，tool_result 放入 `tool_results`。然后在 line 398 执行 `openai_messages.extend(tool_results)`，将 tool_results 添加到 openai_messages。之后在 line 401-417 才将 `content_items` 构建成一条消息并 append。这意味着当同一 content 数组中同时有 text 和 tool_result 时，text 消息会被放在 tool_results 之后，导致顺序错误。

**修复方案：** 调整顺序：先构建含 text 的消息并 append，再 extend tool_results

**Files:**
- Modify: `src/router.py:397-417`
- Test: `src/router_test.py`

- [ ] **Step 1: 写失败测试**

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
    # 期望两条消息：text 在前，tool 在后
    self.assertEqual(len(result), 2)
    # 第一条是 text（role=user，content="请执行命令"）
    self.assertEqual(result[0]["role"], "user")
    self.assertEqual(result[0]["content"], "请执行命令")
    # 第二条是 tool_result
    self.assertEqual(result[1]["role"], "tool")
    self.assertEqual(result[1]["tool_call_id"], "call_001")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest src.router_test.TestConvertMessages.test_text_and_tool_result_same_content_array -v`
Expected: FAIL - 第二条消息的 content 是 "命令输出" 但顺序错误，或 text 被丢失

- [ ] **Step 3: 写最小修复**

修改 `src/router.py` lines 397-417：

**原来的代码（有问题）：**
```python
            # 添加 tool 结果（必须在用户文本之前，满足 OpenAI tool 消息紧跟 tool_calls 的要求）
            openai_messages.extend(tool_results)

            # 合并 content_items 和 tool_calls 到一条消息
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
```

**修复后的代码：**
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

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest src.router_test.TestConvertMessages.test_text_and_tool_result_same_content_array -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/router.py src/router_test.py
git commit -m "fix: 修复 text + tool_result 同数组时 text 被丢弃的问题

当 user 消息 content 数组同时包含 text 和 tool_result 时，
text 内容应在该 tool_result 消息之前。调整添加顺序：
先 append 含 text 的消息，再 extend tool_results。
"
```

---

## Task 2: 提案4 - 流式响应 input_json_delta 拼接优化

**问题：** `streaming.py` 的 `convert_openai_stream_to_anthropic` 函数中，对 tool_call 的 `input_json_delta` 是通过每次调用 `build_content_block_delta` 并直接 yield 拼接。在极端细粒度分片（如每个字符一个 chunk）时，频繁的函数调用和 SSE 编码有一定性能开销。

**修复方案：** 在 `convert_openai_stream_to_anthropic` 中为每个 tool_use block 维护一个累积器（dict），仅在 block 结束时一次性生成 `input_json_delta` event，减少函数调用和 yield 次数。

**Files:**
- Modify: `src/streaming.py:79-171`
- Test: `src/streaming_test.py`

- [ ] **Step 1: 写失败测试**

```python
def test_input_json_delta_batching(self):
    """验证 input_json_delta 可以批量累积，仅在 block 结束时发送"""
    # 这个测试验证累积逻辑存在，但不验证极端分片性能（那是微优化）
    # 验证当累积器有内容时，在 block stop 前不会发送中间 partial_json
    from streaming import convert_openai_stream_to_anthropic
    import asyncio

    # 构造一个模拟的流式响应，每个字符一个 chunk
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
    # 统计 input_json_delta 事件数量
    delta_events = [e for e in events if b"input_json_delta" in e]
    # 累积优化后，应该只有1个 input_json_delta 而不是5个
    self.assertEqual(len(delta_events), 1)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest src.streaming_test.TestStreamingBatching.test_input_json_delta_batching -v`
Expected: FAIL（当前实现每个 delta 都单独 yield）

- [ ] **Step 3: 写最小修复**

修改 `src/streaming.py` 的 `convert_openai_stream_to_anthropic` 函数：

在函数开头添加累积器：
```python
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    block_index = 0
    has_sent_message_start = False
    current_block_type = None
    # 累积器：index -> {"name": "", "input": ""}
    tool_input_accumulator = {}
```

修改 tool_calls 处理部分（替换原来的 lines 134-154）：
```python
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
                tool_input_accumulator[block_index] = {"name": tc_name, "input": tc_input}
                yield format_sse_event(
                    build_content_block_start(block_index, "tool_use", id=tc_id, name=tc_name),
                    "content_block_start"
                )
            elif tc_input:
                # 累积到现有 block
                if block_index in tool_input_accumulator:
                    tool_input_accumulator[block_index]["input"] += tc_input

        # 在 finish 时输出累积的完整 input_json_delta
        if finish_reason in ("stop", "length", "tool_calls"):
            if current_block_type is not None:
                # 输出累积的 input
                if block_index in tool_input_accumulator:
                    accumulated_input = tool_input_accumulator[block_index]["input"]
                    yield format_sse_event(
                        build_content_block_delta(block_index, "input_json_delta", accumulated_input),
                        "content_block_delta"
                    )
                yield format_sse_event(build_content_block_stop(block_index), "content_block_stop")
                tool_input_accumulator.pop(block_index, None)
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest src.streaming_test.TestStreamingBatching.test_input_json_delta_batching -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/streaming.py src/streaming_test.py
git commit -m "feat: 优化流式响应 input_json_delta 累积策略

在 convert_openai_stream_to_anthropic 中为每个 tool_use block
维护输入累积器，仅在 block 结束时一次性发送 partial_json，
减少极端细粒度分片时的函数调用开销。
"
```

---

## Task 3: 提案5 - haiku/sonnet/opus 模糊模型映射

**问题：** 当前 `router.py` 的模型查找（line 705-711）只做精确匹配。用户使用带日期后缀的模型名（如 `claude-sonnet-4-20250514`）时会匹配失败。

**修复方案：** 添加一个 `find_model_config(model_name)` 函数，对模型名进行模糊匹配：
1. 先精确匹配
2. 若未命中，尝试从模型名中提取系列前缀（如从 `claude-sonnet-4-20250514` 提取 `claude-sonnet-4`）
3. 在已知模型列表（DEFAULT_MODELS keys）中查找是否有该前缀
4. 若找到，返回对应的配置

**Files:**
- Modify: `src/router.py:78-91` (DEFAULT_MODELS), `src/router.py:705-727` (model lookup)
- Test: `src/router_test.py`

- [ ] **Step 1: 写失败测试**

```python
def test_fuzzy_model_mapping_sonnet_with_date_suffix(self):
    """claude-sonnet-4-20250514 应匹配到 sonnet 模型配置"""
    # 模拟有 sonnet 系列前缀的配置
    from router import DEFAULT_MODELS, Config
    # 需要先验证 DEFAULT_MODELS 中有 sonnet 系列
    # 注意：当前 DEFAULT_MODELS 没有 sonnet，需要先确认测试环境
    pass

def test_find_model_config_exact_match(self):
    """精确匹配优先"""
    from router import find_model_config
    # 假设 DEFAULT_MODELS 有 "minimax-m2.7"
    result = find_model_config("minimax-m2.7")
    self.assertIsNotNone(result)
    self.assertEqual(result["id"], "minimax-m2.7")

def test_find_model_config_fuzzy_match_sonnet(self):
    """带日期后缀的 sonnet 模型应能模糊匹配"""
    from router import find_model_config
    # 模糊匹配：claude-sonnet-4-20250514 -> claude-sonnet-4
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

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest src.router_test -v 2>&1 | head -50`
Expected: FAIL - `find_model_config` not defined

- [ ] **Step 3: 写最小修复**

**Step 3a: 添加 DEFAULT_MODELS 中的 sonnet/haiku/opus 映射**

在 `src/router.py` 的 DEFAULT_MODELS 中添加（如果不存在）：

```python
DEFAULT_MODELS = {
    # ... 现有模型 ...
    # Anthropic 模型模糊映射（提案5）
    "claude-sonnet-4": {"id": "claude-sonnet-4", "endpoint": "/v1/chat/completions"},
    "claude-sonnet-3": {"id": "claude-sonnet-3", "endpoint": "/v1/chat/completions"},
    "claude-haiku-3": {"id": "claude-haiku-3", "endpoint": "/v1/chat/completions"},
    "claude-opus-4": {"id": "claude-opus-4", "endpoint": "/v1/chat/completions"},
    "claude-opus-3": {"id": "claude-opus-3", "endpoint": "/v1/chat/completions"},
}
```

**Step 3b: 添加 find_model_config 函数**

在 `src/router.py` 中添加（在 Config 类之前）：

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

    # 模糊匹配：从模型名提取前缀
    # 匹配模式：claude-{family}-{version}-{date}
    # 例如 claude-sonnet-4-20250514 -> claude-sonnet-4
    import re
    fuzzy_match = re.match(r'^(claude-(?:sonnet|haiku|opus)-\d+)', model_name)
    if fuzzy_match:
        prefix = fuzzy_match.group(1)
        if prefix in config.models:
            return config.models[prefix]

    return None
```

**Step 3c: 修改模型查找调用处**

修改 `src/router.py` 的 `/v1/messages` endpoint 中的模型查找（约 line 705-711）：

**原来的代码：**
```python
model_config = config.models.get(model_name)
if model_config:
    model_id = model_config["id"]
    endpoint = model_config["endpoint"]
else:
    model_id = model_name
    endpoint = "/v1/chat/completions"
```

**修复后的代码：**
```python
model_config = find_model_config(model_name)
if model_config:
    model_id = model_config["id"]
    endpoint = model_config["endpoint"]
else:
    model_id = model_name
    endpoint = "/v1/chat/completions"
```

同样修改 `/v1/chat/completions` endpoint（约 line 894-895）：
```python
model_config = find_model_config(model)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m unittest src.router_test.TestConvertMessages -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/router.py src/router_test.py
git commit -m "feat: 添加 haiku/sonnet/opus 模糊模型映射

find_model_config() 函数支持：
1. 精确匹配模型名
2. 从带日期后缀的模型名（如 claude-sonnet-4-20250514）
   提取系列前缀进行模糊匹配

DEFAULT_MODELS 新增 claude-sonnet-4, claude-sonnet-3,
claude-haiku-3, claude-opus-4, claude-opus-3 映射项。
"
```

---

## 验收标准

### 提案1 (XS)
- [ ] `test_text_and_tool_result_same_content_array` 通过
- [ ] text 内容在 tool_result 消息之前（顺序正确）
- [ ] 现有所有 router_test 通过

### 提案4 (XS)
- [ ] `test_input_json_delta_batching` 通过
- [ ] 累积优化后每个 tool_call 只产生一个 input_json_delta 事件
- [ ] 现有所有 streaming_test 通过

### 提案5 (S)
- [ ] `test_find_model_config_exact_match` 通过
- [ ] `test_find_model_config_fuzzy_match_sonnet/haiku/opus` 通过
- [ ] `claude-sonnet-4-20250514` 能正确匹配到 `claude-sonnet-4` 配置
- [ ] 现有所有 router_test 通过

### 全局
- [ ] 所有测试通过 (`python -m unittest discover src -v`)
- [ ] ruff lint 通过 (`ruff check src/`)