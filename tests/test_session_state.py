from __future__ import annotations

from pathlib import Path
import sys
import unittest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from core.session_state import SessionState, TokenBucket


class TokenBucketTests(unittest.TestCase):
    def test_capacity_is_enforced_per_key(self) -> None:
        bucket = TokenBucket(capacity=2, refill_seconds=60.0)
        self.assertIsNone(bucket.check("chat-a"))
        self.assertIsNone(bucket.check("chat-a"))
        self.assertIsNotNone(bucket.check("chat-a"))
        # 其他会话不受影响。
        self.assertIsNone(bucket.check("chat-b"))

    def test_retry_after_grows_with_the_debt(self) -> None:
        bucket = TokenBucket(capacity=2, refill_seconds=60.0)
        bucket.check("chat-a")
        bucket.check("chat-a")
        first = bucket.check("chat-a")
        second = bucket.check("chat-a")
        assert first is not None and second is not None
        # 每欠一枚令牌需要等 30 秒；欠得越多等待越长。
        self.assertGreater(second, first)

    def test_tokens_refill_with_time(self) -> None:
        now = [100.0]

        def clock() -> float:
            return now[0]

        bucket = TokenBucket(capacity=2, refill_seconds=60.0, clock=clock)
        self.assertIsNone(bucket.check("chat-a"))
        self.assertIsNone(bucket.check("chat-a"))
        self.assertIsNotNone(bucket.check("chat-a"))  # 拒绝一次并累积债务
        now[0] += 30.0  # 只够还清债务；继续抢跑仍然被拒
        self.assertIsNotNone(bucket.check("chat-a"))
        now[0] += 60.0  # 停止尝试足够久后恢复一枚令牌
        self.assertIsNone(bucket.check("chat-a"))
        self.assertIsNotNone(bucket.check("chat-a"))

    def test_empty_key_is_never_limited(self) -> None:
        bucket = TokenBucket(capacity=1, refill_seconds=60.0)
        self.assertIsNone(bucket.check(""))


class SessionStateTests(unittest.TestCase):
    def test_search_and_delivery_buckets_are_independent(self) -> None:
        state = SessionState()
        for _ in range(6):
            self.assertIsNone(state.check_search("chat-a"))
        self.assertIsNotNone(state.check_search("chat-a"))
        # 搜索耗尽不影响交付预算。
        self.assertIsNone(state.check_delivery("chat-a"))
        self.assertIsNone(state.check_delivery("chat-a"))
        self.assertIsNone(state.check_delivery("chat-a"))
        self.assertIsNotNone(state.check_delivery("chat-a"))

    def test_last_search_memory_expires(self) -> None:
        now = [100.0]

        def clock() -> float:
            return now[0]

        state = SessionState(memory_ttl_seconds=300.0, clock=clock)
        state.remember_search(
            "chat-a", {"query": "晴天", "video_ref": None, "fuzzy": False, "page": 1}
        )
        self.assertEqual(state.last_search("chat-a")["query"], "晴天")
        now[0] += 301.0
        self.assertIsNone(state.last_search("chat-a"))

    def test_forget_search_removes_memory(self) -> None:
        state = SessionState()
        state.remember_search("chat-a", {"query": "晴天", "page": 1})
        state.forget_search("chat-a")
        self.assertIsNone(state.last_search("chat-a"))


if __name__ == "__main__":
    unittest.main()
