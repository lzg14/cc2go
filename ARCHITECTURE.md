# cc2go 架构设计

> 版本：0.6.0 | 更新：2026-05-17

---

## 整体数据流

```
Claude Code          cc2go                 OpenCode Go         上游模型
(Anthropic API)      (格式适配)             (路由聚合)           (GLM/Kimi/...)
     │                  │                      │                  │
     │ POST /v1/messages│                      │                  │
     │─────── Anthropic ─→│                      │                  │
     │                  │                       │                  │
     │                  │ ├─ 有 web_search ───→ MCP 短路           │
     │                  │ │  tool_use?          (mmx CLI 搜索)     │
     │                  │ │                     ←返回搜索结果──────│
     │                  │ │                                          │
     │                  │ ├─ 自定义透传? ─────→ 直传                 │
     │                  │ │   (endpoint=/v1/    (无格式转换)         │
     │                  │ │    messages)        ←─── 响应 ─────────│
     │                  │ │                                          │
     │                  │ ├─ MiniMax 端点? ───→ 直传 + thinking禁用 │
     │                  │ │                     (无格式转换)         │
     │                  │ │                     ←─── 响应 ─────────│
     │                  │ │                                          │
     │                  │ └─ 其他模型 ────────→ 转为 OpenAI 格式     │
     │                  │      (OpenAI格式)    /v1/chat/completions  │
     │                  │                      │──── 透传 ─────────→│
     │                  │                      │←─── 响应 ─────────│
     │                  │                        (流式/非流式)       │
     │                  │←──── OpenAI ──────────                    │
     │                  │ 转为 Anthropic 格式    │                  │
     │                  │ (或通过 streaming.py)  │                  │
     │←── Anthropic ───│                      │                  │
```

**核心职责**：cc2go 做 **格式转换 + 转发 + 可选短路**，不缓存、不持久化对话内容。

---

## 路由决策树

`POST /v1/messages` 入口按以下优先级处理请求：

```
                            ┌─────────────────┐
                            │   接收请求 body   │
                            └────────┬────────┘
                                     │
                            ┌────────▼────────┐
                            │ MCP 工具短路检测  │
                            │ should_bypass()  │
                            └────────┬────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │ 有 tool_use:   │                │
                    │ web_search     │ 无             │
                    ▼                │                │
            ┌──────────────┐         │                │
            │ mmx CLI 搜索  │         │                │
            │ 返回结果      │         │                │
            └──────────────┘         │                │
                                     ▼                │
                            ┌────────────────────┐     │
                            │ 自定义模型判定       │     │
                            │ (按 id 精确匹配)     │     │
                            └────────┬───────────┘     │
                    ┌────────────────┼──────────┐       │
                    │ endpoint=/v1/  │          │       │
                    │ messages?     │          │       │
                    ▼                │          │       │
            ┌──────────────┐         │          │       │
            │ Anthropic格式 │         │          │       │
            │ 透传直发       │         │          │       │
            └──────────────┘         │          │       │
                                     ▼          │       │
                            ┌────────────────────┐│       │
                            │ MiniMax 端点?       ││       │
                            │ endpoint=/v1/       ││       │
                            │ messages (预置)     ││       │
                            └────────┬───────────┘│       │
                    ┌────────────────┼────────┐   │       │
                    │ 是 (MiniMax)   │        │   │       │
                    ▼                │        │   │       │
            ┌──────────────┐         │        │   │       │
            │ 1. 删output   │         │        │   │       │
            │   _config    │         │        │   │       │
            │ 2. thinking  │         │        │   │       │
            │   =disabled  │         │        │   │       │
            │ 3. 摘除thinking│         │        │   │       │
            │ 4. 直传       │         │        │   │       │
            └──────────────┘         │        │   │       │
                                     ▼        │   │       │
                            ┌──────────────────┐│        │
                            │ 格式转换 + 发往    ││        │
                            │ OpenAI 端点       │◄┘        │
                            │ (非流式/流式)      │         │
                            │                   │         │
                            │ ① thinking→       │         │
                            │   reasoning_content│         │
                            │ ② 有tool_calls的   │         │
                            │   assistant消息    │         │
                            │   补reasoning=""   │         │
                            └──────────────────┘         │
                                                             │
                            ┌────────────────────────────────┘
```

### 自定义模型路由匹配

请求中的 `model` 字段与自定义模型按 **id** 精确匹配：
- 预置模型（如 `deepseek-v4-flash`）→ 走预置配置的端点
- 自定义模型（如 `DeepSeek-Custom`）→ 走自定义的 `base_url` + `endpoint` + `api_key`

---

## 模块职责

### router.py — 主路由 + 格式转换 + Web UI

- FastAPI 应用，监听 `host:port`（默认 `0.0.0.0:4000`）
- `POST /v1/messages`：核心入口，实现上述决策树
- `convert_anthropic_messages_to_openai()`：Anthropic → OpenAI 格式转换
  - `thinking` 块 → `reasoning_content`
  - 有 `tool_calls` 的 assistant 消息缺失 `reasoning_content` 时自动补 `""`（DeepSeek thinking mode 要求）
- `convert_response_to_anthropic()`：OpenAI → Anthropic 格式转换
- `convert_tools()`：工具定义格式转换
- `strip_thinking_from_messages()`：MiniMax 专用的 thinking 块摘除
- `strip_system_reminder()` / `strip_reasoning()`：节省 token
- `sync_claude_settings()`：配置变更后自动写入 Claude Code 配置文件
- `refresh_models()`：从 OpenCode Go 拉取模型列表，缓存到 `data/models_cache.json`
- 内嵌 Web 管理页面（`ADMIN_HTML`），中英双语

### streaming.py — 流式转换

- `convert_openai_stream_to_anthropic()`：将 OpenAI SSE 流实时转换为 Anthropic SSE 格式
- `build_content_block_start()` / `build_content_block_delta()` / `build_content_block_stop()`：SSE 事件构造器
- 支持 `text_delta` / `input_json_delta` 事件类型
- `tool_use` 的 id/name 在 `content_block_start` 中携带（无需额外 delta 事件）

### mcp_bypass.py — MCP 工具短路

- `should_bypass()`：检查消息中是否有 `tool_use: web_search`，有则短路
- `extract_query()`：从消息中提取搜索查询词
- `handle_bypass()`：调用 `mmx search` CLI 执行搜索，返回 Anthropic 格式结果
- 支持的短路工具：`web_search`、`mcp__MiniMax__web_search`

### error_handler.py — 错误分类与自适应

- `classify_error()`：将 HTTP 错误分类为 `rate_limit` / `auth_error` / `server_error` / `client_error`
- `calculate_backoff()`：指数退避延迟计算（默认倍率 2，上限 60s）
- `should_retry()`：判断是否应该重试（rate_limit 和 server_error 可重试）
- `ErrorArchiveRateLimiter`：错误归档限速器（默认 30 秒内最多 1 次），带线程锁

### tray.py — 系统托盘

- pystray 托盘图标（常驻后台）
- 菜单仅两项：**打开管理页** + **退出**
- 启动后自动打开管理页
- 双击托盘图标打开管理页

---

## 格式转换

### Anthropic → OpenAI（上行）

| Anthropic 块 | OpenAI 字段 | 说明 |
|-------------|------------|------|
| `thinking` | `reasoning_content` | DeepSeek 等思考模型的推理内容 |
| `tool_use` | `tool_calls[].function` | ID 多字段名兼容 |
| `tool_result` | `tool` 消息 (`role=tool`) | 紧跟 assistant tool_calls |
| `image` (base64) | `image_url` | media_type 透传 |
| 文本 | `content` | — |

**关键规则**：
- assistant 消息有 `tool_calls` 但无 `thinking` 块时，自动补 `reasoning_content: ""`（DeepSeek thinking mode 400 错误修复）
- `reasoning_content` 为空字符串时也必须写入（用 `is not None` 判断而非 truthy）
- 已是 OpenAI 格式的消息（顶层 `tool_calls`/`reasoning_content`/`tool_call_id`）直接透传

### OpenAI → Anthropic（下行）

| OpenAI 字段 | Anthropic 块 | 说明 |
|------------|-------------|------|
| `tool_calls` | `tool_use` | arguments JSON 反序列化 |
| `reasoning_content` | `[思考过程]\n{text}` | 包成文本前缀给 Claude Code 看 |
| `finish_reason=tool_calls` | `stop_reason=tool_use` | 映射 |
| `content` | `text` | — |

### 三条路径的 thinking 处理

| 路径 | thinking 块处理 | reasoning_content 处理 |
|------|---------------|----------------------|
| 自定义透传 (`/v1/messages`) | **不摘除**，原样透传 | 不涉及（Anthropic 格式直传） |
| MiniMax (`/v1/messages`) | **摘除**（`strip_thinking_from_messages`） | 不涉及（Anthropic 格式直传） |
| 格式转换 (`/v1/chat/completions`) | → `reasoning_content`；无 thinking 但有 tool_calls → 补 `""` | 保留原值 |

---

## Token 优化

cc2go 在转发前删除以下内容以节省上游 token：

| 删除项 | 函数 | 省量预估 |
|--------|------|---------|
| `<system-reminder>` 块 | `strip_system_reminder()` | 500-2000 token/条 |
| `[思考过程]` 前缀块 | `strip_reasoning()` | 300-800 token/条 |
| `thinking` 块 | 消息转换中转为 `reasoning_content` 或摘除 | 视模型而定 |

**原理**：Claude Code 回复中 `[思考过程]` 是上游模型的 reasoning，下次请求 Claude Code 会原样发回。cc2go 在转发给上游前摘除，不影响对话连贯性。

> 注意：摘除只影响 **转发** 方向。上游模型回复中的 reasoning 仍会以 `[思考过程]` 格式传给 Claude Code。

---

## 工具调用循环

```
Claude Code         cc2go               上游
    │                 │                   │
    │ tool_use (多个) │                   │
    │────────────────→│ tool_calls        │
    │                 │──────────────────→│
    │                 │←─── 响应(可能有新tool_calls) ──│
    │ tool_use/result │                   │
    │←────────────────│                   │
    │ (CC 执行工具)    │                   │
    │ tool_result     │                   │
    │────────────────→│ tool 消息          │
    │                 │──────────────────→│
```

关键点：
- `tool_result` 转 `tool` 消息时，必须紧跟上一个 assistant 的 `tool_calls`（OpenAI 协议要求）
- ID 在多轮转换中保持字符串透传，不做变换
- 支持多字段名兼容（`id` / `tool_use_id` / `tool_call_id` / `call_id`）
- 有 `tool_calls` 的 assistant 消息必须包含 `reasoning_content` 字段（DeepSeek 要求）

---

## 错误归档 + 自适应

### 错误分类

| 分类 | 匹配条件 | 可重试 |
|------|---------|-------|
| `rate_limit` | 429 / 529 | ✅ |
| `auth_error` | 401 / 403 | ❌ |
| `server_error` | 500 / 502 / 503 | ✅ |
| `client_error` | 400 / 404 / 其他 4xx | ❌ |

### 重试策略

- `rate_limit`：指数退避 + 随机 jitter（base=2, max=60s）
- `server_error`：固定退避 1s，最多重试 3 次
- 其他错误：不重试

### 错误归档

遇到 400+ 错误时自动保存完整现场（限速：每 30 秒最多 1 次）：

```
error-archive/
└── 2026-05-17T210016-deepseek-v4-flash-400.json  # {时间戳}-{模型名}-{状态码}.json
    ├── anthropic_request   # 原始请求（或转换后的 OpenAI 请求）
    ├── openai_request      # 格式转换后的请求（仅格式转换路径，透传路径为 null）
    └── upstream_response   # 上游错误响应
```

事后复盘无需翻日志，直接查看 JSON 文件即可定位问题。

---

## 配置流

```
.env 文件 → load_dotenv() → Config 类 → router.py / tray.py 使用
                                       ↑
                              Web UI PUT /api/config
                              (写回 .env + reload)
                                       ↓
                              sync_claude_settings()
                              (同步到 Claude Code 配置)
```

`update_config_api()` 在收到模型切换请求时：
1. 更新 `config.selected_model`
2. 写回 `.env`
3. 同步到 Claude Code 配置文件
4. 使用模型的 `id` 作为标识符，`claude_model_alias` 用于 Claude Code 显示名

---

## 自定义模型

Web UI 添加的自定义模型存储到 `data/custom_models.json`。

### 流式语义

```
新增 → 自动生成唯一 ID（display_name 的 slug + 时间戳后缀）
     → 不与系统预置模型重名
编辑 → 保留原 ID 不变
切换 → 列表中点击切换模型
删除 → 确认后删除
```

### 透传规则

| endpoint | 行为 |
|----------|------|
| `/v1/messages` | Anthropic 格式直传，不做格式转换（thinking 块保留） |
| `/v1/chat/completions` | 转为 OpenAI 格式（thinking→reasoning_content，tool_calls 补 reasoning_content=""） |

### 路由匹配

- 请求 `model` 字段与自定义模型按 **id** 精确匹配
- 预置模型名和自定义模型 id 不会冲突（自定义模型使用 slug+时间戳 生成唯一 id）

### MiniMax 特殊处理

MiniMax 的预置模型（endpoint=`/v1/messages`）额外处理：
- `thinking: {type: "disabled"}` — MiniMax 不支持 thinking
- 当 `tools` 为空且 `output_config.format.type` 为 `json_schema` 时删除 `output_config` — MiniMax 拒绝该组合
- `strip_thinking_from_messages()` — 摘除历史消息中的 thinking 块

---

## 模型缓存与刷新

```
启动时：
  refresh_models()
    ├─ 上游成功 → config.models = 上游模型 + 自定义
    │             └─ 缓存到 data/models_cache.json
    ├─ 上游失败 → 读缓存 data/models_cache.json
    └─ 缓存失败 → DEFAULT_MODELS + 自定义

管理页点击"刷新模型"：
  POST /api/refresh-models → refresh_models()
  (同启动时流程)

自定义模型变更时：
  PUT /api/custom-models → save_custom_models()
                         → refresh_models()
```

### 预置模型列表

| 模型 | 端点 |
|------|------|
| glm-5.1 | /v1/chat/completions |
| glm-5 | /v1/chat/completions |
| kimi-k2.6 | /v1/chat/completions |
| kimi-k2.5 | /v1/chat/completions |
| qwen3.6-plus | /v1/chat/completions |
| qwen3.5-plus | /v1/chat/completions |
| deepseek-v4-pro | /v1/chat/completions |
| deepseek-v4-flash | /v1/chat/completions |
| mimo-v2.5 | /v1/chat/completions |
| mimo-v2.5-pro | /v1/chat/completions |
| minimax-m2.7 | /v1/messages |
| minimax-m2.5 | /v1/messages |

---

## 目录结构

```
cc2go/
├── src/                    # 核心源码
│   ├── router.py           # 主服务：格式转换、API 端点、Web UI
│   ├── router_test.py      # 格式转换单元测试（19 用例）
│   ├── tray.py             # 系统托盘：托盘图标（仅管理页 + 退出）
│   ├── streaming.py        # 流式响应转换（OpenAI SSE → Anthropic SSE）
│   ├── streaming_test.py   # 流式转换单元测试（16 用例）
│   ├── mcp_bypass.py       # MCP 工具短路（web_search → mmx CLI）
│   ├── mcp_bypass_test.py  # 短路模块测试（14 用例）
│   ├── error_handler.py    # 错误分类、指数退避、归档限速
│   └── error_handler_test.py # 错误处理测试（24 用例）
├── scripts/                # 启停脚本
│   ├── start_bg.bat        # Windows 托盘启动
│   ├── stop.bat            # Windows 停止
│   ├── start.sh            # Linux/Mac 启动
│   └── stop.sh             # Linux/Mac 停止
├── static/                 # Web UI 静态资源
├── data/                   # 运行时数据（gitignored）
├── logs/                   # 日志（gitignored）
├── error-archive/          # 错误现场归档（gitignored）
├── build_release.py        # PyInstaller 打包
├── SPEC.md                 # 版本管理规范
├── README.md               # 项目说明
├── .env.example            # 配置模板
├── requirements.txt        # Python 依赖
└── .gitignore
```

---