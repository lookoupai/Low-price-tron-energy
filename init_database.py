#!/usr/bin/env python3
"""
数据库初始化脚本
用于创建黑名单/白名单/设置功能所需的数据库表结构
"""

import asyncio
import sys
from blacklist_manager import BlacklistManager
from whitelist_manager import WhitelistManager
from settings_manager import SettingsManager

async def init_database():
    """初始化数据库"""
    try:
        print("正在初始化数据库...")

        blacklist_manager = BlacklistManager()
        whitelist_manager = WhitelistManager()
        settings_manager = SettingsManager()

        await blacklist_manager.init_database()
        await whitelist_manager.init_database()
        await settings_manager.init_database()

        print("数据库初始化成功！")
        print("已创建/确认以下表:")
        print("- blacklist / blacklist_associations")
        print("- whitelist / whitelist_pairs")
        print("- bot_settings")

        await blacklist_manager.close()
        await whitelist_manager.close()
        await settings_manager.close()

    except Exception as e:
        print(f"数据库初始化失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(init_database())
