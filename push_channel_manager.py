import logging
from datetime import timedelta
from typing import Dict, List, Optional

import asyncpg
from db import get_db_pool

logger = logging.getLogger(__name__)

PUSH_ADDRESS_COOLDOWN = timedelta(hours=6)


class PushChannelManager:
    """频道推送订阅管理器。

    持久化每个频道的价格筛选，以及 6 小时内已推送的收款地址。
    """

    def __init__(self) -> None:
        pass

    async def init_database(self) -> None:
        pool = await get_db_pool()
        await self._create_tables(pool)

    async def _create_tables(self, pool: asyncpg.Pool) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS push_channels (
                    chat_id BIGINT PRIMARY KEY,
                    min_trx NUMERIC(12,4) NOT NULL,
                    max_trx NUMERIC(12,4) NOT NULL,
                    enabled BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS push_history (
                    chat_id BIGINT NOT NULL,
                    payment_address VARCHAR(50) NOT NULL,
                    pushed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (chat_id, payment_address)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_push_channels_enabled ON push_channels(enabled)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_push_history_pushed_at ON push_history(pushed_at)"
            )

    async def upsert_channel(self, chat_id: int, min_trx: float, max_trx: float) -> None:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO push_channels (chat_id, min_trx, max_trx, enabled, updated_at)
                VALUES ($1, $2, $3, true, NOW())
                ON CONFLICT (chat_id)
                DO UPDATE SET
                    min_trx = EXCLUDED.min_trx,
                    max_trx = EXCLUDED.max_trx,
                    enabled = true,
                    updated_at = NOW()
                """,
                chat_id,
                round(min_trx, 4),
                round(max_trx, 4),
            )

    async def disable_channel(self, chat_id: int) -> bool:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE push_channels
                SET enabled = false, updated_at = NOW()
                WHERE chat_id = $1 AND enabled = true
                """,
                chat_id,
            )
        return result.endswith("1")

    async def get_channel(self, chat_id: int) -> Optional[Dict]:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT chat_id, min_trx, max_trx, enabled
                FROM push_channels
                WHERE chat_id = $1
                """,
                chat_id,
            )
        return self._row_to_channel(row) if row else None

    async def get_enabled_channels(self) -> List[Dict]:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT chat_id, min_trx, max_trx, enabled
                FROM push_channels
                WHERE enabled = true
                ORDER BY updated_at DESC
                """
            )
        return [self._row_to_channel(row) for row in rows]

    async def filter_fresh_addresses(self, chat_id: int, addresses: List[Dict]) -> List[Dict]:
        """去掉该频道 6 小时内已推送过的收款地址。"""
        if not addresses:
            return []
        payment_addresses = [addr.get("address") for addr in addresses if addr.get("address")]
        if not payment_addresses:
            return addresses

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT payment_address
                FROM push_history
                WHERE chat_id = $1
                  AND payment_address = ANY($2::varchar[])
                  AND pushed_at > NOW() - $3::interval
                """,
                chat_id,
                payment_addresses,
                PUSH_ADDRESS_COOLDOWN,
            )
        recently_pushed = {row["payment_address"] for row in rows}
        if not recently_pushed:
            return addresses
        return [addr for addr in addresses if addr.get("address") not in recently_pushed]

    async def mark_pushed(self, chat_id: int, payment_addresses: List[str]) -> None:
        unique_addresses = [addr for addr in dict.fromkeys(payment_addresses) if addr]
        if not unique_addresses:
            return
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO push_history (chat_id, payment_address, pushed_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (chat_id, payment_address)
                DO UPDATE SET pushed_at = NOW()
                """,
                [(chat_id, address) for address in unique_addresses],
            )

    @staticmethod
    def _row_to_channel(row) -> Dict:
        return {
            "chat_id": int(row["chat_id"]),
            "min_trx": float(row["min_trx"]),
            "max_trx": float(row["max_trx"]),
            "enabled": bool(row["enabled"]),
        }

    async def close(self) -> None:
        """关闭方法保留以兼容现有代码，实际连接池由 db.py 统一管理"""
        pass
