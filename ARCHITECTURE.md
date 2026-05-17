# cc2go 架构设计

> 版本：0.5.0 | 更新：2026-05-17

---

## 整体数据流

```
Claude Code          cc2go                 OpenCode Go         上游模型
(Anthropic API)      (格式适配)             (路由聚合)           (GLM/Kimi/...)
     │                  │                      │                  │
     │ POST /v1/messages│                      │                  │
     │─────── Anthropic ─→│                      │                  │
     │                  │ 转为 OpenAI 格式       │                  │
     │                  │────── /v1/chat/ ──────→│                  │
     │                  │        completions     │──── 透传 ──────→│
     │                  │                      │←─── 响应 ───────│
     │                  │←──── OpenAI ──────────│                  │
     │                  │ 转为 Anthropic 格式    │                  │
     │←── Anthropic ───│                      │                  │
```

**核心职责**：cc2go 只做 **格式转换 + 转发**，不缓存、不持久化对话内容。

---

## 格式转换

### Anthropic → OpenAI（上行）

| Anthropic 块 | OpenAI 字段 | 说明 |
|-------------|------------|------|
| `tool_use` | `tool_calls[].function` | ID 多字段名兼容 |
| `tool_result` | `tool` 消息 (`role=tool`) | 紧跟 assistant tool_calls |
| `image` (base64) | `image_url` | media_type 透传 |
| 文本 | `content` | — |

### OpenAI → Anthropic（下行）

| OpenAI 字段 | Anthropic 块 | 说明 |
|------------|-------------|------|
| `tool_calls` | `tool_use` | arguments JSON 反序列化 |
| `reasoning_content` | `[思考过程]\n{text}` | 包成文本前缀给 Claude Code 看 |
| `finish_reason=tool_calls` | `stop_reason=tool_use` | 映射 |
| `content` | `text` | — |

---

## Token 优化

cc2go 在转发前删除以下内容以节省上游 token：

| 删除项 | 函数 | 省量预估 |
|--------|------|---------|
| `<system-reminder>` 块 | `strip_system_reminder()` | 500-2000 token/条 |
| `[思考过程]` 前缀块 | `strip_reasoning()` | 300-800 token/条 |

**原理**：Claude Code 回复中 `[思考过程]` 是上游模型的 reasoning，下次请求 Claude Code 会原样发回。cc2go 在转发给上游前摘除，不影响对话连贯性。

> 注意：`reasoning_content` 摘除只影响 **转发** 方向。上游模型回复中的 reasoning 仍会以 `[思考过程]` 格式传给 Claude Code，保证 CC 能看到思考过程。

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

---

## 错误归档

遇到 400 错误时自动保存完整现场到 `error-archive/`：

```
error-archive/
└── 2026-05-17T125123-kimi-k2.6-400.json
    ├── anthropic_request   # CC 原始请求
    ├── openai_request      # 转换后请求
    └── upstream_response   # 上游错误响应
```

事后复盘无需翻日志，直接查看 JSON 文件即可定位问题。

---

## 目录结构

```
cc2go/
├── src/              # 核心源码
│   ├── router.py     # 主服务：格式转换、API 端点、Web UI
│   └── tray.py       # 系统托盘：托盘图标、模型切换菜单
├── scripts/          # 启停脚本
├── static/           # Web UI 静态资源
├── data/             # 运行时数据（gitignored）
├── logs/             # 日志（gitignored）
├── error-archive/    # 错误现场归档（gitignored）
├── build_release.py  # PyInstaller 打包
└── SPEC.md           # 版本管理规范
```

---

## 配置流

```
.env 文件 → load_dotenv() → Config 类 → router.py / tray.py 使用
                                       ↑
                              Web UI PUT /api/config
                              (写回 .env + reload)
```

---

## 自定义模型

Web UI 添加的自定义模型存储到 `data/custom_models.json`。自定义模型的请求 **不做格式转换**，完全透传。

```
识别 → 直接 POST 到自定义 URL + 自定义 API Key
      → 响应原样返回
```

---
