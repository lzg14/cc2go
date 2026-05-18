# cc2go 版本管理规范 (SPEC)

> 当前版本：0.6.0 | 更新：2026-05-17

---


## 版本号规则

遵循语义化版本 2.0.0：

```
MAJOR.MINOR.PATCH

0.5.0 → 主版本 0, 次版本 5, 修订号 0
```

| 升级 | 触发条件 | 示例 |
|------|---------|------|
| PATCH | Bug 修复、文案调整 | `0.6.0` → `0.6.1` |
| MINOR | 新功能、向后兼容 | `0.6.0` → `0.7.0` |
| MAJOR | 不兼容的 API 变更 | `0.6.0` → `1.0.0` |


## 升版步骤

### 1. 改版本号

```python
# src/router.py
VERSION = "x.y.z"   # 只改这一处，tray.py / build_release.py 自动引用
```

### 2. 生成 Release 包

```bash
python build_release.py
# 生成 cc2go-vx.y.z-windows.zip
```

### 3. 提交并打 Tag

```bash
git add src/router.py
git commit -m "release: vx.y.z - 描述"
git tag vx.y.z
git push && git push --tags
```

### 4. 上传到 GitHub Releases

将 `cc2go-vx.y.z-windows.zip` 作为 Release 附件上传。


## 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| 0.5.0 | 2026-05-17 | 初始开发：格式转换、系统托盘、打包脚本、工具调用循环 |
| 0.5.0+ | 2026-05-17 | 新增：流式 SSE 转换（streaming.py）、MCP web_search 短路（mcp_bypass.py）、错误分类与自适应重试（error_handler.py）、自定义模型 display_name 支持、auto-ID 生成、output_config 修复、Cache-Control 缓存防过期、mmx Windows 路径修复、54 个单元测试 |
| 0.6.0 | 2026-05-17 | 模型 UI 重构（预置/自定义分区、编辑按钮）、托盘简化（仅管理页+退出）、thinking 块三路处理（透传/摘除/转换）、DeepSeek reasoning_content 修复（tool_calls 消息自动补空）、自定义模型按 id 路由匹配（避免预置模型名冲突）、错误归档增强、19 个格式转换单元测试 |


## 项目目录结构

```
cc2go/
├── src/                    # 源码
│   ├── router.py           # 主服务：格式转换 + API 端点 + Web UI
│   ├── router_test.py      # 格式转换单元测试（19 用例）
│   ├── tray.py             # 系统托盘（仅管理页 + 退出）
│   ├── streaming.py        # 流式 SSE 转换 (OpenAI → Anthropic)
│   ├── streaming_test.py   # 流式转换测试 (16 用例)
│   ├── mcp_bypass.py       # MCP web_search 短路 (mmx CLI)
│   ├── mcp_bypass_test.py  # 短路模块测试 (14 用例)
│   ├── error_handler.py    # 错误分类 + 指数退避 + 归档限速
│   └── error_handler_test.py # 错误处理测试 (24 用例)
├── scripts/                # 启动/停止脚本
│   ├── start_bg.bat        # Windows 托盘启动
│   ├── stop.bat            # Windows 停止
│   ├── start.sh            # Linux/Mac 启动
│   └── stop.sh             # Linux/Mac 停止
├── static/                 # Web UI 静态资源
├── data/                   # 运行时数据 (gitignored)
├── logs/                   # 日志 (gitignored)
├── error-archive/          # 错误归档 (gitignored)
├── build_release.py        # PyInstaller 打包脚本
├── requirements.txt        # Python 依赖
├── .env.example            # 配置模板
├── SPEC.md                 # 本文件
├── README.md               # 项目说明
├── ARCHITECTURE.md         # 架构文档
└── .gitignore
```


## API 端点

| 端点 | 方法 | 兼容承诺 |
|------|------|---------|
| `/v1/messages` | POST | 次版本内只增字段 |
| `/v1/chat/completions` | POST | OpenAI 兼容透传 |
| `/v1/models` | GET | 标准格式 |
| `/health` | GET | 只增字段 |
| `/api/config` | GET/PUT | 只增字段 |
| `/api/custom-models` | GET/PUT | 格式稳定 |
| `/api/logs` | GET | 格式稳定 |
| `/api/refresh-models` | POST | 格式稳定 |


## 提交规范

```
feat: 描述     # 新功能
fix: 描述      # Bug 修复
docs: 描述     # 文档
refactor: 描述 # 重构
release: 描述  # 版本发布
chore: 描述    # 杂项
```