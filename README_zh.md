# cc2go

<p align="center">
  <b>Claude Code → OpenCode Go 适配器</b>
  <br>
  轻量级 AI 模型路由代理，让 Claude Code 使用国产模型
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue" alt="Python">
  <img src="https://img.shields.io/github/license/lzg14/cc2go" alt="License">
  <img src="https://img.shields.io/github/v/release/lzg14/cc2go" alt="Release">
  <img src="https://img.shields.io/github/stars/lzg14/cc2go" alt="Stars">
</p>

## 特性

- 🔄 **协议转换** — 自动将 Anthropic Messages API 转为 OpenAI Chat Completions 格式
- 🌐 **Web 管理页** — 浏览器打开 `http://localhost:4001`，点击切换模型
- 🎯 **模型切换** — 一键切换，自动同步到 Claude Code 配置
- ➕ **自定义模型** — 添加自己的 API 端点，独立配置 Key 和地址
- 🖼️ **图片支持** — Anthropic 图片格式自动转换
- ⚡ **流式支持** — 实时 SSE 流式转换（OpenAI → Anthropic 格式）
- 🔁 **自适应重试** — 错误分类（限流/鉴权/服务端/客户端），指数退避，最多 3 次
- 📋 **日志管理** — 内置日志查看器，自动轮转（每文件 5MB，保留 3 份）
- 🖥️ **系统托盘** — 托盘图标，打开管理页和退出；启动后自动打开管理页
- 💰 **省 Token** — 转发前摘除 `<system-reminder>`、`[思考过程]` 推理文本及 `thinking` 块
- 💾 **配置备份** — 首次切换模型时自动备份原始配置，管理页一键恢复
- 🔑 **安全增强** — 默认监听 127.0.0.1（仅本地可访问），代理端点需 Bearer Token 验证
- 🔧 **工具名兼容** — 自动清理工具名中的特殊字符，防止上游 400 错误
- 🧹 **Schema 清理** — 递归移除 JSON Schema 中的不兼容字段，提高模型兼容性

---

## 快速开始（普通用户适用）

### 第一步：下载

从 [GitHub Releases](https://github.com/lzg14/cc2go/releases) 下载最新版本。
解压到任意文件夹（建议不要放 C:\Program Files）。

### 第二步：配置 API Key

1. 打开解压后的文件夹
2. 复制 `.env.example` 为 `.env`（或新建一个名为 `.env` 的文件）
3. 用记事本打开 `.env`，填入你的 OpenCode Go API Key：
   ```
   OPENCODE_API_KEY=你的API密钥
   ```

### 第三步：运行

双击文件夹中的 `start_bg.bat`。

系统托盘会出现 cc2go 图标，管理页面会自动在浏览器中打开。

### 第四步：在 Claude Code 中配置

在 Claude Code 的设置里配置：

| 配置项 | 值 |
|--------|-------|
| Base URL | `http://localhost:4001` |
| API Key | `sk-cc2go-local` |

完成！正常使用 Claude Code，切换模型在管理页面 `http://localhost:4001` 操作即可。

### 如何退出

右键点击系统托盘图标 → 选择「退出」。

---

## 开发者启动方式

```bash
pip install -r requirements.txt
cp .env.example .env   # 编辑 .env 填入 API Key
python src/router.py
```

### 脚本启动（Windows）

```
scripts\start_bg.bat    # 后台托盘模式（不弹窗口）
scripts\stop.bat        # 停止后台进程
```

---

## Docker

```bash
docker build -t cc2go .
docker run -d -p 4001:4001 --env-file .env cc2go
```

访问 `http://localhost:4001` 管理页面。

## Linux 服务 (systemd)

适用于 Linux 长期运行：

```bash
# 安装服务
sudo cp cc2go.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cc2go
sudo systemctl start cc2go
```

需要创建 `cc2go` 用户并把项目安装在 `/opt/cc2go` 目录。

---

关于 Gitee 同步：GitHub Release 不会自动同步到 Gitee，需要在 Gitee 手动创建发布或使用第三方同步工具。

---

## API 端点

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

---

## 自定义模型路由

| 端点 | 行为 |
|------|------|
| `/v1/messages` | Anthropic 格式透传（不做格式转换，thinking 块保留） |
| `/v1/chat/completions` | 走格式转换（tool_calls 补 reasoning_content=""） |

---

## 配置说明

通过 Web UI（`http://localhost:4001`）完成所有配置：
- **连接** — OpenCode Go 地址和 API Key
- **服务** — 监听主机、端口、Master Key（自动同步到 Claude Code）
- **自定义模型** — 增删改自定义 API 端点
- **日志** — 查看日志、设置日志级别、开关详细日志

---

## License

MIT

> 技术细节见 [ARCHITECTURE.md](ARCHITECTURE.md)