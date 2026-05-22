# cc2go 代码审查问题清单

> 审查时间：2026-05-18
> 审查范围：src/router.py, src/tray.py, build_release.py, scripts/, .env.example, .github/workflows/ci.yml, SPEC.md

---

## 🔴 必须修复（3 项）

### Issue 1: tool_results 排序顺序错误

**文件**: `src/router.py` 第 285-303 行

**现状**:
```python
# 第 286 行
openai_messages.extend(tool_results)   # tool 消息先加入

# 第 289-303 行
if content_items or tool_calls_list:   # assistant 消息后加入
    msg_dict = {"role": role}
    ...
    openai_messages.append(msg_dict)
```

输出顺序：
```
1. tool (role=tool, tool_call_id=xxx)
2. assistant (tool_calls=[...])
```

**问题**: OpenAI API 要求 `tool` 消息必须紧跟在包含 `tool_calls` 的 `assistant` 消息之后。当前 tool_results 在 assistant 消息之前，顺序反了。

这很可能就是日志里反复出现的 `tool_call_ids did not have response messages` 错误的根因。

**正确顺序**:
```
1. assistant (tool_calls=[...])
2. tool (role=tool, tool_call_id=xxx)
```

**修复方案**: 把第 286 行 `openai_messages.extend(tool_results)` 移到第 303 行 `openai_messages.append(msg_dict)` 之后。

修改前：
```python
            # 添加 tool 结果（必须在用户文本之前，满足 OpenAI tool 消息紧跟 tool_calls 的要求）
            openai_messages.extend(tool_results)

            # 合并 content_items 和 tool_calls 到一条消息
            if content_items or tool_calls_list:
                msg_dict = {"role": role}
                ...
                openai_messages.append(msg_dict)
```

修改后：
```python
            # 合并 content_items 和 tool_calls 到一条消息（assistant 消息必须排在 tool 消息之前）
            if content_items or tool_calls_list:
                msg_dict = {"role": role}
                ...
                openai_messages.append(msg_dict)

            # tool 消息必须紧跟在包含 tool_calls 的 assistant 消息之后（OpenAI 要求）
            openai_messages.extend(tool_results)
```

**影响范围**: 所有使用工具调用的请求（Claude Code 的核心场景）

---

### Issue 2: build_release.py 中 stop.bat 模板的 PID 路径过时

**文件**: `build_release.py` 第 116-118 行

**现状**:
```bat
if exist cc2go.pid (
    set /p PID=<cc2go.pid
    taskkill /f /pid %PID% 2>nul
    del cc2go.pid 2>nul
```

**问题**: 代码中 PID_FILE 已改为 `data/cc2go.pid`（tray.py 第 27 行、router.py 第 156 行），但打包脚本生成的 stop.bat 还在找根目录的 `cc2go.pid`。

**修复方案**: 将 `cc2go.pid` 替换为 `data\cc2go.pid`

修改前：
```python
    (RELEASE_DIR / "stop.bat").write_text(
        '@echo off\r\n'
        'chcp 65001 > nul\r\n'
        'cd /d "%~dp0"\r\n'
        '\r\n'
        'if exist cc2go.pid (\r\n'
        '    set /p PID=<cc2go.pid\r\n'
        '    taskkill /f /pid %PID% 2>nul\r\n'
        '    del cc2go.pid 2>nul\r\n'
```

修改后：
```python
    (RELEASE_DIR / "stop.bat").write_text(
        '@echo off\r\n'
        'chcp 65001 > nul\r\n'
        'cd /d "%~dp0"\r\n'
        '\r\n'
        'if exist data\\cc2go.pid (\r\n'
        '    set /p PID=<data\\cc2go.pid\r\n'
        '    taskkill /f /pid %PID% 2>nul\r\n'
        '    del data\\cc2go.pid 2>nul\r\n'
```

注意：Python 字符串中 `\r` 是回车符，`\\r` 才是字面量 `\r`，需要仔细处理转义。实际写入 bat 文件的内容应该是：

```bat
if exist data\cc2go.pid (
    set /p PID=<data\cc2go.pid
    taskkill /f /pid %PID% 2>nul
    del data\cc2go.pid 2>nul
```

---

### Issue 3: CI 配置路径过时

**文件**: `.github/workflows/ci.yml` 第 29-33 行

**现状**:
```yaml
- name: Run tests
  run: |
    python -c "from router import app; print('Import OK')"
    python -c "from router import Config; print('Config OK')"

- name: Lint
  run: |
    pip install ruff
    ruff check router.py || true
```

**问题**: 源码已移到 `src/` 目录，但 CI 还在根目录找 `router.py`，会导致 import 失败和 lint 跳过。

**修复方案**:

```yaml
- name: Run tests
  run: |
    cd src
    python -c "from router import app; print('Import OK')"
    python -c "from router import Config; print('Config OK')"

- name: Lint
  run: |
    pip install ruff
    ruff check src/ || true
```

同时在 `Install dependencies` 步骤前加一根目录的 `__init__.py` 也不是必须的，`cd src` 就够了。

---

## 🟡 建议改进（4 项）

### Issue 4: .env.example 缺少新增配置项

**文件**: `.env.example`

**现状**: 只有 10 个配置项，缺少以下 5 个已在 `router.py Config` 类中使用的配置：

**缺失项**:
```
DETAILED_LOGGING=true
SELECTED_MODEL=
CLAUDE_MODEL_ALIAS=
CLAUDE_SETTINGS_PATH=~/.claude/settings.json
LOG_FILE=logs/router.log
```

**修复方案**: 在 `.env.example` 末尾补全：

```ini
# 日志详细程度（true 打印请求/响应详情，false 只打印摘要）
DETAILED_LOGGING=true

# 默认选中模型（留空则使用客户端传来的模型名）
SELECTED_MODEL=

# Claude Code 中显示的模型别名（留空=使用实际模型名，设成 claude-sonnet-4-20250514 可放开图片发送）
CLAUDE_MODEL_ALIAS=

# Claude Code 配置文件路径（用于自动同步模型名和连接信息）
CLAUDE_SETTINGS_PATH=~/.claude/settings.json

# 日志文件路径（默认 logs/router.log）
LOG_FILE=logs/router.log
```

另外 `LOG_FILE` 当前的默认值 `router.log`（根目录）应改为 `logs/router.log`。

---

### Issue 5: tray.py 用了 requests 库（可减少依赖）

**文件**: `src/tray.py` 第 12 行，`requirements.txt` 第 7 行

**现状**: `tray.py` import 了 `requests`（同步 HTTP），但项目本身全部使用 `httpx`（异步 HTTP）。为了托盘的 3 个同步调用额外引入了 `requests` 依赖。

**建议**: 用 Python 标准库 `urllib.request` 替代，减少一个外部依赖（`requests` 可从 `requirements.txt` 移除）。

涉及 3 个函数：
- `get_current_model()` — GET 请求
- `get_models_list()` — GET 请求
- `switch_model()` — PUT 请求

替换示例：
```python
import urllib.request
import urllib.error
import json

def _api_request(method, path, data=None, timeout=3):
    """简单的同步 HTTP 请求，使用标准库"""
    url = f"http://127.0.0.1:{config.router_port}{path}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError):
        return None

def get_current_model():
    cfg = _api_request("GET", "/api/config")
    return cfg.get("selected_model", "") if cfg else ""

def get_models_list():
    cfg = _api_request("GET", "/api/config")
    return cfg.get("models", []) if cfg else []

def switch_model(model_name):
    _api_request("PUT", "/api/config", data={"selected_model": model_name}, timeout=5)
```

**优先级**: 中。功能正常，只是多了一个依赖。

---

### Issue 6: stop.bat 的 PID 读取方式不够健壮

**文件**: `scripts/stop.bat` 第 5 行

**现状**:
```bat
set /p PID=<data\cc2go.pid
```

**问题**: Windows `set /p` 在 `()` 块内需要 `enabledelayedexpansion` 才能正确读取变量。另外如果 PID 文件有尾部空行，可能读入空值。

**修复方案**:
```bat
for /f %%i in (data\cc2go.pid) do set PID=%%i
```

完整修改后的 `scripts/stop.bat`:
```bat
@echo off
chcp 65001 > nul
cd /d "%~dp0.."

if exist data\cc2go.pid (
    for /f %%i in (data\cc2go.pid) do set PID=%%i
    taskkill /f /pid %PID% 2>nul
    del data\cc2go.pid 2>nul
    echo cc2go stopped.
) else (
    echo No PID file found. cc2go may not be running, or use task manager to find and kill python process.
)
pause
```

---

### Issue 7: stop.sh 未利用 PID 文件

**文件**: `scripts/stop.sh`

**现状**: 用 `pkill` 全局匹配进程名，与 `stop.bat` 的 PID 文件方式不一致。

**建议**: 统一为读取 PID 文件的方式：
```bash
#!/bin/bash
# cc2go 停止脚本 (Linux/Mac)
cd "$(dirname "$0")/.."

if [ -f data/cc2go.pid ]; then
    PID=$(cat data/cc2go.pid)
    kill "$PID" 2>/dev/null && echo "cc2go stopped (PID=$PID)." || echo "Process $PID not found."
    rm -f data/cc2go.pid
else
    echo "No PID file. Trying pkill..."
    pkill -f "src/router\.py" 2>/dev/null
    pkill -f "src/tray\.py" 2>/dev/null
    echo "Done."
fi
```

---

## ✅ 审查通过（无问题）

| 项目 | 说明 |
|------|------|
| 版本号管理 | 仅 `src/router.py` 的 `VERSION` 一处定义，tray.py/build_release.py 引用 ✅ |
| .gitignore | 正确排除 data/、logs/、error-archive/、.env、docs/ ✅ |
| 错误归档 | `save_error_archive()` 3 处调用点覆盖了所有 400 错误 ✅ |
| 托盘 checked | pystray `checked=lambda item, n=name: n == get_current_model()` 闭包正确 ✅ |
| SPEC.md | 目录结构描述与实际一致 ✅ |
| tray.py get_base_dir | 正确返回项目根目录（`dirname(dirname(__file__))`） ✅ |
| router.py 日志位置 | `LOG_FILE` 默认值为 `logs/router.log` ✅ |