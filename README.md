# cc2go

**Claude Code → OpenCode Go | Claude Code 转 OpenCode Go 适配器**

English / [中文](#中文)

---

## English

A lightweight proxy that translates [Claude Code](https://claude.ai) (Anthropic Messages API) requests to OpenAI Chat Completions format, routing them to [OpenCode Go](https://opencode.ai) model endpoints. Includes a built-in Web UI for configuration.

> **Core capabilities:** Protocol conversion (Anthropic ↔ OpenAI) — built-in Web UI + system tray — auto-strip injected prompts and reasoning to save upstream tokens — broad model support (GLM, Kimi, Qwen, DeepSeek, MiMo, MiniMax, plus custom endpoints) — full tool call loop and image conversion — streaming SSE conversion — MCP web_search bypass via mmx CLI — adaptive error retry with backoff — auto-sync to Claude Code config — DeepSeek thinking mode fix (reasoning_content passthrough). **Direct replacement for cc-switch.**

### Features

- 🔄 **Format conversion** — Anthropic ↔ OpenAI translation (tool_use, tool_result, reasoning_content, images, streaming SSE)
- 🌐 **Web UI** — Built-in admin page at `http://localhost:4000`
- 🎯 **Model switching** — Click to switch models, auto-syncs to Claude Code settings
- ➕ **Custom models** — Add your own endpoints with independent API keys and URLs (Anthropic-format passthrough, or OpenAI-format conversion)
- 🌓 **i18n** — Chinese and English UI
- 🖼️ **Image support** — Converts Anthropic image blocks to OpenAI image_url format
- ⚡ **Streaming** — Real-time SSE streaming conversion (OpenAI → Anthropic format)
- 🔍 **MCP web_search bypass** — Intercepts web_search tool_use, runs `mmx search` CLI directly, returns results without upstream call
- 🔁 **Adaptive retry** — Error classification (rate_limit/auth/server/client) with exponential backoff, max 3 retries
- 📋 **Log management** — Built-in log viewer with rotation (5MB per file, 3 backups)
- 🖥️ **System tray** — Tray icon for opening admin page and quitting; auto-opens admin on start
- 💰 **Token saving** — Strips `<system-reminder>`, `[思考过程]` reasoning, and `thinking` blocks before forwarding upstream
- 📦 **Error archive** — Auto-saves full request/response context on 400+ errors (`error-archive/`), rate-limited to 1 per 30s
- 🧠 **DeepSeek thinking fix** — Automatically adds `reasoning_content: ""` to assistant messages with `tool_calls` when missing (fixes DeepSeek 400 error)

### Quick Start

**For non-developers (Windows):**
Download the latest release from [GitHub Releases](https://github.com/lzg14/cc2go/releases), extract, and run `start_bg.bat`. No Python required.

**For developers:**

```bash
pip install -r requirements.txt
cp .env.example .env   # Edit .env with your API key
python src/router.py
```

Or use the scripts (Windows):
```
scripts\start_bg.bat    # Background mode (system tray, no terminal)
scripts\stop.bat        # Stop background process
```

With `scripts\start_bg.bat`, cc2go runs silently in your system tray. Double-click the tray icon to open the admin page, right-click → Exit.

Open `http://localhost:4000` → enter API Key → select a model.

### Usage with Claude Code

Configure Claude Code:

| Setting | Value |
|---------|-------|
| Base URL | `http://localhost:4000` |
| API Key | `sk-litellm-local` |

Then use the Web UI to switch models — no Claude Code restart needed.

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web admin UI |
| `/v1/messages` | POST | Claude format entry (Anthropic → OpenAI) |
| `/v1/chat/completions` | POST | OpenAI format passthrough |
| `/v1/models` | GET | List available models |
| `/health` | GET | Health check |
| `/api/config` | GET/PUT | Configuration management |
| `/api/custom-models` | GET/PUT | Custom model management |
| `/api/logs` | GET | Recent log entries |
| `/api/refresh-models` | POST | Refresh model list from upstream |

### Supported Models

Dynamically fetched from OpenCode Go at startup, cached locally. Custom models can be added via Web UI with independent API keys.

### Custom Model Routing

| Endpoint | Behavior |
|----------|----------|
| `/v1/messages` | Anthropic format passthrough (no conversion, thinking blocks preserved) |
| `/v1/chat/completions` | OpenAI format conversion (thinking→reasoning_content, tool_calls gets reasoning_content="" if missing) |

### Configuration

All configuration can be managed via the Web UI (`http://localhost:4000`):
- **Connection** — OpenCode Go base URL and API key
- **Service** — Host, port, master key (auto-syncs to Claude Code)
- **Advanced** — Retry settings, thinking mode, CC model alias for image support
- **Custom Models** — Add/edit/remove custom model endpoints
- **Logs** — View logs, set log level, toggle detailed logging

---

## 中文

轻量级 AI 模型路由代理，将 Claude Code (Anthropic Messages API) 格式自动转为 OpenAI Chat Completions 格式，桥接到 [OpenCode Go](https://opencode.ai/zh/go) 的模型端点。内置 Web 管理页面，配置更方便。

> **核心作用：** 协议转换（Anthropic ↔ OpenAI）→ Web 管理页 + 系统托盘切换模型 → 自动摘除注入提示词和推理文本省上游 Token → 支持 GLM / Kimi / Qwen / DeepSeek / MiMo / MiniMax 等主流模型及自定义端点 → 完整工具调用循环 + 图片转换 + 流式 SSE 转换 → MCP web_search 短路直搜 → 自适应错误重试 → 自动同步 Claude Code 配置 → DeepSeek thinking 模式修复（reasoning_content 透传）。**可直接替代 cc-switch 等模型切换工具。**

### 特性

- 🔄 **格式转换** — 自动处理 tool_use、tool_result、reasoning_content、图片等格式转换
- 🌐 **Web 管理页面** — 浏览器打开 `http://localhost:4000` 即可管理
- 🎯 **模型切换** — 一键切换，自动同步到 Claude Code 配置
- ➕ **自定义模型** — 添加自己的 API 端点，独立配置 Key 和地址（Anthropic 格式透传或 OpenAI 格式转换）
- 🌓 **中英双语** — 界面支持中文和 English
- 🖼️ **图片支持** — Anthropic 图片格式自动转换
- 🔁 **自适应重试** — 错误分类（限流/鉴权/服务端/客户端），指数退避，最多 3 次
- ⚡ **流式支持** — 实时 SSE 流式转换（OpenAI → Anthropic 格式）
- 🔍 **MCP web_search 短路** — 拦截 web_search 工具调用，直接通过 `mmx search` CLI 搜索并返回
- 📋 **日志管理** — 内置日志查看器，自动轮转（每文件 5MB，保留 3 份）
- 🖥️ **系统托盘** — 托盘图标，打开管理页和退出；启动后自动打开管理页
- 💰 **省 Token** — 转发前摘除 `<system-reminder>`、`[思考过程]` 推理文本及 `thinking` 块
- 📦 **错误归档** — 400+ 错误自动保存请求/响应完整上下文（`error-archive/`），限速 30 秒 1 次
- 🧠 **DeepSeek thinking 修复** — 自动为有 tool_calls 的 assistant 消息补充 `reasoning_content: ""`（修复 DeepSeek 400 错误）

### 快速开始

**普通用户（Windows）：**
从 [GitHub Releases](https://github.com/lzg14/cc2go/releases) 下载最新版，解压后双击 `start_bg.bat` 即可，无需安装 Python。

**开发者：**

```bash
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填入 API Key
python src/router.py
```

也可使用脚本启动（Windows）：
```
scripts\start_bg.bat    # 后台托盘模式（不弹窗口，系统托盘运行）
scripts\stop.bat        # 停止后台进程
```

`scripts\start_bg.bat` 启动后会在系统托盘显示图标，双击打开管理页，右键菜单可退出。

打开 `http://localhost:4000` → 填入 API Key → 选择模型即可使用。

### 在 Claude Code 中使用

配置 Claude Code：

| 配置项 | 值 |
|--------|-----|
| Base URL | `http://localhost:4000` |
| API Key | `sk-litellm-local` |

之后通过管理页面切换模型，无需重启 Claude Code。

### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | Web 管理页面 |
| `/v1/messages` | POST | Claude 格式入口（Anthropic → OpenAI） |
| `/v1/chat/completions` | POST | OpenAI 格式透传 |
| `/v1/models` | GET | 获取可用模型列表 |
| `/health` | GET | 健康检查 |
| `/api/config` | GET/PUT | 配置管理 |
| `/api/custom-models` | GET/PUT | 自定义模型管理 |
| `/api/logs` | GET | 最近日志 |
| `/api/refresh-models` | POST | 手动刷新模型列表 |

### 自定义模型路由

| 端点 | 行为 |
|------|------|
| `/v1/messages` | Anthropic 格式透传（不做格式转换，thinking 块保留） |
| `/v1/chat/completions` | 走格式转换（thinking→reasoning_content，tool_calls 补 reasoning_content=""） |

### 配置管理

所有配置可通过 Web UI 完成：
- **连接** — OpenCode Go 地址和 API Key
- **服务** — 监听主机、端口、Master Key（自动同步到 Claude Code）
- **高级** — 重试设置、思考模式、CC 模型别名（用于图片发送）
- **自定义模型** — 增删改自定义 API 端点
- **日志** — 查看日志、设置日志级别、开关详细日志

---

## License

MIT

> 技术细节见 [ARCHITECTURE.md](ARCHITECTURE.md)