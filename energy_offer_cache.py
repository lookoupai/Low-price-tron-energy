"""
能量报价缓存管理器

提供基于数据库的能量报价缓存功能，加速 /query 命令响应速度
"""
import asyncio
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from db import get_db_pool


class EnergyOfferCache:
    """能量报价缓存管理器"""

    # 缓存有效期：发现后 2 小时内认为仍然有效
    CACHE_TTL_HOURS = 2

    def __init__(self):
        pass

    async def init_database(self) -> None:
        """初始化数据库表"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS energy_offers (
                    payment_address VARCHAR(50) NOT NULL,
                    provider_address VARCHAR(50) NOT NULL,
                    price_trx NUMERIC(12,4) NOT NULL,
                    energy BIGINT NOT NULL,
                    discovered_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    PRIMARY KEY (payment_address, provider_address)
                );
                """
            )
            # 索引：按价格和时间查询
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_energy_offers_price_time
                ON energy_offers (price_trx, discovered_at DESC);
                """
            )

    async def query_cached_offers(
        self,
        min_trx: float,
        max_trx: float,
        max_results: int = 3,
    ) -> List[Dict]:
        """
        从缓存查询符合价格区间的报价

        返回格式与 TronEnergyFinder.find_low_cost_energy_addresses 一致
        """
        cutoff_time = datetime.now() - timedelta(hours=self.CACHE_TTL_HOURS)

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    payment_address AS address,
                    provider_address,
                    price_trx,
                    energy
                FROM energy_offers
                WHERE price_trx >= $1
                  AND price_trx <= $2
                  AND discovered_at >= $3
                ORDER BY price_trx ASC, discovered_at DESC
                LIMIT $4
                """,
                min_trx,
                max_trx,
                cutoff_time,
                max_results,
            )

        results = []
        for row in rows:
            results.append(
                {
                    "address": row["address"],
                    "provider_address": row["provider_address"],
                    "staked_trx": float(row["price_trx"]),
                    "energy": row["energy"],
                    "max_count": 0,  # 缓存结果无交易统计
                    "source": "cache",
                }
            )

        return results

    async def save_offers(self, offers: List[Dict]) -> int:
        """
        保存新发现的报价到缓存

        参数 offers 格式与 TronEnergyFinder 返回结果一致
        返回实际插入的记录数
        """
        if not offers:
            return 0

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            inserted = 0
            for offer in offers:
                try:
                    await conn.execute(
                        """
                        INSERT INTO energy_offers
                            (payment_address, provider_address, price_trx, energy, discovered_at)
                        VALUES ($1, $2, $3, $4, NOW())
                        ON CONFLICT (payment_address, provider_address)
                        DO UPDATE SET
                            price_trx = EXCLUDED.price_trx,
                            energy = EXCLUDED.energy,
                            discovered_at = NOW()
                        """,
                        offer["address"],
                        offer.get("provider_address", ""),
                        offer["staked_trx"],
                        offer.get("energy", 0),
                    )
                    inserted += 1
                except Exception:
                    # 忽略单条插入失败，继续处理其他记录
                    continue

        return inserted

    async def cleanup_expired(self) -> int:
        """
        清理过期缓存记录

        返回删除的记录数
        """
        cutoff_time = datetime.now() - timedelta(hours=self.CACHE_TTL_HOURS)

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM energy_offers
                WHERE discovered_at < $1
                """,
                cutoff_time,
            )

        # result 格式为 "DELETE N"
        return int(result.split()[-1]) if result else 0
