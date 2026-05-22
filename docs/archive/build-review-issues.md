# PyInstaller 打包审查问题清单

> 审查提交：4125d63 feat: 添加 PyInstaller 打包脚本，支持一键生成 Release
> 审查时间：2026-05-17
> 状态：待修复

---

## 🔴 严重问题（会导致打包后无法运行）

### 1. static 目录资源路径在 PyInstaller 打包后会出错

**文件**：`router.py`、`tray.py`

**问题描述**：
两文件中均使用 `__file__` 拼接资源路径：

```python
# tray.py:36
icon_path = os.path.join(os.path.dirname(__file__), "static", "favicon-32x32.png")

# router.py:110
_sd = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=_sd), name="static")
```

PyInstaller `--onefile` 打包后，程序解压到临时目录 `_MEIPASS` 运行，`__file__` 指向临时目录。但 `static/` 目录资源打包进去后，路径需要通过 `sys._MEIPASS` 访问。原代码直接用 `__file__` **打包后找不到 static 目录**。

**修复建议**：在 `router.py` 和 `tray.py` 中增加 PyInstaller 兼容的路径解析：

```python
import sys
import os

def get_base_dir():
    """获取程序运行目录，兼容 PyInstaller 打包后的情况"""
    if getattr(sys, 'frozen', False):
        # PyInstaller onefile 模式：资源在 sys._MEIPASS 目录
        return sys._MEIPASS
    else:
        return os.path.dirname(os.path.abspath(__file__))
```

然后将所有资源路径改用 `get_base_dir()`：

```python
# tray.py
icon_path = os.path.join(get_base_dir(), "static", "favicon-32x32.png")

# router.py
_sd = os.path.join(get_base_dir(), "static")
```

---

### 2. 缺少 webbrowser 的 hidden-import

**文件**：`build_release.py` -> `build_exe()`

**问题描述**：
`tray.py` 第 9 行 `import webbrowser`，用于"打开管理页"功能。但 `build_exe()` 只显式声明了以下 hidden-import：

```python
--hidden-import", "httpx",
--hidden-import", "pystray",
--hidden-import", "PIL",
--hidden-import", "PIL._imaging",
```

缺少 `webbrowser`。打包后的 `cc2go-tray.exe` 点击"打开管理页"时会报 `ModuleNotFoundError: No module named 'webbrowser'`。

**修复建议**：在 `build_exe()` 的 PyInstaller 命令中添加：

```python
cmd.extend([
    "--hidden-import", "webbrowser",
])
```

---

### 3. .env.example 没有打包

**文件**：`build_release.py` -> `REQUIRED_FILES`

**问题描述**：
```python
REQUIRED_FILES = [
    "static",
    ".env.example",
    "custom_models.json",
]
```

`.env.example` 在列表中，但当前 `collect_release()` 只检查文件是否存在，不存在时不报错。实际打包后 release 目录中确实会有 `.env.example`，但需要确认。

**修复建议**：确认 `.env.example` 已在 `REQUIRED_FILES` 中，且 `collect_release()` 中对其的处理逻辑正确（不存在时应提示警告而非静默跳过）。

---

## 🟡 中等问题（可能引发用户困惑或 bug）

### 4. custom_models.json 打包后会覆盖用户已有配置

**文件**：`build_release.py` -> `REQUIRED_FILES`

**问题描述**：
`REQUIRED_FILES` 包含 `custom_models.json`，解压 zip 时会把打包时自带的空数组 `[]` 覆盖用户已有的自定义模型列表，导致用户丢失之前的配置。

**修复建议**：从 `REQUIRED_FILES` 中移除 `custom_models.json`，程序运行时如果文件不存在会自动创建空文件。

```python
REQUIRED_FILES = [
    "static",
    ".env.example",
    # "custom_models.json",  # 移除，运行时自动生成
]
```

---

### 5. stop.bat 用 taskkill /im 容易误杀其他进程

**文件**：`build_release.py` -> `collect_release()` 中的内嵌 `stop.bat`

**问题描述**：
```batch
taskkill /f /im cc2go.exe 2>nul
taskkill /f /im cc2go-tray.exe 2>nul
```

用进程名终止，如果用户目录下有其他同名程序会被误杀。源码版的 `stop.bat` 已改用 PID 文件精准终止，但 release 包里的 `stop.bat` 仍用 `taskkill /im`。

**修复建议**：release 版的 `stop.bat` 也采用 PID 文件方式：

```batch
@echo off
chcp 65001 > nul
cd /d "%~dp0"

if exist cc2go.pid (
    set /p PID=<cc2go.pid
    taskkill /f /pid %PID% 2>nul
    del cc2go.pid 2>nul
    echo cc2go stopped.
) else (
    taskkill /f /im cc2go.exe 2>nul
    taskkill /f /im cc2go-tray.exe 2>nul
    echo cc2go stopped.
)
```

同时需要确保 `tray.py` 写入的 PID 文件名改为 `cc2go.pid`（而不是 `tray.pid`），或两个名字都兼容。

---

### 6. release 包缺少 models_cache.json 和 stats.json 模板

**问题描述**：
程序首次运行会在同目录生成 `models_cache.json` 和 `stats.json`。虽然程序会自动创建，但如果 release 包里没有模板，用户解压后第一次运行会有"多了两个文件"的感觉，不够整洁。

**修复建议**（可选）：在 release 目录中放两个 `.gitkeep` 或空模板文件，让用户知道这是程序生成的缓存文件。

---

## 🟢 轻微问题

### 7. 缺少 fastapi、uvicorn、dotenv 的 hidden-import 显式声明

**文件**：`build_release.py` -> `build_exe()`

**问题描述**：
PyInstaller 的自动分析（`pyinst`）通常能通过 `import` 语句自动发现依赖，但显式声明更稳健。当前只有 4 个 `hidden-import`，建议补充。

**修复建议**：
```python
cmd.extend([
    "--hidden-import", "fastapi",
    "--hidden-import", "uvicorn",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols",
    "--hidden-import", "uvicorn.protocols.http",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan",
    "--hidden-import", "uvicorn.lifespan.auto",
    "--hidden-import", "python_dotenv",
    "--hidden-import", "httpx",
    "--hidden-import", "pystray",
    "--hidden-import", "PIL",
    "--hidden-import", "PIL._imaging",
    "--hidden-import", "webbrowser",
])
```

### 8. 启动打印版本号 ASCII 框对齐风险

**文件**：`router.py:1469`

**问题描述**：
```python
print(f"║                    cc2go v{VERSION}                        ║")
```

当前 "cc2go v0.5.0" 是 12 字符，填充空格后恰好对齐。但如果后续版本变成 `v0.5.0-beta.1` 或 `v1.10.0`，ASCII 框会错位。

**修复建议**：改用动态空格填充：
```python
version_str = f"cc2go v{VERSION}"
padding = max(0, 44 - len(version_str))
print(f"║  {version_str}{' ' * padding}  ║")
```

---

### 9. --collect-all pystray 会打包大量无关 ico 文件

**文件**：`build_release.py` -> `build_exe()`

**问题描述**：
```python
"--collect-all", "pystray",
```

pystray 库自带很多 `.ico` 图标文件，`--collect-all` 会全部打包进 exe，导致文件体积膨胀。实际上只需要自己的 `static/` 目录，不需要 pystray 内置图标。

**修复建议**：删除 `--collect-all pystray`，只保留 `--add-data "static;static"` 已足够。

```python
# 删除这一行
# "--collect-all", "pystray",
```

---

## 修复检查清单

- [ ] `router.py` 和 `tray.py` 资源路径改用 `sys._MEIPASS` 兼容写法
- [ ] `build_release.py` 添加 `webbrowser` hidden-import
- [ ] 确认 `.env.example` 正确打包
- [ ] `REQUIRED_FILES` 移除 `custom_models.json`
- [ ] release 包 `stop.bat` 改用 PID 文件方式（与 tray.py PID 文件名对齐）
- [ ] （可选）release 包加入 `models_cache.json` / `stats.json` 空模板
- [ ] 补充 `uvicorn` / `fastapi` / `python_dotenv` hidden-import 显式声明
- [ ] 启动打印版本号改用动态填充
- [ ] 删除 `--collect-all pystray`

---

## 附：PyInstaller onefile 模式关键知识点

打包 `tray.py`（托盘程序）时需要 `--windowed` 模式，避免弹出控制台窗口。但 `--windowed` 模式下 `print()` 和异常回溯会写到 `%TEMP%` 下的临时文件中，建议配合日志重定向。

`static/` 目录必须用 `--add-data "static;static"` 打包进去（分号 `;` 是 Windows 上 PyInstaller 的路径分隔符，macOS/Linux 用 `:`）。打包后在 `sys._MEIPASS` 目录下访问。