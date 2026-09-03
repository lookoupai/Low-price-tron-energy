"""统一数据库连接池管理器

所有需要数据库连接的模块通过此单例获取共享连接池，
避免每个 Manager 各自创建独立连接池导致连接数激增。
"""
import asyncpg
import logging
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class DatabasePool:
    """单例数据库连接池"""

    _instance: Optional['DatabasePool'] = None
    _pool: Optional[asyncpg.Pool] = None
    _initializing: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_pool(self) -> asyncpg.Pool:
        """获取连接池，首次调用时自动初始化"""
        if self._pool is not None:
            return self._pool

        if self._initializing:
            # 防止并发初始化
            import asyncio
            while self._initializing:
                await asyncio.sleep(0.1)
            if self._pool is not None:
                return self._pool

        self._initializing = True
        try:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("请在.env文件中设置DATABASE_URL")

            self._pool = await asyncpg.create_pool(
                database_url,
                min_size=2,
                max_size=15,
                command_timeout=30,
            )
            logger.info("统一数据库连接池初始化成功 (min=2, max=15)")
            return self._pool

        except Exception as e:
            logger.error(f"统一连接池初始化失败: {e}")
            raise
        finally:
            self._initializing = False

    async def close(self) -> None:
        """关闭连接池"""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("统一数据库连接池已关闭")


# 全局单例实例
_db_pool = DatabasePool()


async def get_db_pool() -> asyncpg.Pool:
    """获取全局共享连接池"""
    return await _db_pool.get_pool()


async def close_db_pool() -> None:
    """关闭全局连接池（通常在程序退出时调用）"""
    await _db_pool.close()
