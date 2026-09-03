import asyncpg
import logging
import os
from datetime import timedelta
from typing import Dict, List, Optional
from dotenv import load_dotenv
from db import get_db_pool


logger = logging.getLogger(__name__)

# 撤回时间窗：投票后多久内允许用户自行撤回
REVOKE_WINDOW = timedelta(hours=24)

# 同向票数达到该值后，名单条目从“临时”转为正式
CONFIRM_THRESHOLD = 2

# 反馈范围：两者 / 仅收款地址 / 仅能量提供方
SCOPE_PAIR = 'pair'
SCOPE_PAYMENT = 'payment'
SCOPE_PROVIDER = 'provider'

VOTE_SUCCESS = 'success'
VOTE_FAIL = 'fail'


class FeedbackManager:
    """用户反馈投票管理器

    记录每个用户对“收款地址+能量提供方”的成功/失败投票，
    用于计算票数阈值、支持 24 小时内撤回。
    同一用户对同一组合同一范围只保留一条记录，重复点击只更新不叠加。
    """

    def __init__(self) -> None:
        load_dotenv()

    async def init_database(self) -> None:
        pool = await get_db_pool()
        await self._create_tables(pool)

    async def _create_tables(self, pool: asyncpg.Pool) -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS address_feedback (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    payment_address VARCHAR(50) NOT NULL,
                    provider_address VARCHAR(50) NOT NULL,
                    scope VARCHAR(20) NOT NULL, -- 'pair' | 'payment' | 'provider'
                    vote_type VARCHAR(20) NOT NULL, -- 'success' | 'fail'
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    revoked_at TIMESTAMP,
                    UNIQUE(user_id, payment_address, provider_address, scope)
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_payment ON address_feedback(payment_address)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_provider ON address_feedback(provider_address)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_feedback_user ON address_feedback(user_id)"
            )

    async def _ensure_pool(self) -> asyncpg.Pool:
        return await get_db_pool()

    async def record_vote(
        self,
        user_id: int,
        payment_address: str,
        provider_address: str,
        scope: str,
        vote_type: str,
    ) -> None:
        """记录一次投票，同一用户同范围重复投票只覆盖不叠加"""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO address_feedback
                    (user_id, payment_address, provider_address, scope, vote_type)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, payment_address, provider_address, scope)
                DO UPDATE SET
                    vote_type = EXCLUDED.vote_type,
                    created_at = NOW(),
                    revoked_at = NULL
                """,
                user_id,
                payment_address,
                provider_address,
                scope,
                vote_type,
            )

    async def count_votes(
        self,
        payment_address: Optional[str] = None,
        provider_address: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> Dict[str, int]:
        """统计有效票数，返回 {'success': n, 'fail': m}

        payment_address / provider_address 任一给出即按该地址统计，
        两者都给出时统计同时命中的记录。
        """
        pool = await self._ensure_pool()

        conditions = ["revoked_at IS NULL"]
        params: List[object] = []
        if payment_address:
            params.append(payment_address)
            conditions.append(f"payment_address = ${len(params)}")
        if provider_address:
            params.append(provider_address)
            conditions.append(f"provider_address = ${len(params)}")
        if scope:
            params.append(scope)
            conditions.append(f"scope = ${len(params)}")

        query = (
            "SELECT vote_type, COUNT(*) AS cnt FROM address_feedback "
            f"WHERE {' AND '.join(conditions)} GROUP BY vote_type"
        )
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        counts = {VOTE_SUCCESS: 0, VOTE_FAIL: 0}
        for row in rows:
            counts[row["vote_type"]] = int(row["cnt"])
        return counts

    async def get_user_votes(
        self,
        user_id: int,
        payment_address: str,
        provider_address: str,
    ) -> List[Dict]:
        """取用户对该组合的所有未撤回投票"""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT scope, vote_type, created_at
                FROM address_feedback
                WHERE user_id = $1
                  AND payment_address = $2
                  AND provider_address = $3
                  AND revoked_at IS NULL
                """,
                user_id,
                payment_address,
                provider_address,
            )
        return [dict(row) for row in rows]

    async def revoke_votes(
        self,
        user_id: int,
        payment_address: str,
        provider_address: str,
    ) -> List[Dict]:
        """撤回用户在时间窗内的投票，返回被撤回的记录"""
        pool = await self._ensure_pool()
        window_seconds = int(REVOKE_WINDOW.total_seconds())
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                UPDATE address_feedback
                SET revoked_at = NOW()
                WHERE user_id = $1
                  AND payment_address = $2
                  AND provider_address = $3
                  AND revoked_at IS NULL
                  AND created_at > NOW() - INTERVAL '{window_seconds} seconds'
                RETURNING scope, vote_type
                """,
                user_id,
                payment_address,
                provider_address,
            )
        return [dict(row) for row in rows]

    async def get_stats(self) -> Dict:
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE revoked_at IS NULL) AS active_votes,
                    COUNT(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked_votes,
                    COUNT(DISTINCT user_id) AS voters
                FROM address_feedback
                """
            )
        return {
            "active_votes": int(row["active_votes"]) if row else 0,
            "revoked_votes": int(row["revoked_votes"]) if row else 0,
            "voters": int(row["voters"]) if row else 0,
        }

    async def close(self) -> None:
        """关闭方法保留以兼容现有代码，实际连接池由 db.py 统一管理"""
        pass
