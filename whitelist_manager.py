import asyncpg
import logging
from typing import Dict, Optional
from cachetools import TTLCache
from db import get_db_pool


logger = logging.getLogger(__name__)


class WhitelistManager:
    """白名单管理器

    管理收款地址、能量提供方以及两者组合的白名单记录。
    支持"临时"标记，用于1票即时生效的场景。
    """

    def __init__(self) -> None:
        self._cache = TTLCache(maxsize=2000, ttl=300)

    async def init_database(self) -> None:
        pool = await get_db_pool()
        await self._create_tables(pool)

    async def _create_tables(self, pool: asyncpg.Pool) -> None:
        async with pool.acquire() as conn:
            # 单地址白名单
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS whitelist (
                    id SERIAL PRIMARY KEY,
                    address VARCHAR(50) NOT NULL,
                    address_type VARCHAR(20) NOT NULL, -- 'payment' | 'provider'
                    reason TEXT,
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT NOW(),
                    is_active BOOLEAN DEFAULT true,
                    is_provisional BOOLEAN DEFAULT false,
                    success_count INTEGER DEFAULT 1,
                    UNIQUE(address, address_type)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_whitelist_address ON whitelist(address)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_whitelist_active ON whitelist(is_active)"
            )

            # 组合白名单
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS whitelist_pairs (
                    id SERIAL PRIMARY KEY,
                    payment_address VARCHAR(50) NOT NULL,
                    provider_address VARCHAR(50) NOT NULL,
                    success_count INTEGER DEFAULT 1,
                    last_success_time TIMESTAMP DEFAULT NOW(),
                    is_active BOOLEAN DEFAULT true,
                    is_provisional BOOLEAN DEFAULT false,
                    added_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    UNIQUE(payment_address, provider_address)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_whitelist_pairs_payment ON whitelist_pairs(payment_address)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_whitelist_pairs_provider ON whitelist_pairs(provider_address)"
            )

    async def add_address(self, address: str, address_type: str, reason: Optional[str], added_by: Optional[int], is_provisional: bool = True) -> bool:
        if not self._validate_tron_address(address):
            return False
        if address_type not in ("payment", "provider"):
            return False
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO whitelist (address, address_type, reason, added_by, is_provisional)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (address, address_type)
                DO UPDATE SET
                    reason = COALESCE(EXCLUDED.reason, whitelist.reason),
                    is_active = true,
                    is_provisional = EXCLUDED.is_provisional,
                    success_count = whitelist.success_count + 1,
                    added_at = NOW()
                """,
                address,
                address_type,
                reason,
                added_by,
                is_provisional,
            )
        # invalidate cache
        self._cache.pop((address, address_type), None)
        return True

    async def remove_address(self, address: str, address_type: str) -> bool:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE whitelist SET is_active = false WHERE address = $1 AND address_type = $2",
                address,
                address_type,
            )
        self._cache.pop((address, address_type), None)
        return True

    async def check_address(self, address: str, address_type: str) -> Optional[Dict]:
        cache_key = (address, address_type)
        if cache_key in self._cache:
            return self._cache[cache_key]
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT address, address_type, reason, added_by, added_at, is_active, is_provisional, success_count
                FROM whitelist
                WHERE address = $1 AND address_type = $2 AND is_active = true
                """,
                address,
                address_type,
            )
            if row:
                info = dict(row)
                self._cache[cache_key] = info
                return info
            self._cache[cache_key] = None
            return None

    async def add_pair(self, payment_address: str, provider_address: str, added_by: Optional[int], is_provisional: bool = True) -> bool:
        if not (self._validate_tron_address(payment_address) and self._validate_tron_address(provider_address)):
            return False
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO whitelist_pairs (payment_address, provider_address, is_provisional, added_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (payment_address, provider_address)
                DO UPDATE SET
                    is_active = true,
                    is_provisional = EXCLUDED.is_provisional,
                    success_count = whitelist_pairs.success_count + 1,
                    last_success_time = NOW()
                """,
                payment_address,
                provider_address,
                is_provisional,
                added_by,
            )
        # no cache for pair currently
        return True

    async def check_pair(self, payment_address: str, provider_address: str) -> Optional[Dict]:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT payment_address, provider_address, success_count, last_success_time, is_active, is_provisional, added_by, created_at
                FROM whitelist_pairs
                WHERE payment_address = $1 AND provider_address = $2 AND is_active = true
                """,
                payment_address,
                provider_address,
            )
            return dict(row) if row else None

    async def remove_pair(self, payment_address: str, provider_address: str) -> bool:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE whitelist_pairs SET is_active = false WHERE payment_address = $1 AND provider_address = $2",
                payment_address,
                provider_address,
            )
        return True

    async def set_provisional(self, address: str, address_type: str, is_provisional: bool) -> bool:
        """仅更新临时标记，不叠加 success_count"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE whitelist SET is_provisional = $3 WHERE address = $1 AND address_type = $2",
                address,
                address_type,
                is_provisional,
            )
        self._cache.pop((address, address_type), None)
        return True

    async def set_pair_provisional(self, payment_address: str, provider_address: str, is_provisional: bool) -> bool:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE whitelist_pairs SET is_provisional = $3 WHERE payment_address = $1 AND provider_address = $2",
                payment_address,
                provider_address,
                is_provisional,
            )
        return True

    async def get_stats(self) -> Dict:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row1 = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM whitelist WHERE is_active = true")
            row2 = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM whitelist_pairs WHERE is_active = true")
            return {
                "addresses": int(row1["cnt"]) if row1 else 0,
                "pairs": int(row2["cnt"]) if row2 else 0,
            }

    def _validate_tron_address(self, address: str) -> bool:
        if not address:
            return False
        if address.startswith('T') and len(address) == 34:
            import re
            pattern = r'^T[1-9A-HJ-NP-Za-km-z]{33}$'
            return bool(re.match(pattern, address))
        return False

    async def close(self) -> None:
        """关闭方法保留以兼容现有代码，实际连接池由 db.py 统一管理"""
        pass


