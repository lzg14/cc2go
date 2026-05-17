# 代码审查问题清单

> 审查提交：c1f8bf8 + 0d90996
> 审查时间：2026-05-17
> 状态：✅ 已修复

---

## 🔴 高优先级

### 1. stop.bat 可能误杀系统中所有 Python 进程（严重）

**文件**：`stop.bat`
**问题描述**：
```batch
taskkill /f /im python.exe /fi "WINDOWTITLE eq cc2go*" 2>nul
```
`start_bg.bat` 中使用的是 `Start-Process -WindowStyle Hidden`，启动的进程**没有窗口标题**，因此 `/fi "WINDOWTITLE eq cc2go*"` 过滤器**永远不会匹配**。

这导致实际行为变成：强制杀死系统中**所有** `python.exe` 进程。如果用户同时运行了其他 Python 程序（如 IDE、数据分析脚本、其他服务），会被无辜终止。

**修复**：改用 PID 文件方式（`tray.pid`），`stop.bat` 通过 PID 精准终止进程，不再有 `taskkill` 全杀 fallback。

---

## 🟡 中优先级

### 2. detailed_logging 模式逻辑变更需确认

**文件**：`router.py` -> `anthropic_messages` 函数
**问题描述**：

原代码（detailed_logging 模式下直接返回 JSONResponse，跳过统计）：
```python
if config.detailed_logging:
    ...
    return JSONResponse(content=result)  # 直接返回，不执行后续统计
```

新代码（detailed_logging 模式下只记录日志，继续执行后续统计代码）：

**确认**：统计请求数始终记录是预期行为（`request_count` 和 `error_count` 应反映真实使用情况，不应因日志级别跳过）。已加注释说明。

---

### 3. start_bg.bat PID 管理缺失

**文件**：`start_bg.bat`
**问题描述**：
启动脚本没有记录进程 PID，导致 stop.bat 无法精确找到并终止对应进程。

**修复**：`tray.py` 启动时自动写入 `tray.pid`，`stop.bat` 读取该文件精准终止。

---

### 4. .env 创建逻辑格式不匹配

**文件**：`start_bg.bat`
**问题描述**：
```batch
if not exist .env (
    echo Creating default .env...
    copy config.yaml.example .env 2>nul
)
```
项目配置使用的是 `.env`（key=value 格式），但复制来源是 `config.yaml.example`（YAML 格式），两者格式不兼容，可能导致程序解析失败。

**修复**：`config.yaml.example` 重命名为 `.env.example`，避免命名混淆。实际文件内容本就是 KEY=VALUE 格式，仅文件名有误导。所有引用已同步更新。

---

## 🟢 低优先级 / 建议优化

### 5. AsyncClient 异常时连接复用风险

**文件**：`router.py` -> `call_opencode` 函数
**问题描述**：
```python
async with httpx.AsyncClient(timeout=180.0) as client:
    for attempt in range(config.max_retry):
        response = await client.post(...)
```

`AsyncClient` 放在重试循环外层，优点是复用连接池。但如果失败原因是连接层问题（连接被重置、SSL 握手失败），同个 client 重试可能复用有问题的底层连接。

**修复**：`AsyncClient` 移到重试循环内层，每次重试时重建 client，避免复用坏连接。

---

### 6. 刷新模型列表按钮被删除

**文件**：`router.py`（HTML 内嵌部分）
**确认**：刷新按钮（`<button onclick="fetchModels()">`）和 `fetchModels()` 函数均存在且正常工作，无需修复。

---

## 修复检查清单

- [x] stop.bat 改为 PID 文件方式精确终止进程
- [x] start_bg.bat / tray.py 写入 PID 文件
- [x] 确认 detailed_logging 逻辑变更是预期行为
- [x] 修复 .env 创建来源格式问题（`config.yaml.example` → `.env.example`）
- [x] AsyncClient 连接异常时重建 client
- [x] 确认模型列表刷新入口存在

---

## 附：本次审查中确认正确的修复（无需改动）

1. ✅ 删除重复的 `console_handler = logging.StreamHandler()`
2. ✅ 删除 HTML 中重复的 `</style>` 结束标签
3. ✅ 优化 `sync_claude_settings()` 调用频率（只在相关配置变更时触发）
4. ✅ 移除 MiniMax 死代码注释
