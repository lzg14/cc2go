# cc2go 代码审查问题清单 v3

> 审查时间：2026-05-18
> v2 审查的 7 项问题中 6 项已修复，1 项未修复（Issue 1），新增 1 项

---

## 🔴 必须修复（1 项，遗留）

### Issue 1: tool_results 排序顺序错误（未修复）

**文件**: `src/router.py` 第 285-303 行

**现状**:
```python
# 第 286 行：tool 消息先加入
openai_messages.extend(tool_results)

# 第 289-303 行：assistant 消息后加入
if content_items or tool_calls_list:
    msg_dict = {"role": role}
    ...
    openai_messages.append(msg_dict)
```

输出顺序：
```
1. tool (role=tool, tool_call_id=xxx)       ← 先
2. assistant (tool_calls=[...])               ← 后
```

**正确顺序**（OpenAI API 要求）：
```
1. assistant (tool_calls=[...])               ← 先
2. tool (role=tool, tool_call_id=xxx)          ← 紧跟
```

OpenAI API 要求 `tool` 消息必须紧跟在包含 `tool_calls` 的 `assistant` 消息之后。当前 tool_results 在 assistant 之前，顺序反了。

这很可能就是 `log-analysis.md` 中记录的 `tool_call_ids did not have response messages` 错误的根因。

**修复方案**: 把第 286 行移到第 303 行之后。

修改前（当前代码）：
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

---

## 🟡 建议改进（1 项，新增）

### Issue 8: 管理页轮询定时器泄漏

**文件**: `src/router.py` 的 ADMIN_HTML 末尾（约第 1488-1500 行）

**现状**:
```javascript
let _lastModel = '';
let _pollTimer = null;
async function autoRefresh() {
  try {
    const cfg = await api('GET','/api/config');
    if (cfg.selected_model !== _lastModel) {
      _lastModel = cfg.selected_model || '';
      await loadCustomModels();
      await load();
    }
  } catch(e) {}
}
setTimeout(() => { _pollTimer = setInterval(autoRefresh, 10000); }, 5000);
```

**问题**: 每次页面刷新都会创建一个新的 `setInterval`，旧的定时器不会被清理。用户反复刷新管理页会导致 N 个轮询叠加，每 10 秒发出 N 个请求。

**修复方案**:

```javascript
let _lastModel = '';
let _pollTimer = null;
async function autoRefresh() {
  try {
    const cfg = await api('GET','/api/config');
    if (cfg.selected_model !== _lastModel) {
      _lastModel = cfg.selected_model || '';
      await load();  // load() 内部已包含模型列表渲染，不需要单独再调 loadCustomModels
    }
  } catch(e) {}
}
function startPolling() {
  if (_pollTimer) clearInterval(_pollTimer);   // 清掉旧定时器
  _pollTimer = setInterval(autoRefresh, 10000);
}
setTimeout(startPolling, 5000);
window.addEventListener('beforeunload', () => {
  if (_pollTimer) clearInterval(_pollTimer);     // 页面关闭时清理
});
```

附带优化：`loadCustomModels()` 不需要单独调用，因为 `load()` 内部渲染模型列表时已经使用了 `customModels` 数组。

---

## ✅ v2 审查修复确认

| # | v2 问题 | 状态 | 修复内容 |
|---|---------|------|---------|
| 2 | build_release.py PID 路径 | ✅ 已修 | `cc2go.pid` → `data\cc2go.pid`，`set /p` → `for /f` |
| 3 | CI 路径过时 | ✅ 已修 | `from src.router import`，`ruff check src/` |
| 4 | .env.example 缺配置项 | ✅ 已修 | 补了 5 个配置项 + LOG_FILE 路径更新 |
| 5 | tray.py 用 requests | ✅ 已修 | 改用 `urllib.request` 标准库，`requirements.txt` 移除 `requests` |
| 6 | stop.bat PID 读取 | ✅ 已修 | `set /p` → `for /f %%i`，去掉冗余 del |
| 7 | stop.sh 未用 PID 文件 | ✅ 已修 | 先读 PID 文件，找不着才 pkill |

---

## 🟢 小问题（不影响功能）

### requirements.txt 末尾缺少换行符

```
Pillow>=10.0.0
← 这里没有换行符
```

部分工具（如 pip、cat）对没有尾换行的文件可能报警告。建议在文件末尾加一个空行。