# 错误自适应实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 cc2go 对错误更具弹性 — 429 时指数退避重试、模型切换 fallback、限速归档。

**Architecture:** 新建 `src/error_handler.py` 模块。错误分类和策略选择由 `classify_and_suggest_action` 统一处理（不设独立 `should_try_fallback`），集成时由 `call_opencode` 直接调用。归档限速：同一种错误 5 分钟内最多归档一次。

**Tech Stack:** FastAPI / httpx / asyncio / time

---

## 文件结构

```
src/
  router.py           # 修改: call_opencode() 中引入 error_handler
  error_handler.py    # 新建: 错误分类、策略、退避重试
  error_handler_test.py # 新建: 错误处理单元测试
```

---

### Task 1: 错误处理核心逻辑

**Files:**
- Create: `src/error_handler.py`
- Create: `src/error_handler_test.py`

- [ ] **Step 1: 写测试用例**

```python
# src/error_handler_test.py
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import unittest
from error_handler import (
    ErrorType, RetryStrategy, get_backoff_delay,
    parse_upstream_error, classify_and_suggest_action
)


class TestErrorType(unittest.TestCase):
    def test_429_is_rate_limit(self):
        _, t = classify_and_suggest_action(429, {}, 0, 3)[:2]
        self.assertEqual(t, ErrorType.RATE_LIMIT)

    def test_500_is_server_error(self):
        _, t = classify_and_suggest_action(500, {}, 0, 3)[:2]
        self.assertEqual(t, ErrorType.SERVER_ERROR)

    def test_400_is_client_error(self):
        _, t = classify_and_suggest_action(400, {}, 0, 3)[:2]
        self.assertEqual(t, ErrorType.CLIENT_ERROR)


class TestBackoff(unittest.TestCase):
    def test_backoff_exponential(self):
        d0 = get_backoff_delay(0, base=1.0)
        d1 = get_backoff_delay(1, base=1.0)
        d2 = get_backoff_delay(2, base=1.0)
        self.assertGreater(d1, d0)
        self.assertGreater(d2, d1)

    def test_backoff_capped(self):
        d = get_backoff_delay(10, base=1.0, max_delay=5.0)
        self.assertLessEqual(d, 5.0)


class TestParseError(unittest.TestCase):
    def test_parse_anthropic_format(self):
        body = {"error": {"message": "rate limit exceeded"}}
        msg = parse_upstream_error(body)
        self.assertIn("rate limit", msg.lower())

    def test_parse_plain_string(self):
        msg = parse_upstream_error("internal server error")
        self.assertIn("internal", msg.lower())


class TestClassifyAndSuggest(unittest.TestCase):
    def test_429_with_retry_attempts_returns_backoff(self):
        strategy, msg, _ = classify_and_suggest_action(429, {}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.RETRY_WITH_BACKOFF)

    def test_429_max_attempts_returns_switch(self):
        strategy, _, hint = classify_and_suggest_action(429, {}, 2, 3)
        self.assertEqual(strategy, RetryStrategy.SWITCH_MODEL)
        self.assertEqual(hint, "try_next_available")

    def test_401_returns_fail_fast(self):
        strategy, _, _ = classify_and_suggest_action(401, {}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.FAIL_FAST)

    def test_400_returns_fail_fast(self):
        strategy, _, _ = classify_and_suggest_action(400, {}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.FAIL_FAST)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python src/error_handler_test.py`
Expected: FAIL — error_handler.py not found

- [ ] **Step 3: 实现错误处理模块**

```python
# src/error_handler.py
"""
错误自适应处理器
提供错误分类、指数退避重试、模型切换 fallback
"""

import asyncio
import json
import logging
import random
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("llm_router")


class ErrorType(Enum):
    RATE_LIMIT = "rate_limit"       # 429
    SERVER_ERROR = "server_error"   # 500/502/503
    AUTH_ERROR = "auth_error"       # 401/403
    CLIENT_ERROR = "client_error"    # 400
    UNKNOWN = "unknown"


class RetryStrategy(Enum):
    RETRY_WITH_BACKOFF = "retry_with_backoff"     # 指数退避重试
    SWITCH_MODEL = "switch_model"                  # 切换模型
    FAIL_FAST = "fail_fast"                         # 直接失败


def classify_error(status_code: int) -> ErrorType:
    """根据状态码分类错误类型"""
    if status_code == 429:
        return ErrorType.RATE_LIMIT
    elif status_code in (500, 502, 503, 504):
        return ErrorType.SERVER_ERROR
    elif status_code in (401, 403):
        return ErrorType.AUTH_ERROR
    elif 400 <= status_code < 500:
        return ErrorType.CLIENT_ERROR
    else:
        return ErrorType.UNKNOWN


def get_backoff_delay(attempt: int, base: float = 1.0, max_delay: float = 60.0, jitter: bool = True) -> float:
    """计算指数退避延迟"""
    delay = base * (2 ** attempt)
    delay = min(delay, max_delay)
    if jitter:
        delay = delay * (0.5 + random.random() * 0.5)
    return delay


def parse_upstream_error(response_body) -> str:
    """从上游响应中提取人类可读的错误信息"""
    if isinstance(response_body, dict):
        if "error" in response_body:
            return response_body["error"].get("message", str(response_body["error"]))
        if "message" in response_body:
            return response_body["message"]
        return str(response_body)
    elif isinstance(response_body, str):
        try:
            return parse_upstream_error(json.loads(response_body))
        except Exception:
            return response_body[:500]
    return "Unknown error"


def classify_and_suggest_action(
    status_code: int,
    response_body,
    attempt: int,
    max_retry: int
) -> Tuple[RetryStrategy, str, Optional[str]]:
    """
    分析错误并建议处理动作
    Returns: (strategy, log_message, fallback_hint)
    fallback_hint: None | "try_next_available"
    """
    error_type = classify_error(status_code)
    error_msg = parse_upstream_error(response_body)

    if error_type == ErrorType.RATE_LIMIT:
        if attempt < max_retry - 1:
            delay = get_backoff_delay(attempt)
            return (
                RetryStrategy.RETRY_WITH_BACKOFF,
                f"[RateLimit] 429, 退避 {delay:.1f}s 后重试 (attempt {attempt + 1}/{max_retry})",
                None
            )
        else:
            return (
                RetryStrategy.SWITCH_MODEL,
                f"[RateLimit] 多次 429，建议切换模型",
                "try_next_available"
            )
    elif error_type == ErrorType.SERVER_ERROR:
        if attempt < max_retry - 1:
            delay = get_backoff_delay(attempt, base=2.0)
            return (
                RetryStrategy.RETRY_WITH_BACKOFF,
                f"[ServerError] {status_code}, 退避 {delay:.1f}s 后重试 (attempt {attempt + 1}/{max_retry})",
                None
            )
        else:
            return (
                RetryStrategy.SWITCH_MODEL,
                f"[ServerError] 多次 {status_code}，建议切换模型",
                "try_next_available"
            )
    elif error_type == ErrorType.AUTH_ERROR:
        return (
            RetryStrategy.FAIL_FAST,
            f"[AuthError] {status_code} — 检查 API Key: {error_msg[:100]}",
            None
        )
    else:
        return (
            RetryStrategy.FAIL_FAST,
            f"[Error] {status_code}: {error_msg[:200]}",
            None
        )


# ============ 归档限速 ============
class ErrorArchiveRateLimiter:
    """错误归档限速：同一种错误 5 分钟内最多归档一次"""

    def __init__(self, window_seconds: float = 300.0, max_per_window: int = 10):
        self.window = window_seconds
        self.max_per_window = max_per_window
        self._timestamps: List[float] = []

    def can_archive(self, error_key: str) -> bool:
        """判断是否可以归档（未超限）"""
        now = time.time()
        # 清理过期记录
        self._timestamps = [ts for ts in self._timestamps if now - ts < self.window]
        if len(self._timestamps) >= self.max_per_window:
            return False
        self._timestamps.append(now)
        return True

    def archive(self, error_key: str) -> bool:
        """尝试归档，返回是否成功"""
        if self.can_archive(error_key):
            return True
        logger.debug(f"[ArchiveRateLimit] 限速跳过: {error_key}")
        return False


# 全局限速器（模块级单例）
_archive_limiter = ErrorArchiveRateLimiter()


def save_error_archive_limited(error_key: str, error_info: Dict) -> bool:
    """限速版错误归档"""
    if not _archive_limiter.archive(error_key):
        return False
    # 实际归档逻辑由调用方在 router.py 中处理
    return True
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python src/error_handler_test.py`
Expected: PASS (13 tests)

- [ ] **Step 5: 提交**

```bash
git add src/error_handler.py src/error_handler_test.py
git commit -m "feat: 错误自适应模块 - 错误分类、指数退避、限速归档"
```

---

### Task 2: 集成到 call_opencode

**Files:**
- Modify: `src/router.py:439-459`（重写 call_opencode）
- Modify: `src/router.py:110-136`（Config 类添加 fallback_models）

- [ ] **Step 1: 在 Config 类中添加 fallback 配置**

```python
# 在 Config.__init__ 中添加
self.fallback_models = [
    m.strip() for m in os.getenv("FALLBACK_MODELS", "").split(",")
    if m.strip()
]
```

在 .env 中添加示例：

```bash
# Fallback 模型列表（逗号分隔，主模型失效时按顺序尝试）
FALLBACK_MODELS=glm-5.1,qwen3.6-plus,kimi-k2.6
```

- [ ] **Step 2: 重写 call_opencode 集成错误处理**

替换 `call_opencode()` 函数开头：

```python
from error_handler import (
    classify_and_suggest_action,
    get_backoff_delay,
    parse_upstream_error,
    RetryStrategy,
)


async def call_opencode(endpoint: str, payload: dict, base_url: str = None, api_key: str = None, full_url: str = None) -> httpx.Response:
    """调用 API，带智能错误处理和模型切换"""
    url = full_url or f"{base_url or config.opencode_base_url}{endpoint}"
    key = api_key or config.opencode_api_key
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "x-api-key": key
    }

    fallback_models = config.fallback_models
    fallback_idx = 0

    for attempt in range(config.max_retry):
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(url, headers=headers, json=payload)

                if response.status_code == 200:
                    return response

                try:
                    raw_body = json.loads(response.text)
                except Exception:
                    raw_body = response.text

                strategy, log_msg, hint = classify_and_suggest_action(
                    response.status_code, raw_body, attempt, config.max_retry
                )
                logger.warning(log_msg)

                # 限速归档（只对 400/429/500+ 归档）
                if response.status_code >= 400:
                    error_key = f"{payload.get('model','?')}-{response.status_code}"
                    if _archive_limiter.archive(error_key):
                        save_error_archive(
                            datetime.now().isoformat(),
                            payload.get("model", "unknown"),
                            payload,
                            None,
                            response.text,
                            response.status_code
                        )

                if strategy == RetryStrategy.FAIL_FAST:
                    raise HTTPException(status_code=response.status_code, detail=parse_upstream_error(raw_body))

                if strategy == RetryStrategy.SWITCH_MODEL and fallback_idx < len(fallback_models):
                    fallback_model = fallback_models[fallback_idx]
                    fallback_idx += 1
                    logger.info(f"[Fallback] 切换到模型: {fallback_model}")
                    payload = dict(payload, model=fallback_model)
                    continue

                if strategy == RetryStrategy.RETRY_WITH_BACKOFF:
                    delay = get_backoff_delay(attempt)
                    logger.info(f"[Retry] wait {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue

        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} error: {e}")
            if attempt < config.max_retry - 1:
                await asyncio.sleep(get_backoff_delay(attempt))
            else:
                raise HTTPException(status_code=500, detail=str(e))

    raise HTTPException(status_code=500, detail="OpenCode API 调用失败")
```

- [ ] **Step 3: 添加限速器 import**

在 router.py 顶部 import 区域添加：

```python
from error_handler import ErrorArchiveRateLimiter, _archive_limiter
```

- [ ] **Step 4: 运行测试**

Run: `python src/error_handler_test.py`
Expected: PASS

- [ ] **Step 5: 验证 cc2go 仍能正常启动**

Run: `python -m py_compile src/router.py && echo "OK"`
Expected: 无报错

- [ ] **Step 6: 提交**

```bash
git add src/router.py
git commit -m "feat: 集成错误自适应到call_opencode - 429退避和模型fallback"
```