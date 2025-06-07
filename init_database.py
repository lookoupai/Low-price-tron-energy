#!/usr/bin/env python3
"""
数据库初始化脚本
用于创建黑名单功能所需的数据库表结构
"""

import asyncio
import sys
from blacklist_manager import BlacklistManager

async def init_database():
    """初始化数据库"""
    try:
        print("正在初始化数据库...")
        
        # 创建黑名单管理器
        blacklist_manager = BlacklistManager()
        
        # 初始化数据库
        await blacklist_manager.init_database()
        
        print("✅ 数据库初始化成功！")
        print("已创建以下表:")
        print("- blacklist: 黑名单表")
        print("- blacklist_associations: 地址关联表")
        
        # 关闭连接
        await blacklist_manager.close()
        
    except Exception as e:
        print(f"❌ 数据库初始化失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    # 运行初始化
    asyncio.run(init_database()) 