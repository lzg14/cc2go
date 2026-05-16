# cc2go

**Claude Code → OpenCode Go | Claude Code 转 OpenCode Go 适配器**

English / [中文](#中文)

---

## English

A lightweight proxy that translates [Claude Code](https://claude.ai) (Anthropic Messages API) requests to OpenAI Chat Completions format, routing them to [OpenCode Go](https://opencode.ai) model endpoints. Includes a built-in Web UI for configuration.

### Features

- 🔄 **Format conversion** — Anthropic ↔ OpenAI translation (tool_use, tool_result, reasoning_content, images)
- 🌐 **Web UI** — Built-in admin page at `http://localhost:4000`
- 🎯 **Model switching** — Click to switch models, auto-syncs to Claude Code settings
- ➕ **Custom models** — Add your own endpoints with independent API keys and URLs (passthrough, no format conversion)
- 🌓 **i18n** — Chinese and English UI
- 🖼️ **Image support** — Converts Anthropic image blocks to OpenAI image_url format
- 🔁 **Auto retry** — Failed requests retry up to 3 times with backoff
- 📋 **Log management** — Built-in log viewer with rotation (5MB per file, 3 backups)

### Quick Start

```bash
pip install -r requirements.txt
cp config.yaml.example .env   # Edit .env with your API key
python router.py
```

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

Dynamically fetched from OpenCode Go at startup, cached locally. Custom models can be added via Web UI with independent API keys and full request passthrough.

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

### 特性

- 🔄 **格式转换** — 自动处理 tool_use、tool_result、reasoning_content、图片等格式转换
- 🌐 **Web 管理页面** — 浏览器打开 `http://localhost:4000` 即可管理
- 🎯 **模型切换** — 一键切换，自动同步到 Claude Code 配置
- ➕ **自定义模型** — 添加自己的 API 端点，独立配置 Key 和地址，请求完全透传
- 🌓 **中英双语** — 界面支持中文和 English
- 🖼️ **图片支持** — Anthropic 图片格式自动转换
- 🔁 **自动重试** — 请求失败最多重试 3 次
- 📋 **日志管理** — 内置日志查看器，自动轮转（每文件 5MB，保留 3 份）

### 快速开始

```bash
pip install -r requirements.txt
cp config.yaml.example .env   # 编辑 .env 填入 API Key
python router.py
```

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

### 支持的模型

启动时从 OpenCode Go 动态拉取并缓存到本地，网络不可用时自动使用缓存。支持通过 Web UI 添加自定义模型，请求完全透传，不做格式转换。

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
