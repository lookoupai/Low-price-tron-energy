#!/usr/bin/env python3
"""
数据库初始化脚本
用于创建黑名单/白名单/设置/频道推送/反馈投票/能量缓存功能所需的数据库表结构
"""

import asyncio
import sys
from blacklist_manager import BlacklistManager
from whitelist_manager import WhitelistManager
from settings_manager import SettingsManager
from push_channel_manager import PushChannelManager
from feedback_manager import FeedbackManager
from energy_offer_cache import EnergyOfferCache
from db import close_db_pool

async def init_database():
    """初始化数据库"""
    try:
        print("正在初始化数据库...")

        blacklist_manager = BlacklistManager()
        whitelist_manager = WhitelistManager()
        settings_manager = SettingsManager()
        push_channel_manager = PushChannelManager()
        feedback_manager = FeedbackManager()
        offer_cache = EnergyOfferCache()

        await blacklist_manager.init_database()
        await whitelist_manager.init_database()
        await settings_manager.init_database()
        await push_channel_manager.init_database()
        await feedback_manager.init_database()
        await offer_cache.init_database()

        print("数据库初始化成功！")
        print("已创建/确认以下表:")
        print("- blacklist / blacklist_associations")
        print("- whitelist / whitelist_pairs")
        print("- bot_settings")
        print("- push_channels / push_history")
        print("- address_feedback")
        print("- energy_offers")

        await close_db_pool()

    except Exception as e:
        print(f"数据库初始化失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(init_database())
