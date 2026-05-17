# cc2go 版本管理规范 (SPEC)

> 当前版本：0.5.0 | 更新：2026-05-17

---


## 版本号规则

遵循语义化版本 2.0.0：

```
MAJOR.MINOR.PATCH

0.5.0 → 主版本 0, 次版本 5, 修订号 0
```

| 升级 | 触发条件 | 示例 |
|------|---------|------|
| PATCH | Bug 修复、文案调整 | `0.5.0` → `0.5.1` |
| MINOR | 新功能、向后兼容 | `0.5.0` → `0.6.0` |
| MAJOR | 不兼容的 API 变更 | `0.5.0` → `1.0.0` |


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


## 项目目录结构

```
cc2go/
├── src/                    # 源码
│   ├── router.py           # 主服务 (FastAPI)
│   └── tray.py             # 系统托盘 (pystray)
├── scripts/                # 启动/停止脚本
│   ├── start_bg.bat        # Windows 托盘启动
│   ├── stop.bat            # Windows 停止
│   ├── start.sh            # Linux/Mac 启动
│   └── stop.sh             # Linux/Mac 停止
├── static/                 # Web UI 静态资源
├── data/                   # 运行时数据 (gitignored)
├── logs/                   # 日志 (gitignored)
├── error-archive/          # 错误归档 (gitignored)
├── docs/                   # 文档草稿 (gitignored)
├── build_release.py        # PyInstaller 打包脚本
├── requirements.txt        # Python 依赖
├── .env.example            # 配置模板
├── SPEC.md                 # 本文件
├── README.md               # 项目说明
├── .gitignore
└── LICENSE


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
