# 文件夹结构调整审查

> 审查时间：2026-05-17
> 状态：基本通过，3 个问题需修复

---

## 当前目录结构

```
cc2go/
├── src/                    # 源代码（router.py, tray.py）
├── scripts/                # 启动脚本（.bat, .sh）
├── static/                 # 静态资源（favicon, 图标）
├── data/                   # 运行时数据（pid, stats, custom_models, models_cache）
├── logs/                   # 日志文件
├── error-archive/          # 错误归档（400 错误上下文）
├── docs/                   # 文档（审查清单、规格说明）
├── .github/workflows/      # CI 配置
├── build_release.py        # PyInstaller 打包脚本
├── requirements.txt        # Python 依赖
├── .env                    # 用户配置（gitignore）
├── .env.example            # 默认配置模板
├── config.yaml             # 旧配置格式（？）
├── README.md               # 项目说明
├── LICENSE                 # 许可证
├── .gitignore              # Git 忽略规则
```

---

## ✅ 通过的部分

| 项目 | 说明 |
|------|------|
| `src/` 目录 | 代码集中，结构清晰 |
| `get_base_dir()` 调整 | `src/router.py` 和 `src/tray.py` 都正确返回项目根目录 |
| 数据文件集中 | `custom_models.json`、`stats.json`、`models_cache.json` 都放到了 `data/` |
| 启动脚本路径更新 | `src\tray.py`、`src\router.py` 路径正确 |
| gitignore 覆盖 | `data/`、`logs/`、`error-archive/` 已加入 `.gitignore` |
| `build_release.py` 导入 | `from src.router import VERSION` 正确 |

---

## 🔴 需要修复

### 1. `.claude/` 目录未加入 gitignore

**问题**：`.claude/` 包含本地 Claude Code 配置，不应提交到 git。

**修复**：在 `.gitignore` 中添加：
```
# Claude Code 本地配置
.claude/
```

---

### 2. `config.yaml` 疑似遗留文件

**问题**：`config.yaml` 仍在根目录，但代码现在使用 `.env` 配置。确认是否还需要 `config.yaml`：

- 如果不再需要：删除 `config.yaml`，并在 `.gitignore` 中移除 `config.yaml` 条目
- 如果仍有用途：说明用途，保留

**当前代码**：`router.py` 中使用 `python-dotenv` 加载 `.env`，没有看到 `config.yaml` 的引用。

---

### 3. PyInstaller `add-data` 路径需确认

**问题**：当前 `build_release.py` 中：
```python
"--add-data", f"static{os.pathsep}static",
```

在 PyInstaller onefile 模式下，`static/` 目录被打包到 `sys._MEIPASS/static/`。

`src/router.py` 中：
```python
_sd = os.path.join(get_base_dir(), "static")
```

`get_base_dir()` 在打包后返回 `sys._MEIPASS`，所以路径变成 `sys._MEIPASS/static`，与 `--add-data` 的目标一致。

**验证方式**：打包后检查 `dist/cc2go.exe` 运行时是否能正确加载 `static/` 下的图标。

---

## 🟡 建议（非阻塞）

### 4. `data/custom_models.json` 首次运行

**问题**：`data/` 目录里没有 `custom_models.json`，首次运行时代码会尝试创建它（因为 `load_custom_models()` 有 try/except）。但最好在首次启动时确保文件存在。

**当前逻辑**：
```python
def load_custom_models():
    try:
        with open(CUSTOM_MODELS_FILE, "r") as f:
            return json.load(f)
    except:
        return []
```

首次运行返回 `[]`，程序会继续。这不是 bug，只是用户体验问题（首次运行时没有自定义模型）。

### 5. `scripts/start_bg.bat` 的提示路径

**当前**：
```batch
echo To stop, run: scriptsackslash stop.bat
```

用户可能在 `cc2go/` 根目录或 `scripts/` 目录运行，提示路径需要确认是否总是正确。

---

## 修复检查清单

- [ ] `.gitignore` 添加 `.claude/`
- [ ] 确认 `config.yaml` 是否仍需保留
- [ ] PyInstaller 打包后验证 `static/` 资源是否能正确加载
- [ ] （可选）首次启动时自动创建 `data/custom_models.json` 空文件
