# 遗留问题修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 code-review-v6.md 中 4 个遗留问题

**Architecture:** 4 个独立改动，可并行实施
- Task 1: ADMIN_HTML 分离（静态文件替换嵌入式 HTML）
- Task 2: save_stats() 冗余函数删除
- Task 3: 流式路径补充 increment_requests()
- Task 4: convert_openai_stream_to_anthropic 添加异常处理

**Tech Stack:** Python / FastAPI / Starlette

---

## Task 1: ADMIN_HTML 分离

**Files:**
- Create: `static/index.html`（从 router.py 提取）
- Modify: `src/router.py:1335`（改为 FileResponse）
- Test: 启动服务，访问 http://127.0.0.1:4001/ 验证页面正常加载

### 步骤

- [ ] **Step 1: 找到 ADMIN_HTML 起始位置**

```python
# src/router.py line ~1335
ADMIN_HTML = r"""<!DOCTYPE html>
...
```

- [ ] **Step 2: 创建 static/index.html，粘贴 HTML 内容**

从 `ADMIN_HTML = r"""` 下一行开始，到 `"""` 结束的全部内容，复制到 `static/index.html`（去掉 `r` 前缀的原始字符串标记）。

- [ ] **Step 3: 修改 router.py 路由**

找到：
```python
@app.get("/", include_in_schema=False)
async def root():
    return HTMLResponse(content=ADMIN_HTML)
```

改为：
```python
from starlette.responses import FileResponse

@app.get("/", include_in_schema=False)
async def serve_index():
    return FileResponse("static/index.html")
```

- [ ] **Step 4: 删除 router.py 中的 ADMIN_HTML 变量**

删除 `ADMIN_HTML = r"""..."""` 整段（约 600 行）。

- [ ] **Step 5: 验证**

Run: 启动 cc2go，访问 http://127.0.0.1:4001/，确认页面正常显示

- [ ] **Step 6: 提交**

```bash
git add static/index.html src/router.py
git commit -m "feat: 分离 ADMIN_HTML 到 static/index.html"
```

---

## Task 2: 删除 save_stats() 冗余函数

**Files:**
- Modify: `src/router.py:314-325`（删除 save_stats 函数）
- Verify: `src/router_test.py`（确认无引用）

### 步骤

- [ ] **Step 1: 确认无外部引用**

`save_stats()` 仅被 `increment_*` 和自身递归调用，无外部模块引用。确认调用关系：
- `save_stats()` 内部调用 `save_stats_unlocked()`
- 无任何 `import router` 后调用 `router.save_stats()` 的路径

- [ ] **Step 2: 删除函数**

删除 `src/router.py` 第 314-325 行：
```python
def save_stats(force=False):
    global _stats_dirty
    ...
```

- [ ] **Step 3: 验证无影响**

Run: `python -m unittest discover -s src -p "*_test.py" -v`
Expected: 所有测试通过

- [ ] **Step 4: 提交**

```bash
git commit -m "refactor: 删除 save_stats() 冗余函数"
```

---

## Task 3: 流式路径补充 increment_requests()

**Files:**
- Modify: `src/router.py:891`（流式路径已有，但确认调用）

### 步骤

- [ ] **Step 1: 确认当前流式路径**

line 891 已调用 `increment_requests()`，无需修改。

- [ ] **Step 2: 更新文档**

更新 code-review-v6.md，将"流式统计未计入"标记为 N/A（已存在）。

---

## Task 4: convert_openai_stream_to_anthropic 添加异常处理

**Files:**
- Modify: `src/streaming.py:91`（aiter_lines 循环外包裹 try/except）

### 步骤

- [ ] **Step 1: 找到循环位置**

`src/streaming.py` line 91:
```python
async for line in response.aiter_lines():
    line = line.strip()
    if not line or not line.startswith("data: "):
        continue
```

- [ ] **Step 2: 添加上游断开异常处理**

在 `async for` 循环外包裹 `try/except Exception`，捕获连接断开等异常，记录日志后安全退出：

```python
try:
    async for line in response.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data: "):
            continue
        # ... 原有处理逻辑 ...
except Exception as e:
    logger.debug(f"[Stream] 上游连接断开: {e}")
```

- [ ] **Step 3: 提交**

```bash
git commit -m "fix: 流式转换添加上游断开异常处理"
```

---

## 执行方式

**推荐 Subagent-Driven：** 4 个任务互相独立，可派给 4 个 subagent 并行执行。

**1. Subagent-Driven（推荐）** - 派 subagent 并行执行 4 个任务
**2. Inline Execution** - 我来依次执行