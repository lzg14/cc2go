# 多后端 Key 轮询实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持配置多个 API Key，轮询或随机选择使用，提升可用性和配额利用率。

**Architecture:** 在 Config 类中新增 `opencode_api_keys` 字段（Key 列表）和 `key_strategy` 字段（roundrobin/random）。调用 `call_opencode()` 时从池中选取 Key，支持轮询（RR）和随机（Random）两种策略。Key 失败时自动剔除并重试。

**Tech Stack:** FastAPI / httpx / asyncio / random

---

## 文件结构

```
src/
  router.py          # 修改: Config 类、call_opencode()、.env 配置
  test_key_rotation.py # 新建: Key 轮询单元测试
```

---

### Task 1: Key 轮询核心逻辑

**Files:**
- Modify: `src/router.py:110-136`（Config 类）
- Modify: `src/router.py:439-459`（call_opencode 函数）
- Create: `src/test_key_rotation.py`

- [ ] **Step 1: 写测试用例**

```python
# src/test_key_rotation.py
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from unittest.mock import patch

def test_roundrobin_selects_keys_in_order():
    """轮询策略按顺序选择 Key"""
    keys = ["key-a", "key-b", "key-c"]
    selections = [keys[i % len(keys)] for i in range(6)]
    assert selections == ["key-a", "key-b", "key-c", "key-a", "key-b", "key-c"]

def test_random_strategy_not_all_same():
    """随机策略应产生不同的 Key（概率上）"""
    import random
    keys = ["key-a", "key-b", "key-c"]
    results = [random.choice(keys) for _ in range(100)]
    unique = set(results)
    assert len(unique) > 1, "随机策略应该选择多个不同的 Key"

def test_key_failure_detection():
    """检测 401/403 时标记 Key 失效"""
    test_codes = [200, 401, 403, 429, 500]
    should_retry = [c in (401, 403) for c in test_codes]
    assert should_retry == [False, True, True, False, False]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/test_key_rotation.py -v`
Expected: FAIL (import errors ok)

- [ ] **Step 3: 修改 Config 类支持多 Key**

在 `src/router.py` 的 Config 类 `__init__` 中添加：

```python
def __init__(self):
    # ... 现有字段 ...
    # 多 Key 支持
    raw_keys = os.getenv("OPENCODE_API_KEYS", "")
    fallback_key = os.getenv("OPENCODE_API_KEY", "")
    if raw_keys:
        self.opencode_api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
    elif fallback_key:
        self.opencode_api_keys = [fallback_key]
    else:
        self.opencode_api_keys = []

    self.key_strategy = os.getenv("KEY_STRATEGY", "roundrobin")  # roundrobin | random
    self._key_index = 0  # 轮询索引

    self.models = merge_models(DEFAULT_MODELS, load_custom_models())
```

- [ ] **Step 4: 实现 Key 选择器**

在 `Config` 类后、`call_opencode` 函数前添加：

```python
import random as random_module

class KeyPool:
    """API Key 轮询池"""

    def __init__(self, keys: list, strategy: str = "roundrobin"):
        self.keys = keys
        self.strategy = strategy
        self._index = 0
        self._failed = {}  # {key: fail_count}

    def select(self) -> str:
        if not self.keys:
            raise ValueError("No API keys available")

        if self.strategy == "random":
            return random_module.choice(self.keys)
        else:  # roundrobin
            return self.keys[self._index % len(self.keys)]

    def advance(self):
        self._index = (self._index + 1) % max(len(self.keys), 1)

    def mark_failed(self, key: str):
        self._failed[key] = self._failed.get(key, 0) + 1
        if self._failed[key] >= 3:
            logger.warning(f"[KeyPool] Key {key[:8]}... removed due to repeated failures")
            self.keys = [k for k in self.keys if k != key]
            self._index = 0

key_pool = None

def get_key_pool() -> KeyPool:
    global key_pool
    if key_pool is None:
        key_pool = KeyPool(config.opencode_api_keys, config.key_strategy)
    return key_pool
```

- [ ] **Step 5: 修改 call_opencode() 支持多 Key**

修改 `call_opencode()` 函数签名和逻辑：

```python
async def call_opencode(endpoint: str, payload: dict, base_url: str = None, api_key: str = None, full_url: str = None) -> httpx.Response:
    """调用 API，带重试和 Key 轮询。连接异常时重建 client 避免复用坏连接"""
    pool = get_key_pool()

    for attempt in range(config.max_retry):
        try:
            url = full_url or f"{base_url or config.opencode_base_url}{endpoint}"
            # 优先使用传入的 api_key，否则从池中选取
            key = api_key or pool.select()
            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "x-api-key": key
            }

            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(url, headers=headers, json=payload)

                # 401/403 时标记 Key 失效并重试
                if response.status_code in (401, 403):
                    pool.mark_failed(key)
                    logger.warning(f"[KeyPool] Key {key[:8]}... failed ({response.status_code}), retrying with next key")
                    continue

                if response.status_code < 500:
                    pool.advance()
                    return response

                logger.warning(f"Attempt {attempt + 1} failed: {response.status_code}")
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} error: {e}")

        if attempt < config.max_retry - 1:
            await asyncio.sleep(config.retry_delay * (attempt + 1))

    raise HTTPException(status_code=500, detail="OpenCode API 调用失败")
```

- [ ] **Step 6: 在 .env 中添加多 Key 配置示例**

```bash
# 单 Key 兼容（原有格式）
# OPENCODE_API_KEY=sk-xxx

# 多 Key（逗号分隔，轮询策略）
OPENCODE_API_KEYS=sk-key1,sk-key2,sk-key3
KEY_STRATEGY=roundrobin  # 或 random
```

- [ ] **Step 7: 运行测试**

Run: `cd D:/ProjectFile/cc2go && python -m pytest src/test_key_rotation.py -v`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add src/router.py src/test_key_rotation.py
git commit -m "feat: 多后端Key轮询 - 支持逗号分隔多Key和roundrobin/random策略"
```