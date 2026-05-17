"""
错误自适应处理器 - 单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import unittest
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


if __name__ == "__main__":
    unittest.main(verbosity=2)