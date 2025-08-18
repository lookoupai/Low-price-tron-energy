import asyncio
import asyncpg
import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from cachetools import TTLCache
import os
from dotenv import load_dotenv
from settings_manager import SettingsManager

# 加载环境变量
load_dotenv()

logger = logging.getLogger(__name__)

class BlacklistManager:
    def __init__(self):
        """初始化黑名单管理器"""
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("请在.env文件中设置DATABASE_URL")
        
        # 黑名单缓存，TTL为5分钟
        self._blacklist_cache = TTLCache(maxsize=1000, ttl=300)
        self._connection_pool = None
        self._settings_manager: Optional[SettingsManager] = None
        
    async def init_database(self):
        """初始化数据库连接池和表结构"""
        try:
            # 创建连接池
            self._connection_pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=10,
                command_timeout=30
            )
            
            # 创建表结构
            await self._create_tables()
            logger.info("数据库初始化成功")
            
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise
            
    async def _create_tables(self):
        """创建数据库表结构"""
        async with self._connection_pool.acquire() as connection:
            # 创建黑名单表
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS blacklist (
                    id SERIAL PRIMARY KEY,
                    address VARCHAR(50) UNIQUE NOT NULL,
                    reason TEXT,
                    type VARCHAR(20) DEFAULT 'manual',
                    added_by BIGINT,
                    added_at TIMESTAMP DEFAULT NOW(),
                    is_active BOOLEAN DEFAULT true,
                    is_provisional BOOLEAN DEFAULT false
                )
            ''')
            # 兼容已存在表，补充缺失列
            await connection.execute('''
                ALTER TABLE blacklist
                ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN DEFAULT false
            ''')
            
            # 创建关联记录表
            await connection.execute('''
                CREATE TABLE IF NOT EXISTS blacklist_associations (
                    id SERIAL PRIMARY KEY,
                    source_address VARCHAR(50) NOT NULL,
                    target_address VARCHAR(50) NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # 创建索引
            await connection.execute('''
                CREATE INDEX IF NOT EXISTS idx_blacklist_address ON blacklist(address);
            ''')
            await connection.execute('''
                CREATE INDEX IF NOT EXISTS idx_blacklist_active ON blacklist(is_active);
            ''')
            
    async def add_to_blacklist(self, address: str, reason: str = None, 
                             added_by: int = None, addr_type: str = 'manual',
                             is_provisional: bool = False) -> bool:
        """添加地址到黑名单"""
        try:
            # 验证地址格式
            if not self._validate_tron_address(address):
                return False
                
            # 确保数据库连接池已初始化
            if self._connection_pool is None:
                await self.init_database()
                
            async with self._connection_pool.acquire() as connection:
                await connection.execute('''
                    INSERT INTO blacklist (address, reason, type, added_by, is_provisional)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (address) 
                    DO UPDATE SET 
                        reason = COALESCE(EXCLUDED.reason, blacklist.reason),
                        is_active = true,
                        added_at = NOW(),
                        is_provisional = EXCLUDED.is_provisional
                ''', address, reason, addr_type, added_by, is_provisional)
                
            # 清除缓存
            self._blacklist_cache.pop(address, None)
            
            logger.info(f"成功添加地址到黑名单: {address}")
            return True
            
        except Exception as e:
            logger.error(f"添加黑名单失败: {e}")
            return False
            
    async def check_blacklist(self, address: str) -> Optional[Dict]:
        """检查地址是否在黑名单中"""
        try:
            # 先检查缓存
            if address in self._blacklist_cache:
                return self._blacklist_cache[address]
                
            # 验证地址格式
            if not self._validate_tron_address(address):
                return None
                
            # 确保数据库连接池已初始化
            if self._connection_pool is None:
                await self.init_database()
                
            async with self._connection_pool.acquire() as connection:
                result = await connection.fetchrow('''
                    SELECT address, reason, type, added_by, added_at, is_active, is_provisional
                    FROM blacklist 
                    WHERE address = $1 AND is_active = true
                ''', address)
                
                if result:
                    blacklist_info = {
                        'address': result['address'],
                        'reason': result['reason'],
                        'type': result['type'],
                        'added_by': result['added_by'],
                        'added_at': result['added_at'],
                        'is_active': result['is_active'],
                        'is_provisional': result['is_provisional']
                    }
                    # 缓存结果
                    self._blacklist_cache[address] = blacklist_info
                    return blacklist_info
                else:
                    # 缓存空结果
                    self._blacklist_cache[address] = None
                    return None
                    
        except Exception as e:
            logger.error(f"检查黑名单失败: {e}")
            return None
            
    async def remove_from_blacklist(self, address: str) -> bool:
        """从黑名单中移除地址"""
        try:
            # 确保数据库连接池已初始化
            if self._connection_pool is None:
                await self.init_database()
                
            async with self._connection_pool.acquire() as connection:
                result = await connection.execute('''
                    UPDATE blacklist 
                    SET is_active = false 
                    WHERE address = $1
                ''', address)
                
            # 清除缓存
            self._blacklist_cache.pop(address, None)
            
            logger.info(f"成功移除黑名单地址: {address}")
            return True
            
        except Exception as e:
            logger.error(f"移除黑名单失败: {e}")
            return False
            
    async def add_association(self, source_address: str, target_address: str) -> bool:
        """添加地址关联记录"""
        try:
            # 确保数据库连接池已初始化
            if self._connection_pool is None:
                await self.init_database()
                
            async with self._connection_pool.acquire() as connection:
                await connection.execute('''
                    INSERT INTO blacklist_associations (source_address, target_address)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                ''', source_address, target_address)
                
            logger.info(f"添加地址关联: {source_address} -> {target_address}")
            return True
            
        except Exception as e:
            logger.error(f"添加地址关联失败: {e}")
            return False
            
    async def auto_associate_addresses(self, address1: str, address2: str) -> bool:
        """自动关联：仅当 能量提供方(address2或address1) 在黑名单时，将收款地址关联入黑名单。

        规则：
        - 仅保留 提供方 -> 收款地址 单向关联
        - 受设置项 blacklist_association_enabled 控制
        - 若提供方为临时黑名单，则关联的收款地址也为临时黑名单
        """
        try:
            # 设置开关
            if self._settings_manager is None:
                self._settings_manager = SettingsManager()
                await self._settings_manager.init_database()

            if not await self._settings_manager.is_blacklist_association_enabled():
                return False

            # 检查两个地址的黑名单状态
            result1 = await self.check_blacklist(address1)  # 可能是收款或提供方
            result2 = await self.check_blacklist(address2)  # 可能是收款或提供方
            
            # 仅当 能量提供方 在黑名单时进行传播。这里我们无法仅凭入参判断角色，
            # 约定 address1 为收款地址，address2 为能量提供方（调用方需按此传参）。
            payment_address = address1
            provider_address = address2

            provider_black = await self.check_blacklist(provider_address)
            payment_black = await self.check_blacklist(payment_address)

            if provider_black and not payment_black:
                await self.add_to_blacklist(
                    payment_address,
                    f"关联黑名单能量提供方 {provider_address}",
                    addr_type='auto_associated',
                    is_provisional=bool(provider_black.get('is_provisional'))
                )
                await self.add_association(provider_address, payment_address)
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"自动关联地址失败: {e}")
            return False
            
    async def get_blacklist_stats(self) -> Dict:
        """获取黑名单统计信息"""
        try:
            # 确保数据库连接池已初始化
            if self._connection_pool is None:
                await self.init_database()
                
            async with self._connection_pool.acquire() as connection:
                result = await connection.fetchrow('''
                    SELECT 
                        COUNT(*) as total,
                        COUNT(CASE WHEN type = 'manual' THEN 1 END) as manual,
                        COUNT(CASE WHEN type = 'auto_associated' THEN 1 END) as auto_associated
                    FROM blacklist 
                    WHERE is_active = true
                ''')
                
                return {
                    'total': result['total'],
                    'manual': result['manual'],
                    'auto_associated': result['auto_associated']
                }
                
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}")
            return {}
            
    def _validate_tron_address(self, address: str) -> bool:
        """验证TRON地址格式"""
        if not address:
            return False
            
        # TRON主网地址格式验证（T开头，34位）
        if address.startswith('T') and len(address) == 34:
            # 简单的字符验证
            import re
            pattern = r'^T[1-9A-HJ-NP-Za-km-z]{33}$'
            return bool(re.match(pattern, address))
            
        return False
        
    async def close(self):
        """关闭数据库连接池"""
        if self._connection_pool:
            await self._connection_pool.close()
            logger.info("数据库连接池已关闭") 