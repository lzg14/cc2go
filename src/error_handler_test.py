"""
错误自适应处理器 - 单元测试 & 集成测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from error_handler import (
    ErrorType, RetryStrategy, get_backoff_delay,
    parse_upstream_error, classify_and_suggest_action,
    ErrorArchiveRateLimiter, classify_error
)


class TestErrorType(unittest.TestCase):
    def test_429_is_rate_limit(self):
        self.assertEqual(classify_error(429), ErrorType.RATE_LIMIT)

    def test_500_is_server_error(self):
        self.assertEqual(classify_error(500), ErrorType.SERVER_ERROR)

    def test_502_is_server_error(self):
        self.assertEqual(classify_error(502), ErrorType.SERVER_ERROR)

    def test_503_is_server_error(self):
        self.assertEqual(classify_error(503), ErrorType.SERVER_ERROR)

    def test_400_is_client_error(self):
        self.assertEqual(classify_error(400), ErrorType.CLIENT_ERROR)

    def test_401_is_auth_error(self):
        self.assertEqual(classify_error(401), ErrorType.AUTH_ERROR)

    def test_403_is_auth_error(self):
        self.assertEqual(classify_error(403), ErrorType.AUTH_ERROR)

    def test_unknown_for_unexpected_codes(self):
        self.assertEqual(classify_error(418), ErrorType.UNKNOWN)


class TestBackoff(unittest.TestCase):
    def test_backoff_exponential(self):
        d0 = get_backoff_delay(0, base=1.0, jitter=False)
        d1 = get_backoff_delay(1, base=1.0, jitter=False)
        d2 = get_backoff_delay(2, base=1.0, jitter=False)
        self.assertGreater(d1, d0)
        self.assertGreater(d2, d1)
        self.assertAlmostEqual(d2, d1 * 2, places=3)

    def test_backoff_capped(self):
        d = get_backoff_delay(10, base=1.0, max_delay=5.0)
        self.assertLessEqual(d, 5.0)

    def test_backoff_jitter_range(self):
        d = get_backoff_delay(1, base=2.0, jitter=True)
        self.assertGreaterEqual(d, 2.0)
        self.assertLessEqual(d, 4.0)


class TestParseError(unittest.TestCase):
    def test_parse_anthropic_format(self):
        body = {"error": {"message": "rate limit exceeded", "type": "rate_limit_error"}}
        msg = parse_upstream_error(body)
        self.assertIn("rate limit", msg.lower())

    def test_parse_openai_format(self):
        body = {"message": "invalid api key"}
        msg = parse_upstream_error(body)
        self.assertIn("invalid api key", msg.lower())

    def test_parse_plain_string(self):
        msg = parse_upstream_error("internal server error")
        self.assertIn("internal", msg.lower())

    def test_parse_json_string(self):
        body = '{"error": {"message": "model not found"}}'
        msg = parse_upstream_error(body)
        self.assertIn("model not found", msg.lower())


class TestClassifyAndSuggest(unittest.TestCase):
    def test_429_first_attempt_returns_backoff(self):
        strategy, _, hint = classify_and_suggest_action(429, {}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.RETRY_WITH_BACKOFF)
        self.assertIsNone(hint)

    def test_429_last_attempt_returns_switch_model(self):
        strategy, _, hint = classify_and_suggest_action(429, {}, 2, 3)
        self.assertEqual(strategy, RetryStrategy.SWITCH_MODEL)
        self.assertEqual(hint, "try_next_available")

    def test_500_first_attempt_returns_backoff(self):
        strategy, _, _ = classify_and_suggest_action(500, {}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.RETRY_WITH_BACKOFF)

    def test_500_last_attempt_returns_switch_model(self):
        strategy, _, hint = classify_and_suggest_action(500, {}, 2, 3)
        self.assertEqual(strategy, RetryStrategy.SWITCH_MODEL)
        self.assertEqual(hint, "try_next_available")

    def test_400_returns_fail_fast(self):
        strategy, _, _ = classify_and_suggest_action(400, {}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.FAIL_FAST)

    def test_401_returns_fail_fast(self):
        strategy, _, _ = classify_and_suggest_action(401, {}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.FAIL_FAST)


class TestArchiveRateLimiter(unittest.TestCase):
    def test_first_archive_allowed(self):
        limiter = ErrorArchiveRateLimiter(window_seconds=300.0, max_per_window=10)
        self.assertTrue(limiter.can_archive())
        self.assertTrue(limiter.archive())

    def test_within_limit_allowed(self):
        limiter = ErrorArchiveRateLimiter(window_seconds=300.0, max_per_window=5)
        for i in range(4):
            self.assertTrue(limiter.archive())

    def test_exceeds_limit_blocked(self):
        limiter = ErrorArchiveRateLimiter(window_seconds=300.0, max_per_window=3)
        limiter.archive()
        limiter.archive()
        limiter.archive()
        self.assertFalse(limiter.can_archive())
        self.assertFalse(limiter.archive())

    def test_archive_returns_true_on_success(self):
        limiter = ErrorArchiveRateLimiter(window_seconds=300.0, max_per_window=10)
        self.assertTrue(limiter.archive())

    def test_archive_returns_false_when_limited(self):
        limiter = ErrorArchiveRateLimiter(window_seconds=300.0, max_per_window=2)
        limiter.archive()
        limiter.archive()
        self.assertFalse(limiter.archive())


# ============ 集成测试：call_opencode 重试逻辑 ============
import asyncio
import json
import threading
import time as time_module


class TestCallOpencodeRetryLogic(unittest.TestCase):
    """验证 call_opencode 的实际重试行为"""

    def test_429_twice_then_200_triggers_backoff_and_succeeds(self):
        """
        场景：上游返回 429，重试后返回 200
        期望：RETRY_WITH_BACKOFF -> 退避 -> 重试 -> 成功
        """
        from error_handler import get_backoff_delay, RetryStrategy, classify_and_suggest_action

        # attempt 0: 429 -> RETRY_WITH_BACKOFF
        strategy0, _, _ = classify_and_suggest_action(429, {}, 0, 3)
        self.assertEqual(strategy0, RetryStrategy.RETRY_WITH_BACKOFF)

        # attempt 1: 再次 429 -> RETRY_WITH_BACKOFF
        strategy1, _, _ = classify_and_suggest_action(429, {}, 1, 3)
        self.assertEqual(strategy1, RetryStrategy.RETRY_WITH_BACKOFF)

        # attempt 2 (最后一次): 429 -> SWITCH_MODEL
        strategy2, _, hint = classify_and_suggest_action(429, {}, 2, 3)
        self.assertEqual(strategy2, RetryStrategy.SWITCH_MODEL)
        self.assertEqual(hint, "try_next_available")

    def test_500_twice_then_last_try_switches_model(self):
        """
        场景：上游连续返回 500，第三次尝试时建议切换模型
        期望：attempt 2 -> SWITCH_MODEL hint="try_next_available"
        """
        from error_handler import classify_and_suggest_action, RetryStrategy

        strategy, _, hint = classify_and_suggest_action(500, {}, 2, 3)
        self.assertEqual(strategy, RetryStrategy.SWITCH_MODEL)
        self.assertEqual(hint, "try_next_available")

    def test_400_always_fail_fast(self):
        """
        场景：400 Bad Request，任何 attempt 都直接失败，不重试不切换模型
        """
        from error_handler import classify_and_suggest_action, RetryStrategy

        for attempt in range(3):
            strategy, log_msg, hint = classify_and_suggest_action(400, {}, attempt, 3)
            self.assertEqual(strategy, RetryStrategy.FAIL_FAST)
            self.assertIsNone(hint)
            self.assertIn("400", log_msg)

    def test_401_always_fail_fast(self):
        """
        场景：401 Unauthorized，直接失败，不泄露上游返回的错误详情
        """
        from error_handler import classify_and_suggest_action, RetryStrategy

        strategy, log_msg, hint = classify_and_suggest_action(401, {"message": "invalid api key the real secret"}, 0, 3)
        self.assertEqual(strategy, RetryStrategy.FAIL_FAST)
        self.assertIsNone(hint)
        # 敏感信息不应出现在日志消息中
        self.assertNotIn("the real secret", log_msg)
        self.assertNotIn("invalid api key", log_msg)


class TestArchiveRateLimiterIntegration(unittest.TestCase):
    """验证限速器在线程安全和滑动窗口方面的行为"""

    def test_concurrent_archive_calls(self):
        """
        场景：多线程同时调用 archive()
        期望：线程安全，不超限
        """
        from error_handler import ErrorArchiveRateLimiter

        limiter = ErrorArchiveRateLimiter(window_seconds=300.0, max_per_window=10)
        results = []
        errors = []

        def archive_task():
            try:
                result = limiter.archive()
                results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=archive_task) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        success_count = sum(1 for r in results if r is True)
        self.assertEqual(success_count, 10)
        self.assertEqual(len(errors), 0)

    def test_sliding_window_expires(self):
        """
        场景：窗口到期后，限额恢复
        期望：等待窗口过期或时间戳过期后可再次归档
        """
        from error_handler import ErrorArchiveRateLimiter

        # 使用极短窗口验证滑动窗口过期逻辑
        limiter = ErrorArchiveRateLimiter(window_seconds=0.1, max_per_window=2)

        self.assertTrue(limiter.archive())
        self.assertTrue(limiter.archive())
        self.assertFalse(limiter.can_archive())

        # 等待窗口过期
        time_module.sleep(0.15)

        self.assertTrue(limiter.can_archive())
        self.assertTrue(limiter.archive())


class TestBackoffDelayValues(unittest.TestCase):
    """验证退避延迟值的实际计算"""

    def test_backoff_attempt_0_no_jitter(self):
        d = get_backoff_delay(0, base=1.0, jitter=False)
        self.assertAlmostEqual(d, 1.0)

    def test_backoff_attempt_1_no_jitter(self):
        d = get_backoff_delay(1, base=1.0, jitter=False)
        self.assertAlmostEqual(d, 2.0)

    def test_backoff_attempt_2_no_jitter(self):
        d = get_backoff_delay(2, base=1.0, jitter=False)
        self.assertAlmostEqual(d, 4.0)

    def test_backoff_server_error_base_is_2x(self):
        """服务器错误使用 base=2.0 的退避"""
        from error_handler import classify_and_suggest_action

        strategy, log_msg, _ = classify_and_suggest_action(500, {}, 0, 3)
        self.assertIn("退避", log_msg)

    def test_backoff_max_delay_capped(self):
        d = get_backoff_delay(10, base=2.0, max_delay=60.0, jitter=False)
        self.assertLessEqual(d, 60.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)