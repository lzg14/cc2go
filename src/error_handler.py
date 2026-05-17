"""
错误自适应处理器
提供错误分类、指数退避重试、模型切换 fallback
"""

import json
import logging
import random
import threading
import time
from enum import Enum
from typing import Tuple, Optional

logger = logging.getLogger("llm_router")


class ErrorType(Enum):
    RATE_LIMIT = "rate_limit"       # 429
    SERVER_ERROR = "server_error"  # 500/502/503
    AUTH_ERROR = "auth_error"      # 401/403
    CLIENT_ERROR = "client_error"  # 400
    UNKNOWN = "unknown"


class RetryStrategy(Enum):
    RETRY_WITH_BACKOFF = "retry_with_backoff"  # 指数退避重试
    SWITCH_MODEL = "switch_model"               # 切换模型
    FAIL_FAST = "fail_fast"                     # 直接失败


def classify_error(status_code: int) -> ErrorType:
    """根据状态码分类错误类型"""
    if status_code == 429:
        return ErrorType.RATE_LIMIT
    elif status_code in (500, 502, 503, 504):
        return ErrorType.SERVER_ERROR
    elif status_code in (401, 403):
        return ErrorType.AUTH_ERROR
    elif status_code == 400:
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
                "[RateLimit] 多次 429，建议切换模型",
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
            f"[AuthError] {status_code} — 认证失败，请检查 API Key 配置",
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
    """错误归档限速：window_seconds 内最多归档 1 次"""

    def __init__(self, window_seconds: float = 30.0):
        self.window = window_seconds
        self._last_archive: float = 0.0
        self._lock = threading.Lock()

    def update(self, window_seconds: float):
        with self._lock:
            self.window = window_seconds

    def archive(self) -> bool:
        now = time.time()
        with self._lock:
            if now - self._last_archive >= self.window:
                self._last_archive = now
                return True
            logger.debug("[ArchiveRateLimit] 限速跳过归档")
            return False


# 模块级限速器
_archive_limiter = ErrorArchiveRateLimiter()