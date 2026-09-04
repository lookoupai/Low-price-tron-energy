"""
测试 APIKeyManager 的限流与并发行为

回归重点：限流分支曾在持锁状态下递归调用自身，导致永久死锁，
表现为定时推送和 /query 全部卡死（无日志、无异常）。
"""
import asyncio

import pytest

from tron_energy_finder import APIKeyManager, MAX_REQUESTS_PER_SECOND


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
