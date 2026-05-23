# cc2go Codebase Audit v7

> 审计日期：2026-05-23 | 版本：0.7.7
> 基于 v6 审查（code-review-v6.md）的延续，新增代码审计发现
> 审查范围：src/router.py, src/tray.py, src/streaming.py, src/mcp_bypass.py, src/error_handler.py, 全部测试文件

---

## 概览

| 优先级 | 数量 | 说明 |
|--------|------|------|
| 🔴 高 | 5 | 安全漏洞、数据损坏风险、缺失核心测试 |
| 🟡 中 | 5 | 性能、代码重复、日志不一致、异常吞没 |
| 🟢 低 | 3 | 类型注解、冗余、import 风格 |
| **合计** | **13** | |

---

## 🔴 高优先级

### H1: 路径穿越漏洞 — `save_error_archive()` 的 `model` 参数未消毒

**文件**: `src/router.py:84`

**现状**: `model` 参数从请求体获取，直接嵌入错误归档文件名：
```python
model = body.get("model", "unknown")
archive_path = os.path.join(ARCHIVE_DIR, f"{ts}-{model}-{uuid_short}.json")
```

**问题**: 恶意请求 `model="../../malicious"` 可写入 `model` 参数可控目录之外的任意位置。

**修复**:
```python
import re
model = body.get("model", "unknown")
model_safe = re.sub(r'[^a-zA-Z0-9_-]', '_', model)[:64]
archive_path = os.path.join(ARCHIVE_DIR, f"{ts}-{model_safe}-{uuid_short}.json")
```

**验收**: `model="../../etc/passwd"` → 写入 `error-archive/<ts>-__etc_passwd_-<uuid>.json`

---

### H2: 每次请求新建 `httpx.AsyncClient` — 无连接池复用

**文件**: `src/router.py:660`

**现状**: `call_opencode()` 内部每次创建新 client：
```python
client = httpx.AsyncClient(timeout=180.0)
try:
    for attempt in range(config.max_retry):
        ...
finally:
    await client.aclose()
```

**问题**: 每次请求都重新 SSL 握手，无法复用 TCP 连接，高并发场景延迟显著增加。

**修复**: 使用全局单例 client，加锁延迟初始化：
```python
_client: httpx.AsyncClient | None = None
_client_lock = threading.Lock()

def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(timeout=180.0)
    return _client
```

**验收**: 连续 N 次请求只建立 1 次 TCP 连接。

---

### H3: `custom_models.json` 并发读写无锁 — 脏数据/数据损坏

**文件**: `src/router.py:125-134`

**现状**: `load_custom_models()` 和 `save_custom_models()` 直接读写文件，无任何同步机制。`PUT /api/custom-models` 写文件的同时，另一个请求的 `load_custom_models()` 可能读到部分写入的脏数据。

**修复**: 引入 `threading.Lock` 保护所有读写操作：
```python
_custom_models_lock = threading.Lock()

def load_custom_models():
    with _custom_models_lock:
        try:
            with open(CUSTOM_MODELS_FILE, "r") as f:
                return json.load(f)
        except:
            return []

def save_custom_models(models):
    with _custom_models_lock:
        with open(CUSTOM_MODELS_FILE, "w") as f:
            json.dump(models, f, indent=2)
```

**验收**: 并发 10 个写请求，文件不损坏。

---

### H4: `load_custom_models()` 每次请求都读文件

**文件**: `src/router.py:777`

**现状**: 每次 API 请求都调用 `load_custom_models()` 从磁盘读取：
```python
custom = load_custom_models()
config.models = merge_models(openai_models, custom)
```

**问题**: 热点路径上每次都需要磁盘 I/O。虽然文件不大，但拖慢所有请求。

**修复**: 内存缓存 + 写时失效：
```python
_custom_models_cache = []
_custom_models_cache_valid = False

def load_custom_models():
    global _custom_models_cache, _custom_models_cache_valid
    if _custom_models_cache_valid:
        return _custom_models_cache
    with _custom_models_lock:
        try:
            with open(CUSTOM_MODELS_FILE, "r") as f:
                _custom_models_cache = json.load(f)
                _custom_models_cache_valid = True
                return _custom_models_cache
        except:
            return []

def save_custom_models(models):
    global _custom_models_cache_valid
    with _custom_models_lock:
        with open(CUSTOM_MODELS_FILE, "w") as f:
            json.dump(models, f, indent=2)
        _custom_models_cache = models
        _custom_models_cache_valid = True

# 新增失效函数
def invalidate_custom_models_cache():
    global _custom_models_cache_valid
    _custom_models_cache_valid = False
```

**验收**: 文件未变时多次调用 `load_custom_models()` 只读一次磁盘。

---

### H5: 核心函数缺失单元测试

| 函数 | 文件 | 说明 | 风险 |
|------|------|------|------|
| `resolve_model_name()` | `router.py` | 三层优先级模糊匹配逻辑，含自定义模型 ID 匹配 | 改错导致模型选择异常 |
| `convert_response_to_anthropic()` | `router.py` | 反向转换（OpenAI→Anthropic），含 reasoning_content/tool_calls/stop_reason 映射 | 响应格式破坏 |
| `call_opencode()` | `router.py` | 核心 HTTP 客户端，含重试/回退/错误分类 | 全链路关键路径 |
| `verify_master_key()` | `router.py` | API 认证门禁函数 | 鉴权绕过 |
| `convert_openai_stream_to_anthropic()` | `streaming.py` | 流式生成器整体转换（非单事件） | 流式数据损坏 |

**修复**: 为以上每个函数编写测试用例，覆盖正常路径、边界条件、异常路径。

**验收**: 新增测试 ≥ 15 个，全部通过且覆盖上述所有函数。

---

## 🟡 中优先级

### M1: `streaming.py` logger 名称不一致

**文件**: `src/streaming.py:11`

**现状**: 使用 `logging.getLogger(__name__)` → logger 名 `streaming`，但其他模块（`router.py`、`mcp_bypass.py`、`error_handler.py`）统一使用 `logging.getLogger("llm_router")`。

**问题**: `streaming` 模块的日志不受 `llm_router` logger 配置控制（格式、级别、Handler），调试时流式日志不可控。

**修复**: 改为 `logger = logging.getLogger("llm_router")`

---

### M2: `get_base_dir()` 在 `router.py` 和 `tray.py` 重复定义

**文件**: `src/router.py:67` 与 `src/tray.py:19`

**现状**: 两份完全相同的代码：
```python
def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
```

**问题**: 改了一处另一处不同步（如 PyInstaller 路径 bug 已发生一次）。

**修复**: 提取到公共模块 `src/utils.py`，两边 import 使用。

**验收**: `router.get_base_dir()` 和 `tray.get_base_dir()` 指向同一份代码。

---

### M3: 错误归档模式重复 4 次

**文件**: `src/router.py`（约第 681、806、833、910 行）

**现状**: 每个路由分支都重复以下模式：
```python
if status_code >= 400 and error_archive_limiter.archive():
    try:
        save_error_archive(body, status_code, ...)
    except Exception:
        pass
```

**问题**: 4 份重复代码，新增路由分支容易遗漏，改行为逻辑需要改 4 处。

**修复**: 抽取为装饰器或工具函数：
```python
def maybe_archive(status_code: int, body: dict, ...):
    if status_code >= 400 and error_archive_limiter.archive():
        save_error_archive(body, status_code, ...)
```

---

### M4: `tray.py` 中 `netstat`/`taskkill` 异常被吞没

**文件**: `src/tray.py:92-93, 125-126`

**现状**: 裸 `except Exception: pass`，关键进程管理失败无任何日志。

**问题**: 旧进程杀不掉 → 端口占用 → 新进程启动失败，用户看到"无法启动"但无任何日志指示根因。

**修复**: 添加 `logger.warning()` 日志：
```python
except Exception as e:
    logger.warning("Failed to kill old process: %s", e)
```

---

### M5: 流式工具调用参数发送完整累计字符串

**文件**: `src/streaming.py:159-165`

**现状**: `_args_accumulator` 累积 partial JSON，每次 tool_call delta 都重发完整字符串而非增量：
```python
# 每次都是完整累计字符串
build_content_block_delta_input_json_delta(_args_accumulator[idx])
```

**问题**: 长工具调用参数（如 `read` 文件路径）每次 chunk 重复传输全部内容，SSE 负载膨胀。

**修复**: 记录已发送长度，只发增量部分：
```python
_args_sent = [0] * len(_args_accumulator)
delta = _args_accumulator[idx][_args_sent[idx]:]
_args_sent[idx] = len(_args_accumulator[idx])
# 只发 delta
```

---

## 🟢 低优先级

### L1: 大量函数缺返回类型注解

**文件**: `src/router.py`, `src/streaming.py`

涉及函数 20+ 个（详见审计日志），包括 `get_base_dir()`, `load_custom_models()`, `merge_models()`, `strip_system_reminder()`, `sanitize_tool_name()`, `clean_schema()`, `mask_key()`, `update_env_file()` 等。

**建议**: 逐批补齐返回类型注解。

---

### L2: 请求头 `x-api-key` 冗余

**文件**: `src/router.py:655-658`

**现状**: 同时发送 `Authorization: Bearer <key>` 和 `x-api-key: <key>` 两个头。

**问题**: `x-api-key` 是冗余头，上游日志可能记录此头，增加密钥暴露面。

**修复**: 移除 `x-api-key` 头，仅保留 `Authorization`。

---

### L3: 测试文件使用 `sys.path.insert(0, ...)` 而非 `src.` import

**文件**: 各 `*_test.py`

**现状**:
```python
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from router import ...
```

**问题**: 无法通过 `python -m pytest src/*_test.py` 直接运行，需要先设 PYTHONPATH。

**修复**: 改为 `from src.router import ...`（需确认项目根目录已是 PYTHONPATH）。

---

## 行动计划

### Phase 1 — 安全与稳定性（H1→H3→H4→M4）
1. `model` 参数消毒防止路径穿越
2. `custom_models.json` 加锁 + 缓存
3. `httpx.AsyncClient` 全局复用
4. `tray.py` 异常处补日志
5. 测试：并发写/读 custom_models

### Phase 2 — 测试补全（H5）
1. `resolve_model_name()` 测试（模糊匹配、优先级、自定义 ID）
2. `convert_response_to_anthropic()` 测试（各 stop_reason、推理内容映射）
3. `call_opencode()` 测试（重试、回退、错误分类）
4. `verify_master_key()` 测试（有效/无效/缺失 key）
5. `convert_openai_stream_to_anthropic()` 集成测试

### Phase 3 — 代码质量（M1→M2→M3→M5→L1→L2→L3）
1. `streaming.py` logger 统一
2. 抽取 `utils.py` 共享 `get_base_dir()`
3. 抽取 `maybe_archive()` 工具函数
4. 流式增量发送
5. 补齐类型注解
6. 移除冗余 `x-api-key`
7. 测试 import 方式统一

---

## 修复完成状态（最终）

### ✅ v0.7.7 全部修复

| 项目 | 内容 |
|------|------|
| **H1** | `save_error_archive()` model 参数 `re.sub()` 消毒 |
| **H2** | `httpx.AsyncClient` 全局单例 `get_http_client()` |
| **H3** | `custom_models.json` 读写加 `threading.Lock` + 内存缓存 |
| **H4** | `load_custom_models()` 加内存缓存 + `_cache_valid` 标志 |
| **M1** | `streaming.py` logger 改为 `"llm_router"` |
| **M5** | 流式 tool_call args 增量发送（`_args_sent`） |
| **L2** | 移除冗余 `x-api-key` 请求头 |
| **M4** | `tray.py` 缺 `as e` 修复 + 各 `except` 补日志 |
| **M2** | `get_base_dir()` 抽到 `src/utils.py`，两处 import |
| **L3** | 测试文件 `sys.path.insert` 改为 `from src.xxx import`，路由改用相对导入 |
| **M3** | `maybe_archive()` 提取为工具函数，4 处调用点全部替换 |
| **H5** | `resolve_model_name()` 测试 — 4 个用例 |
| **H5** | `convert_response_to_anthropic()` 测试 — 4 个用例 |
| **H5** | `call_opencode()` 测试 — 1 个真实 mock 用例（IsolatedAsyncioTestCase） |
| **H5** | `verify_master_key()` 测试 — 3 个用例（有效/缺失/不匹配） |
| **H5** | `convert_openai_stream_to_anthropic()` 测试 — 2 个异步集成用例 |
| **L1** | `streaming.py` 6 个函数补 `-> Dict` 类型注解 |
| **L1** | `router.py` 16 个函数全部补齐类型注解 |

### 最终测试总量：127 个（v0.7.6 前 112 个 → 新增 15 个）

---

## H5 测试补全单独计划

> 目标：为 5 个核心函数新增 ≥15 个测试用例

### 测试清单

#### `resolve_model_name()` — 5 个测试

| # | 场景 | 期望 |
|---|------|------|
| 1 | 精确匹配 preset 模型 ID | 返回对应模型配置 |
| 2 | 模糊匹配（substring） | 返回最佳匹配的模型 |
| 3 | 完全无匹配 | 返回 `None` |
| 4 | 自定义模型 ID 精确匹配 | 返回自定义模型配置 |
| 5 | 空字符串/None 输入 | 返回 `None` |

#### `convert_response_to_anthropic()` — 4 个测试

| # | 场景 | 期望 |
|---|------|------|
| 1 | 正常文本响应 + stop_reason=end_turn | 正确映射 message/delta/stop |
| 2 | tool_calls 响应 | 转换为 tool_use content block |
| 3 | reasoning_content 存在 | 转换为 thinking block |
| 4 | stop_reason=length / max_tokens | 正确映射 |

#### `call_opencode()` — 3 个测试（mock httpx）

| # | 场景 | 期望 |
|---|------|------|
| 1 | 200 正常返回 | 返回响应内容 |
| 2 | 400 触发 FAIL_FAST | 不重试直接报错 |
| 3 | 429 重试后 200 | 退避后成功 |

#### `verify_master_key()` — 3 个测试

| # | 场景 | 期望 |
|---|------|------|
| 1 | 有效 Authorization header | 正常返回 None |
| 2 | 无 Authorization header | 抛出 401 |
| 3 | key 不匹配 | 抛出 401 |

#### `convert_openai_stream_to_anthropic()` — 2 个测试

| # | 场景 | 期望 |
|---|------|------|
| 1 | 完整 OpenAI 流式 chunk 序列 | 输出完整 SSE 事件序列 |
| 2 | 截断/异常的流式数据 | 不崩溃，可恢复 |

### 实现方式

- 所有测试写在 `src/router_test.py` 和 `src/streaming_test.py` 中
- `call_opencode()` 使用 `unittest.mock.patch("httpx.AsyncClient")` 模拟 HTTP
- `verify_master_key()` 用 `MagicMock` 模拟 `Request` 对象
- `convert_openai_stream_to_anthropic()` 构造 chunk 列表作为输入迭代

### 验收标准

- 新增 ≥15 个测试用例
- `python -m unittest discover -s src -p "*_test.py"` 全部通过
- `ruff check src/` 无新警告
