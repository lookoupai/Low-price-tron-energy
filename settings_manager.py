import asyncpg
import logging
from typing import Optional
from db import get_db_pool


logger = logging.getLogger(__name__)


class SettingsManager:
    """系统设置管理器

    当前用于管理以下配置：
    - blacklist_association_enabled: 是否启用黑名单关联（仅保留 提供方→收款地址 单向关联）
    """

    def __init__(self) -> None:
        pass

    async def init_database(self) -> None:
        """初始化表结构"""
        pool = await get_db_pool()
        await self._create_tables(pool)

    async def _create_tables(self, pool: asyncpg.Pool) -> None:
        async with pool.acquire() as conn:
            # 设置表
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
                """
            )

            # 初始化默认值（如未设置）
            await conn.execute(
                """
                INSERT INTO bot_settings (key, value)
                VALUES ('blacklist_association_enabled', 'true')
                ON CONFLICT (key) DO NOTHING
                """
            )

    async def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM bot_settings WHERE key = $1", key
            )
            if row:
                return row["value"]
            return default

    async def set(self, key: str, value: str) -> None:
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                """,
                key,
                value,
            )

    async def is_blacklist_association_enabled(self) -> bool:
        value = await self.get("blacklist_association_enabled", "true")
        return str(value).lower() in ("1", "true", "yes", "on")

    async def set_blacklist_association_enabled(self, enabled: bool) -> None:
        await self.set("blacklist_association_enabled", "true" if enabled else "false")

    async def close(self) -> None:
        """关闭方法保留以兼容现有代码，实际连接池由 db.py 统一管理"""
        pass


