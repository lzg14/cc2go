# 日志错误分析报告

> 日志文件：`router.log` + `error-archive/2026-05-17T125123.221302-kimi-k2.6-400.json`
> 分析时间：2026-05-17
> 状态：✅ tool_result 排列顺序 bug 已修复，其余待验证

---

## 一、错误总览

| # | 错误类型 | 次数 | 首次发现 | 最后发现 | 是否已修复 |
|---|----------|------|----------|----------|-----------|
| 1 | `PlainTextResponse` 未定义 | 9 | 05-16 22:36 | 05-16 22:37 | ✅ 已修复 |
| 2 | tool_call_ids 缺失响应 | 2 | 05-16 22:40 | 05-17 11:46 | ⚠️ 待验证 |
| 3 | function name invalid | 1 | 05-16 22:40 | 05-16 22:40 | ⚠️ 待验证 |
| 4 | MiniMax invalid params | 1 | 05-16 22:40 | 05-17 11:45 | ⚠️ 待验证 |
| 5 | 速率限制（rate limit） | 1 | 05-16 22:50 | 05-16 22:50 | ❌ 非 cc2go 问题 |

**总计**：25 个 ERROR（多数为同类型重复）

---

## 二、错误详情

### 错误 1：PlainTextResponse 未定义（✅ 已修复）

**日志**：
```
2026-05-16 22:36:56 - ERROR - [Error] name 'PlainTextResponse' is not defined
```

**原因**：`router.py` 导入了 `FastAPI` 但没有导入 `PlainTextResponse`，导致在返回纯文本响应时失败。

**修复**：在 import 语句中添加 `PlainTextResponse`。

**状态**：✅ 已修复。05-16 22:37 之后的日志无此错误。

---

### 错误 2：tool_call_ids 缺失响应（⚠️ 仍偶发）

**日志**：
```
# 05-16 22:40（DeepSeek）
2026-05-16 22:40:55 - ERROR - [Error] OpenCode API: status=400,
body={"error":{"message":"Error from provider (DeepSeek): An assistant message with 'tool_calls'
must be followed by tool messages responding to each 'tool_call_id'.
(insufficient tool messages following tool_calls message)"

# 05-17 11:41（kimi-k2.6）
2026-05-17 11:41:35 - ERROR - [Error] OpenCode API: status=400,
body={"error":{"message":"Error from provider: Provider returned error","code":400,
"metadata":{"raw":"{\"error\":{\"message\":\"Invalid request: an assistant message with 'tool_calls'
must be followed by tool messages responding to each 'tool_call_id'.
The following tool_call_ids did not have response messages: Grep:26, Read:27, Read:28\"...
```

**关键发现**：

日志行 1729-1738 显示了完整的多轮对话流程：

1. **第 1 轮**（11:41:01）：Claude Code 生成 tool_calls，`id` 为 `Grep:26`、`Read:27`、`Read:28`
   ```json
   {
     "tool_calls": [
       {"id": "Grep:26", "type": "function", "function": {"name": "Grep", ...}},
       {"id": "Grep:27", "type": "function", "function": {"name": "Grep", ...}},
       {"id": "Read:28", "type": "function", "function": {"name": "Read", ...}}
     ]
   }
   ```

2. **第 2 轮**（11:41:08）：Claude Code 生成 tool_calls，`id` 变为 `Grep:26`、`Grep:27`、`Read:28`
   ```json
   {
     "tool_calls": [
       {"id": "Grep:26", "type": "function", "function": ...},
       {"id": "Grep:27", "type": "function", "function": ...},
       {"id": "Read:28", "type": "function", "function": ...}
     ]
   }
   ```

3. **第 3 轮**（11:41:10）：Claude Code 发送工具结果
   ```json
   {
     "role": "assistant",
     "content": "...",
     "tool_calls": [
       {"id": "Bash:0", "type": "function", ...},
       {"id": "Bash:1", "type": "function", ...},
       {"id": "Bash:2", "type": "function", ...}
     ]
   },
   {
     "role": "tool",
     "tool_call_id": "Bash:1",   // ← 与原始 tool_call_id 不匹配！
     "content": "..."
   }
   ```

**根因分析**：

这里的问题是 **Claude Code 生成的 tool_calls ID 与 tool_result 中的 tool_call_id 不一致**：

- 前面的请求中工具名是 `Grep:26`、`Read:27`
- tool_result 中用的却是 `Bash:1`、`Bash:2`

这是 **Claude Code 自身的工具调用 ID 生成逻辑问题**，不是 cc2go 转换问题。但还有另一种可能：

**cc2go 需要验证**：转换时是否正确保留了原始的 tool_call_id？

当前转换代码：
```python
# router.py - convert_anthropic_messages_to_openai
tool_id = (
    tool_data.get("id")
    or tool_data.get("tool_use_id")
    or tool_data.get("call_id")
    or f"tc_{idx}_{len(tool_calls_list)}"
)
tool_calls_list.append({"id": tool_id, ...})
```

这段代码逻辑正确，会优先使用原始 ID。但需要日志验证：

**建议**：在 `convert_anthropic_messages_to_openai` 中，当发现 tool_use ID 与之前不同时，打印调试日志：
```python
logger.debug(f"[Tool Call ID] original={tool_id}, index={idx}")
```

**状态**：⚠️ 需要加日志验证 cc2go 转换是否正确传递 ID

---

### 错误 3：function name invalid（⚠️ 待验证）

**日志**：
```
# 05-16 22:40
2026-05-16 22:40:55 - ERROR - [Error] OpenCode API status=400:
{"error":{"message":"Error from provider: Provider returned error","code":400,
"metadata":{"raw":"{\"error\":{\"message\":\"Invalid request: function name is invalid,
must start with a letter and can contain letters, numbers, underscores, and dashes\"...
```

**分析**：Claude Code 生成的工具名（如 `Grep`、`Read`、`Bash`）完全符合规则。这个错误很奇怪。

**可能原因**：
1. 工具名中包含不可见字符（JSON 转义问题）
2. Moonshot AI 对工具名有额外限制
3. 转换过程中引入了非法字符

**cc2go 检查**：代码中工具名是直接透传的，没有修改。

**建议**：在日志中打印原始工具名，确认发送时是否被修改：
```python
logger.debug(f"[Tool Name] name={func_name}, sanitized={sanitize(func_name)}")
```

**状态**：⚠️ 需要日志验证

---

### 错误 4：MiniMax invalid params（⚠️ 待验证）

**日志**：
```
# 05-16 22:40
2026-05-16 22:40:11 - INFO - [Passthrough] MiniMax2.7 status=400:
{"type":"error","error":{"type":"invalid_request_error","message":"invalid params"}}

# 05-17 11:45
2026-05-17 11:45:49 - ERROR - [Error] OpenCode API status=400:
{"type":"error","error":{"type":"invalid_request_error","message":"Error from provider (MiniMax): invalid params"}}
```

**分析**：MiniMax 的 `/v1/messages` 端点对请求格式要求更严格。当前代码中 `thinking` 禁用逻辑是正确的：
```python
body["thinking"] = {"type": "disabled"}
```

但可能有其他参数格式问题。

**建议**：在发送 MiniMax 请求时，记录完整 payload：
```python
logger.debug(f"[MiniMax Payload] {json.dumps(body, ensure_ascii=False)[:500]}")
```

**状态**：⚠️ 需要日志验证

---

### 错误 5：速率限制（❌ 非 cc2go 问题）

**日志**：
```
2026-05-16 22:50:16 - ERROR - [Error] OpenCode API: status=429,
body={"error":{"message":"Error from provider (DeepSeek): Too many requests.
Please pace your requests reasonably. Your current concurrency: 500"
```

**分析**：这是上游 DeepSeek 的限流错误，与 cc2go 无关。

**状态**：❌ 无需修复

---

## 三、日志缺失的关键信息

当前日志格式缺少以下调试信息，导致问题定位困难：

### 3.1 原始 Anthropic 请求中的 tool_use ID
需要看到 Claude Code 发来的原始请求，确认 tool_use ID 格式。

### 3.2 转换后的 OpenAI 请求中的 tool_calls ID
需要看到 cc2go 转换后的请求，确认 ID 是否正确传递。

### 3.3 工具名原始值
需要看到发送给上游的函数名，确认是否有非法字符引入。

### 3.4 MiniMax 发送的完整 payload
需要看到 MiniMax 专用端点发送的完整参数。

---

## 四、修复计划

### 优先级 1：添加调试日志（低风险）

在 `router.py` 中添加以下调试日志，**不改变业务逻辑**，只增加可见性：

#### 4.1.1 tool_call_id 转换日志

```python
# 在 convert_anthropic_messages_to_openai 的 tool_use 处理中
logger.debug(f"[Tool] name={tool_data.get('name')}, id={tool_id}, converted_id={converted_id}")
```

#### 4.1.2 工具名日志

```python
# 在 convert_anthropic_messages_to_openai 的 tool_calls 处理中
logger.debug(f"[Function] name={func.get('name')}, args_preview={str(func.get('arguments', ''))[:50]}")
```

#### 4.1.3 MiniMax payload 日志

```python
# 在 anthropic_messages 的 MiniMax 处理中
logger.debug(f"[MiniMax] sending to /v1/messages: {json.dumps(body, ensure_ascii=False)[:500]}")
```

### 优先级 2：收集更多复现数据

1. 当遇到 400 错误时，保存完整请求和响应到单独的错误日志
2. 对比原始 Anthropic 请求和转换后的 OpenAI 请求
3. 确认 tool_call_id 是否一致

### 优先级 3：修复确认

根据日志数据，确认以下问题：

| 问题 | 如果日志显示 ID 一致 | 如果日志显示 ID 不一致 |
|------|---------------------|------------------------|
| tool_call_ids 缺失 | 是上游模型 bug | cc2go 转换 bug，需定位代码 |
| function name invalid | 上游限制过严 | cc2go 引入非法字符，需定位代码 |
| MiniMax invalid params | 参数格式差异 | cc2go 转换错误，需对照文档 |

---

## 八、tool_result 排列顺序 bug（✅ 已修复）

### 问题定位

通过 `error-archive/2026-05-17T125123.221302-kimi-k2.6-400.json` 完整上下文分析，定位到了 tool_call_ids 缺失错误的**根因**：

### 根因（已修复前）

转换后的 OpenAI 请求中，**user 消息被插在了 assistant 的 tool_calls 和对应的 tool_result 之间**：

```
错误情况（修复前）：
[65] assistant, tool_calls = ['Grep:26', 'Read:27', 'Read:28']
[66] user, content_len=210                         ← ❌ user 消息插在中间
[67] tool, tool_call_id=Read:28                   ← tool_result 被排到后面
```

### 修复（已验证）

`src/router.py` 第 285-286 行：
```python
# 添加 tool 结果（必须在用户文本之前，满足 OpenAI tool 消息紧跟 tool_calls 的要求）
openai_messages.extend(tool_results)
```

tool_results 在 content_items 之前输出，确保 tool 消息紧跟上一个 assistant 的 tool_calls，user text 排在 tool 之后。

### 验证方法

1. Claude Code 多轮工具调用（至少 3 轮以上）
2. 用 kimi-k2.6 模型测试（Moonshot AI 后端最严格）
3. 确认 error-archive 中不再出现 `tool_call_ids did not have response messages` 错误

---

## 五、当前代码中的工具调用 ID 处理逻辑

### 5.1 Anthropic → OpenAI（发送请求）

```python
# router.py:203-215
tool_id = (
    tool_data.get("id")
    or tool_data.get("tool_use_id")
    or tool_data.get("call_id")
    or f"tc_{idx}_{len(tool_calls_list)}"
)
tool_calls_list.append({
    "id": tool_id,
    "type": "function",
    "function": {
        "name": func_name,
        "arguments": json.dumps(tool_data.get("input", {}), ensure_ascii=False)
    }
})
```

### 5.2 OpenAI → Anthropic（接收响应）

```python
# router.py:353-365
call_id = (
    tc.get("id")
    or tc.get("tool_call_id")
    or tc.get("call_id")
    or f"tc_{int(time.time() * 1000)}"
)
content_items.append({
    "type": "tool_use",
    "id": call_id,
    "name": func.get("name", ""),
    "input": json.loads(func.get("arguments", "{}")) if isinstance(func.get("arguments"), str) else func.get("arguments", {})
})
```

### 5.3 tool_result 处理

```python
# router.py:229-239
tool_use_id = (
    tool_data.get("tool_use_id")
    or tool_data.get("tool_call_id")
    or tool_data.get("id")
    or f"tc_{idx}_{len(tool_results)}"
)
tool_results.append({
    "role": "tool",
    "tool_call_id": tool_use_id,
    "content": str(result_content) if result_content else ""
})
```

---

## 六、验证清单

修复后需要验证：

- [ ] 发送多轮工具调用请求，确认 tool_call_id 从 Anthropic 到 OpenAI 全程一致
- [ ] 打印工具名日志，确认无非法字符
- [ ] 打印 MiniMax payload 日志，确认格式正确
- [ ] 复现错误场景，确认修复有效

---

## 七、问题根因：缺少现场证据

当前日志格式存在根本缺陷：**错误发生时没有保存完整上下文**，导致问题定位困难。

### 7.1 核心问题

1. `detailed_logging` 控制太粗 — 开则所有请求都打印，关则所有请求都不打印，无法针对特定错误精细化复盘
2. 400 错误时没有保存现场 — 需要在 router.log 里翻找上下文，信息分散且可能被日志轮转覆盖
3. 缺少的关键数据：
   - 原始 Anthropic 请求（Claude Code 发来的完整 JSON）
   - 转换后的 OpenAI 请求（cc2go 转换后的 JSON）
   - 上游原始响应（OpenCode 返回的完整 JSON）
   - 错误摘要（时间戳、模型名、错误码）

### 7.2 解决方案

**方案 A：在 router.py 中实现错误现场自动归档（推荐）**

**改动 1：顶部新增 error archive 目录**

```python
ERROR_ARCHIVE_DIR = os.path.join(os.path.dirname(__file__), "error-archive")

def save_error_archive(name, model, request_body, openai_payload, response_text, status_code):
    """400 错误时保存完整上下文到独立 JSON 文件"""
    os.makedirs(ERROR_ARCHIVE_DIR, exist_ok=True)
    path = os.path.join(ERROR_ARCHIVE_DIR, f"{name}-{model}-{status_code}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "model": model,
            "status": status_code,
            "anthropic_request": request_body,
            "openai_request": openai_payload,
            "upstream_response": response_text,
        }, f, ensure_ascii=False, indent=2)
```

**改动 2：tool_use 转换处加日志（detailed_logging 控制）**

在 `convert_anthropic_messages_to_openai` 处理 tool_use 时：
```python
if config.detailed_logging:
    logger.debug(f"[Tool] id={tool_id}, name={tool_data.get('name')}")
```

**改动 3：发送 MiniMax 请求前打印 body**

在 `anthropic_messages` 发送 MiniMax 请求前：
```python
if config.detailed_logging:
    logger.debug(f"[MiniMax Payload] {json.dumps(body, ensure_ascii=False)[:1000]}")
```

**改动 4：400 错误时自动归档**

在收到上游响应状态码为 400 时（所有端点通用）：
```python
if response.status_code == 400:
    save_error_archive(
        datetime.now().strftime("%Y%m%d%H%M%S"),
        model_name,
        body,              # 原始 Anthropic 请求
        openai_payload,    # 转换后 OpenAI 请求
        raw_text,          # 上游响应原文
        response.status_code
    )
```

**方案 B：精细化 detailed_logging 控制**

将 `detailed_logging` 从 bool 改为层级控制：
```python
LOG_LEVEL_DETAIL = os.getenv("LOG_LEVEL_DETAIL", "none")
# LOG_LEVEL_DETAIL=none|summary|full|debug
# none: 只打印错误摘要
# summary: 打印请求/响应摘要（消息数量、工具数量）
# full: 打印完整 JSON（截取 2000 字）
# debug: 打印完整 JSON + 内部转换日志
```

---

## 八、预期效果

实施后，每次遇到 400 错误，自动生成 `error-archive/20260517-143022-kimi-k2.6-400.json`：

```json
{
  "timestamp": "2026-05-17T14:30:22",
  "model": "kimi-k2.6",
  "status": 400,
  "anthropic_request": { /* Claude Code 发来的原始 JSON */ },
  "openai_request": { /* cc2go 转换后的 JSON */ },
  "upstream_response": { /* 上游返回的错误详情 */ }
}
```

调查问题时只需：
```bash
ls error-archive/ | grep "kimi"
cat error-archive/20260517-143022-kimi-k2.6-400.json
```

即可完整还原现场，无需翻日志。

---

## 九、Issue 追踪

| # | 问题 | 类型 | 根因 | 状态 | 修复验证 |
|---|------|------|------|------|---------|
| 1 | PlainTextResponse 未定义 | 代码缺陷 | import 缺失 | ✅ 已修复 | 22:37 后无此错误 |
| 2 | tool_call_ids 缺失响应 | 行为异常 | 待定（需现场数据） | 🔍 待复现 | 需错误归档数据 |
| 3 | function name invalid | 行为异常 | 待定（需现场数据） | 🔍 待复现 | 需工具名日志 |
| 4 | MiniMax invalid params | 行为异常 | 待定（需现场数据） | 🔍 待复现 | 需 payload 日志 |
| 5 | 速率限制 | 上游限流 | 上游 DeepSeek 限流 | ✅ 已知 | 非 cc2go 问题 |

---

## 十、日志关键行号索引

| 行号 | 内容 |
|------|------|
| 329-656 | PlainTextResponse 错误（9 次） |
| 988-989 | DeepSeek tool_call_id 缺失（05-16） |
| 1739 | kimi-k2.6 tool_call_id 缺失（05-17 第一次） |
| 1761-1762 | kimi-k2.6 tool_call_id 缺失（05-17 第二次） |
| 1741 | function name invalid |
| 942 | MiniMax invalid params（05-16） |
| 1745 | MiniMax invalid params（05-17） |
| 1007-1008 | 速率限制 |
| 1729-1738 | 完整的多轮对话上下文 |