# cc2go 代码审查问题清单 v4

> 审查时间：2026-05-18
> 审查范围：全项目（src/router.py, src/tray.py, build_release.py, scripts/, .env.example, .github/workflows/ci.yml, SPEC.md, 等）

---

## 🟡 建议改进（3 项）

### Issue 11: `load_custom_models()` 在失败兜底路径中被重复调用

**文件**: `src/router.py` `refresh_models()` 函数

`refresh_models()` 的成功路径（上游拉取成功）已正确缓存 `load_custom_models()` 结果。但失败兜底路径（读缓存和最终默认列表）各自单独调用 `load_custom_models()`，未复用缓存。

**当前代码**（成功路径已修，失败路径未修）:
```python
# 成功路径（已修）
custom = load_custom_models()
config.models = merge_models(new_models, custom)
logger.info(...)
# 缓存兜底（未修）
config.models = merge_models(cached, load_custom_models())
# 默认兜底（未修）
config.models = merge_models(DEFAULT_MODELS, load_custom_models())
```

**修复**: 在函数入口处统一缓存一次结果，所有路径复用。

---

### Issue 13: `save_stats()` 每次请求都写文件

**文件**: `src/router.py` `save_stats()` 函数

当前代码已有批处理优化（每 10 次请求才实际写盘），但仍有改进空间：
```python
def save_stats(force=False):
    global _stats_dirty
    _stats_dirty += 1
    if not force and _stats_dirty < 10:
        return
    with open(STATS_FILE, "w") as f:
        json.dump(...)
    _stats_dirty = 0
```

**建议（可选优化）**:
- 改用定时器（如每 60 秒）或退出时统一保存
- 程序退出时补充 `atexit.register(save_stats, force=True)`

---

### Issue 14: 裸 `except:` 语句过多

**文件**: `src/router.py` 和 `src/tray.py`

项目中有大量裸 `except:`（不指定异常类型），这会在出现 `KeyboardInterrupt`、`SystemExit` 等非预期异常时吞掉它们：

| 文件 | 行号（约） | 上下文 |
|------|-----------|--------|
| router.py | 85 | `load_custom_models()` |
| router.py | 161 | `load_stats()` |
| router.py | 167 | `save_stats()` |
| router.py | 152 | `StaticFiles mount` |
| router.py | 510 | `json.loads` fallback |
| router.py | 535 | `json.loads` fallback |
| router.py | 1525 | `refresh_models` 缓存写入 |
| router.py | 1531 | `refresh_models` 缓存读取 |
| router.py | 1543 | `refresh_models` 最终兜底 |
| tray.py | 41 | `remove_pid()` |
| tray.py | 51 | `load_icon()` |
| tray.py | 78 | `_api_request()` |

**建议**: 全部改为 `except Exception:`，除了明确的 `except:` 用于兜底的情况。

---

## 🟢 小问题（不影响功能）

### ~~Issue 15: docs/ 在 .gitignore 中~~ 已修复

**文件**: `.gitignore`（原第 45 行）

**修复**: 已从 `.gitignore` 中移除 `docs/` 规则，代码审查文档现在可被 git 跟踪。

---

### ~~Issue 17: Release 打包缺少 `data/` 目录~~ 已修复

**文件**: `build_release.py` 第 21-24 行 `REQUIRED_FILES`，第 77-104 行 `collect_release()`

**现状**:
```python
REQUIRED_FILES = [
    "static",
    ".env.example",
]   # ← 缺少 "data"
```

`data/` 目录在 `.gitignore` 中，打包时不会被复制。运行时 `save_pid()` 写 `data/cc2go.pid` 会失败（目录不存在），裸 `except:` 会静默吞掉错误，`stop.bat` 因找不到 PID 文件而无法正常停止进程。

**修复方案**: 在 `collect_release()` 中显式创建空目录：

```python
# collect_release() 中
data_dir = RELEASE_DIR / "data"
data_dir.mkdir(exist_ok=True)
```

**验收标准**: 打包后的 ZIP 解压运行，`data/cc2go.pid` 能正常写入。

**状态**: 2026-05-17 已修复：`build_release.py` `collect_release()` 中显式创建 `data/` 和 `logs/` 目录。同时修了 `get_base_dir()` 对 PyInstaller 返回 `sys._MEIPASS` 而非 EXE 目录的 bug，确保 `data/`、`logs/`、`.env` 都在 EXE 同目录下工作。

---

### ~~Issue 18: 自定义模型保存报错~~ 已修复

**现象**: `PUT /api/custom-models` 接口成功，但管理页报错 `innerHTML`，新模型不显示。刷新页面后正常。

**根因**: `saveCustomModal()` 保存成功后，`renderCustomModels()` 和 `load()` 使用的是**本地旧的 `customModels` 数组**，没有从服务端重新拉取。`renderCustomModels()` 用旧列表渲染导致下标错位，触发 innerHTML 相关 JS 错误。

**修复**: 保存成功后主动 `GET /api/custom-models` 重新拉取，再渲染。

```javascript
api('PUT','/api/custom-models', customModels).then(async () => {
    ...
    customModels = await api('GET','/api/custom-models');  // 新增
    renderCustomModels();
    await load();
});
```

---

### ~~Issue 19: 自定义模型保存后未出现在系统托盘~~ 已随 Issue 18 修复

同根因：保存后未重新拉取 `customModels`，导致托盘 watcher 信号触发时 `refresh_tray_menu()` 拿到的哈希值未变，菜单未重建。

修复 Issue 18 后，`customModels` 正确更新，`load()` 重新渲染模型标签，托盘 watcher 信号触发时哈希值变化，菜单正常刷新。

---

### ~~Issue 20: CI workflow 缺少 Release 文件说明~~ 已修复

**文件**: `build_release.py` 生成的 `README.md`

**修复**: README.md 文件说明表已补充 `data/` 和 `logs/` 目录行，`stop.bat` 说明已标注 PID 路径依赖。

---

### Issue 21: 托盘菜单中自定义模型无标记（已修复）

**文件**: `src/tray.py` 第 105-130 行 `build_model_menu()`

**现象**: 托盘「切换模型」菜单中，自定义模型和内置模型外观完全相同，无法区分。

**修复**: 构建模型菜单时调用 `GET /api/custom-models`，自定义模型显示名称加 `★` 后缀。

```python
def get_custom_model_ids():
    cm = _api_request("GET", "/api/custom-models")
    return [m["id"] for m in (cm or [])]

def build_model_menu():
    models = get_models_list()
    custom_ids = set(get_custom_model_ids())
    for name in sorted(models):
        label = name + " ★" if name in custom_ids else name
        items.append(pystray.MenuItem(label, ...))
```

---

## ✅ 确认无问题（含已修复项）

| 项目 | 说明 |
|------|------|
| Issue 9-NEW: 托盘菜单动态刷新 | 已修复：信号驱动 `menu_watcher` + `model_change_signal` ✅ |
| Issue 9: getVal/getChecked | 已修复：`src/router.py:1348-1349` 定义 ✅ |
| Issue 10: import re 位置 | 已修复：在文件顶部 `src/router.py:9` ✅ |
| Issue 12: CI 分支 | 已修复：`.github/workflows/ci.yml` 使用 `master` ✅ |
| Issue 16: README 缺少 stop.bat | 已修复：`build_release.py` README 已包含 ✅ |
| Issue 17: Release 缺少 data/ 目录 | 已修复：`build_release.py` `collect_release()` 创建 ✅ |
| get_base_dir() 对 PyInstaller 返回 EXE 目录 | 已修复：不再返回 `sys._MEIPASS` ✅ |
| tool_results 排序 | 正确：跨消息迭代，tool 消息紧跟 assistant 之后 ✅ |
| 轮询定时器泄漏 | 已修复：`clearInterval` + `beforeunload` ✅ |
| requirements.txt 换行 | 已修复 ✅ |
| build_release.py PID 路径 | 已修复：`data\cc2go.pid` ✅ |
| CI 路径 | 已修复：`from src.router import`, `ruff check src/` ✅ |
| .env.example 配置项 | 已补全 ✅ |
| tray.py 使用 urllib | 已修复：移除 requests 依赖 ✅ |
| stop.bat PID 读取 | 已修复：`for /f %%i` ✅ |
| stop.sh PID 文件 | 已修复 ✅ |
| 托盘菜单 checked 回调 | 正确：闭包捕获 + 每次展开实时查询 ✅ |
| extract_reasoning_text 替代 | `strip_reasoning` 行为正确 ✅ |
