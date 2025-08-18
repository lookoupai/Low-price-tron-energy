import asyncpg
import logging
import os
from typing import Optional
from dotenv import load_dotenv


logger = logging.getLogger(__name__)


class SettingsManager:
    """系统设置管理器

    当前用于管理以下配置：
    - blacklist_association_enabled: 是否启用黑名单关联（仅保留 提供方→收款地址 单向关联）
    """

    def __init__(self) -> None:
        load_dotenv()
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("请在.env文件中设置DATABASE_URL")
        self._connection_pool: Optional[asyncpg.pool.Pool] = None

    async def init_database(self) -> None:
        """初始化连接池和表结构"""
        self._connection_pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        await self._create_tables()

    async def _create_tables(self) -> None:
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT value FROM bot_settings WHERE key = $1", key
            )
            if row:
                return row["value"]
            return default

    async def set(self, key: str, value: str) -> None:
        if self._connection_pool is None:
            await self.init_database()
        assert self._connection_pool is not None
        async with self._connection_pool.acquire() as conn:
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
        if self._connection_pool:
            await self._connection_pool.close()
            logger.info("设置连接池已关闭")


