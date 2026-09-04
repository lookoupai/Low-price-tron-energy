"""
付款到账率观测存储

一次扫描只能看到 6 个区块（约 18 秒）内的付款，单轮样本太小。
把每轮观测按区块窗口累加进数据库，展示时读 24 小时聚合，样本才有说服力。
主键取 (收款地址, 窗口起始块)，同一窗口被重复扫描时覆盖而不是重复累加。
"""
import logging
from typing import Dict, Iterable, List

from db import get_db_pool

logger = logging.getLogger(__name__)


class DeliveryStatsStore:
    """到账率观测存储"""

    # 聚合与清理窗口
    RETENTION_HOURS = 24

    async def init_database(self) -> None:
        """初始化数据库表"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_observations (
                    payment_address VARCHAR(50) NOT NULL,
                    window_start_block BIGINT NOT NULL,
                    total INTEGER NOT NULL,
                    delivered INTEGER NOT NULL,
                    observed_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (payment_address, window_start_block)
                );
                """
            )
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_delivery_obs_addr_time
                ON delivery_observations (payment_address, observed_at DESC);
                """
            )

    async def record(
        self,
        stats: Dict[str, Dict[str, int]],
        window_start_block: int,
    ) -> int:
        """写入本轮观测，返回写入的地址数"""
        rows = [
            (address, window_start_block, bucket["total"], bucket["delivered"])
            for address, bucket in stats.items()
            if bucket.get("total", 0) > 0
        ]
        if not rows:
            return 0

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO delivery_observations
                    (payment_address, window_start_block, total, delivered, observed_at)
                VALUES ($1, $2, $3, $4, NOW())
                ON CONFLICT (payment_address, window_start_block)
                DO UPDATE SET
                    total = EXCLUDED.total,
                    delivered = EXCLUDED.delivered,
                    observed_at = NOW()
                """,
                rows,
            )

        return len(rows)

    async def get_totals(self, payment_addresses: Iterable[str]) -> Dict[str, Dict[str, int]]:
        """读取指定地址近 RETENTION_HOURS 小时的累计到账率"""
        addresses: List[str] = [address for address in payment_addresses if address]
        if not addresses:
            return {}

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT payment_address,
                       SUM(total)::int AS total,
                       SUM(delivered)::int AS delivered
                FROM delivery_observations
                WHERE payment_address = ANY($1::varchar[])
                  AND observed_at >= NOW() - INTERVAL '{self.RETENTION_HOURS} hours'
                GROUP BY payment_address
                """,
                addresses,
            )

        return {
            row["payment_address"]: {"total": row["total"], "delivered": row["delivered"]}
            for row in rows
        }

    async def cleanup_expired(self) -> int:
        """清理过期观测，返回删除的记录数"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                f"""
                DELETE FROM delivery_observations
                WHERE observed_at < NOW() - INTERVAL '{self.RETENTION_HOURS} hours'
                """
            )

        return int(result.split()[-1]) if result else 0
