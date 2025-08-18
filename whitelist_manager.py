import asyncpg
import logging
import os
from typing import Dict, Optional
from cachetools import TTLCache
from dotenv import load_dotenv


logger = logging.getLogger(__name__)


class WhitelistManager:
    """白名单管理器

    管理收款地址、能量提供方以及两者组合的白名单记录。
    支持“临时”标记，用于1票即时生效的场景。
    """

    def __init__(self) -> None:
        load_dotenv()
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("请在.env文件中设置DATABASE_URL")
        self._connection_pool: Optional[asyncpg.pool.Pool] = None
        self._cache = TTLCache(maxsize=2000, ttl=300)

    async def init_database(self) -> None:
        self._connection_pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=10,
            command_timeout=30,
        )
        await self._create_tables()

    async def _create_tables(self) -> None:
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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

    async def get_stats(self) -> Dict:
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool:
            await self._connection_pool.close()
            logger.info("白名单连接池已关闭")


