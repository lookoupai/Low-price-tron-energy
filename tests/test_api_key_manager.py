"""
测试 APIKeyManager 的限流与并发行为

回归重点：
1. 限流分支曾在持锁状态下递归调用自身，导致永久死锁，
   表现为定时推送和 /query 全部卡死（无日志、无异常）。
2. 本地滑动窗口只保证"每秒不超过 5 次"，窗口边界上的突发（0.99 秒发 5 次、
   1.01 秒再发 5 次）服务端看到的是瞬时 10 次，会返回 429 并把 key 封禁十几秒。
   封禁期间必须跳过该 key，否则重试全部撞在同一个被封的 key 上。
"""
import asyncio
import time

import pytest

from tron_energy_finder import (
    APIKeyManager,
    DEFAULT_SUSPEND_SECONDS,
    MAX_REQUESTS_PER_SECOND,
    TronEnergyFinder,
)

parse_suspend = TronEnergyFinder._parse_suspend_seconds


class TestAPIKeyManager:
    """API Key 轮换与限流测试"""

    @pytest.mark.asyncio
    async def test_returns_key_within_quota(self):
        """配额内应直接返回 key"""
        manager = APIKeyManager(["key_a", "key_b"])
        key = await manager.get_next_key()
        assert key in ("key_a", "key_b")

    @pytest.mark.asyncio
    async def test_rotates_between_keys(self):
        """单 key 配额用满后应轮换到下一个 key"""
        manager = APIKeyManager(["key_a", "key_b"])
        keys = [await manager.get_next_key() for _ in range(MAX_REQUESTS_PER_SECOND * 2)]
        assert set(keys) == {"key_a", "key_b"}

    @pytest.mark.asyncio
    async def test_no_deadlock_when_all_keys_throttled(self):
        """所有 key 都触发限流时必须等待后继续，不能死锁"""
        manager = APIKeyManager(["key_a", "key_b"])
        total = MAX_REQUESTS_PER_SECOND * 2 * 2 + 4  # 必然超出单秒配额

        results = await asyncio.wait_for(
            asyncio.gather(*(manager.get_next_key() for _ in range(total))),
            timeout=10,
        )

        assert len(results) == total
        assert all(key in ("key_a", "key_b") for key in results)

    @pytest.mark.asyncio
    async def test_throttle_respects_per_second_quota(self):
        """限流后请求应被推迟，而不是超发"""
        manager = APIKeyManager(["key_a"])
        for _ in range(MAX_REQUESTS_PER_SECOND):
            await manager.get_next_key()

        start = asyncio.get_running_loop().time()
        await asyncio.wait_for(manager.get_next_key(), timeout=5)
        elapsed = asyncio.get_running_loop().time() - start

        assert elapsed > 0
        assert len(manager.request_times["key_a"]) <= MAX_REQUESTS_PER_SECOND + 1


class TestSuspension:
    """服务端 429 封禁处理测试"""

    @pytest.mark.asyncio
    async def test_suspended_key_is_skipped(self):
        """被封禁的 key 不应再被派发"""
        manager = APIKeyManager(["key_a", "key_b"])
        await manager.suspend("key_a", 30)

        keys = [await manager.get_next_key() for _ in range(MAX_REQUESTS_PER_SECOND)]
        assert set(keys) == {"key_b"}

    @pytest.mark.asyncio
    async def test_suspension_expires(self):
        """封禁到期后应重新可用"""
        manager = APIKeyManager(["key_a"])
        await manager.suspend("key_a", 0.05)

        start = asyncio.get_running_loop().time()
        key = await asyncio.wait_for(manager.get_next_key(), timeout=5)
        elapsed = asyncio.get_running_loop().time() - start

        assert key == "key_a"
        assert elapsed >= 0.04

    @pytest.mark.asyncio
    async def test_suspension_keeps_longest_deadline(self):
        """重复封禁时取更晚的解封时间，避免被短封禁覆盖"""
        manager = APIKeyManager(["key_a"])
        await manager.suspend("key_a", 30)
        long_deadline = manager.suspended_until["key_a"]

        await manager.suspend("key_a", 1)
        assert manager.suspended_until["key_a"] == long_deadline

    @pytest.mark.asyncio
    async def test_suspend_unknown_key_is_noop(self):
        """未知 key 不应写入封禁表"""
        manager = APIKeyManager(["key_a"])
        await manager.suspend("key_x", 30)

        assert "key_x" not in manager.suspended_until
        assert await manager.get_next_key() == "key_a"

    @pytest.mark.asyncio
    async def test_all_keys_suspended_waits_instead_of_failing(self):
        """所有 key 都被封禁时应等待解封，而不是抛错或死锁"""
        manager = APIKeyManager(["key_a", "key_b"])
        await manager.suspend("key_a", 0.05)
        await manager.suspend("key_b", 0.05)

        key = await asyncio.wait_for(manager.get_next_key(), timeout=5)
        assert key in ("key_a", "key_b")
        assert manager.suspended_until["key_a"] <= time.time()


class TestSuspendSecondsParsing:
    """封禁时长解析测试"""

    def test_parses_seconds_from_body(self):
        """按服务端响应体给出的秒数退避"""
        body = ('{"Error":"The key exceeds the frequency limit(5), '
                'and the query server is suspended for 16 s"}')
        assert parse_suspend(body) == 16.0

    def test_retry_after_header_wins(self):
        """Retry-After 头优先于响应体"""
        assert parse_suspend("suspended for 16 s", retry_after="5") == 5.0

    def test_invalid_retry_after_falls_back_to_body(self):
        """Retry-After 是日期等无法解析的格式时回退到响应体"""
        assert parse_suspend("suspended for 16 s",
                             retry_after="Wed, 21 Oct 2015 07:28:00 GMT") == 16.0

    def test_unparsable_body_uses_default(self):
        """解析不出时长时使用默认退避，不能返回 0 导致立刻重试"""
        assert parse_suspend("Too Many Requests") == float(DEFAULT_SUSPEND_SECONDS)
        assert parse_suspend("") == float(DEFAULT_SUSPEND_SECONDS)
        assert parse_suspend(None) == float(DEFAULT_SUSPEND_SECONDS)

    def test_never_returns_below_one_second(self):
        """至少退避 1 秒，避免解析到 0 后原地重试"""
        assert parse_suspend("suspended for 0 s") == 1.0
        assert parse_suspend("whatever", retry_after="0") == 1.0
