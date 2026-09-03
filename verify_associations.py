#!/usr/bin/env python3
"""
验证地址关联数据状态脚本

此脚本用于检查和验证当前的地址关联数据状态，
包括黑白名单、关联记录、设置状态等。

使用方法：
    python verify_associations.py [选项]

选项：
    --detailed        显示详细信息
    --export         导出数据到CSV文件
"""

import asyncio
import argparse
import csv
import sys
from datetime import datetime
from typing import Dict, List
from db import get_db_pool, close_db_pool


class AssociationVerifier:
    """地址关联数据验证器"""

    def __init__(self):
        pass

    async def init_database(self):
        """初始化数据库连接"""
        try:
            await get_db_pool()
            print("✅ 数据库连接成功")
        except Exception as e:
            print(f"❌ 数据库连接失败: {e}")
            raise

    async def check_table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        pool = await get_db_pool()
        async with pool.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = $1
                )
                """,
                table_name
            )
            return result

    async def get_comprehensive_stats(self) -> Dict:
        """获取全面的数据统计"""
        stats = {}

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            # 检查表是否存在
            tables = ['blacklist', 'blacklist_associations', 'whitelist', 'whitelist_pairs', 'bot_settings']
            for table in tables:
                exists = await self.check_table_exists(table)
                stats[f'{table}_exists'] = exists

            # 如果表存在，获取统计信息
            if stats.get('blacklist_exists'):
                # 黑名单统计
                stats['blacklist_total'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM blacklist WHERE is_active = true"
                )
                stats['blacklist_manual'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM blacklist WHERE type = 'manual' AND is_active = true"
                )
                stats['blacklist_auto'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM blacklist WHERE type = 'auto_associated' AND is_active = true"
                )
                stats['blacklist_provisional'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM blacklist WHERE is_provisional = true AND is_active = true"
                )
                
            if stats.get('blacklist_associations_exists'):
                # 关联记录统计
                stats['associations_total'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM blacklist_associations"
                )
                
            if stats.get('whitelist_exists'):
                # 白名单统计
                stats['whitelist_addresses'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM whitelist WHERE is_active = true"
                )
                stats['whitelist_payment'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM whitelist WHERE address_type = 'payment' AND is_active = true"
                )
                stats['whitelist_provider'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM whitelist WHERE address_type = 'provider' AND is_active = true"
                )
                
            if stats.get('whitelist_pairs_exists'):
                # 白名单组合统计
                stats['whitelist_pairs'] = await conn.fetchval(
                    "SELECT COUNT(*) FROM whitelist_pairs WHERE is_active = true"
                )
                
            if stats.get('bot_settings_exists'):
                # 设置状态
                association_enabled = await conn.fetchval(
                    "SELECT value FROM bot_settings WHERE key = 'blacklist_association_enabled'"
                )
                stats['association_enabled'] = association_enabled
                
        return stats

    async def get_recent_associations(self, limit: int = 10) -> List[Dict]:
        """获取最近的关联记录"""
        if not await self.check_table_exists('blacklist_associations'):
            return []

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT source_address, target_address, created_at
                FROM blacklist_associations
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit
            )
            return [dict(row) for row in rows]

    async def get_recent_auto_blacklist(self, limit: int = 10) -> List[Dict]:
        """获取最近的自动黑名单记录"""
        if not await self.check_table_exists('blacklist'):
            return []

        pool = await get_db_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT address, reason, added_at, is_provisional
                FROM blacklist
                WHERE type = 'auto_associated' AND is_active = true
                ORDER BY added_at DESC
                LIMIT $1
                """,
                limit
            )
            return [dict(row) for row in rows]

    async def export_to_csv(self, output_dir: str = "exports") -> List[str]:
        """导出数据到CSV文件"""
        import os
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        exported_files = []

        pool = await get_db_pool()

        # 导出黑名单
        if await self.check_table_exists('blacklist'):
            filename = f"{output_dir}/blacklist_{timestamp}.csv"
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM blacklist WHERE is_active = true ORDER BY added_at DESC"
                )
                
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                if rows:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(dict(row))
            exported_files.append(filename)

        # 导出关联记录
        if await self.check_table_exists('blacklist_associations'):
            filename = f"{output_dir}/associations_{timestamp}.csv"
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM blacklist_associations ORDER BY created_at DESC"
                )

            with open(filename, 'w', newline='', encoding='utf-8') as f:
                if rows:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(dict(row))
            exported_files.append(filename)

        # 导出白名单
        if await self.check_table_exists('whitelist'):
            filename = f"{output_dir}/whitelist_{timestamp}.csv"
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM whitelist WHERE is_active = true ORDER BY added_at DESC"
                )

            with open(filename, 'w', newline='', encoding='utf-8') as f:
                if rows:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    for row in rows:
                        writer.writerow(dict(row))
            exported_files.append(filename)

        return exported_files

    async def close(self):
        """关闭方法保留以兼容现有代码，实际连接池由 db.py 统一管理"""
        pass


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="验证地址关联数据状态脚本")
    parser.add_argument("--detailed", action="store_true", help="显示详细信息")
    parser.add_argument("--export", action="store_true", help="导出数据到CSV文件")

    args = parser.parse_args()

    try:
        verifier = AssociationVerifier()
        await verifier.init_database()
        
        print("📊 地址关联数据状态报告")
        print("=" * 50)
        
        # 获取统计信息
        stats = await verifier.get_comprehensive_stats()
        
        # 显示表存在状态
        print("\n🗄️  数据表状态:")
        table_status = {
            'blacklist_exists': '黑名单表',
            'blacklist_associations_exists': '关联记录表',
            'whitelist_exists': '白名单表',
            'whitelist_pairs_exists': '白名单组合表',
            'bot_settings_exists': '设置表'
        }
        
        for key, name in table_status.items():
            status = "✅ 存在" if stats.get(key) else "❌ 不存在"
            print(f"   {name}: {status}")
            
        # 显示数据统计
        print("\n📈 数据统计:")
        
        # 黑名单
        if stats.get('blacklist_exists'):
            print(f"   黑名单总数: {stats.get('blacklist_total', 0)}")
            print(f"   - 手动添加: {stats.get('blacklist_manual', 0)}")
            print(f"   - 自动关联: {stats.get('blacklist_auto', 0)}")
            print(f"   - 临时标记: {stats.get('blacklist_provisional', 0)}")
        else:
            print("   黑名单: 表不存在")
            
        # 关联记录
        if stats.get('blacklist_associations_exists'):
            print(f"   关联记录: {stats.get('associations_total', 0)}")
        else:
            print("   关联记录: 表不存在")
            
        # 白名单
        if stats.get('whitelist_exists'):
            print(f"   白名单地址: {stats.get('whitelist_addresses', 0)}")
            print(f"   - 收款地址: {stats.get('whitelist_payment', 0)}")
            print(f"   - 提供方地址: {stats.get('whitelist_provider', 0)}")
        else:
            print("   白名单: 表不存在")
            
        if stats.get('whitelist_pairs_exists'):
            print(f"   白名单组合: {stats.get('whitelist_pairs', 0)}")
        else:
            print("   白名单组合: 表不存在")
            
        # 设置状态
        print("\n⚙️  功能设置:")
        if stats.get('bot_settings_exists'):
            enabled = stats.get('association_enabled', 'true')
            status = "✅ 启用" if enabled.lower() in ('true', '1', 'yes', 'on') else "❌ 禁用"
            print(f"   自动关联功能: {status}")
        else:
            print("   设置: 表不存在")
            
        # 详细信息
        if args.detailed:
            print("\n📋 详细信息:")
            
            # 最近的关联记录
            recent_associations = await verifier.get_recent_associations(5)
            if recent_associations:
                print("\n   最近的关联记录 (最新5条):")
                for assoc in recent_associations:
                    print(f"     {assoc['source_address']} -> {assoc['target_address']} ({assoc['created_at']})")
            else:
                print("\n   无关联记录")
                
            # 最近的自动黑名单
            recent_auto = await verifier.get_recent_auto_blacklist(5)
            if recent_auto:
                print("\n   最近的自动黑名单 (最新5条):")
                for bl in recent_auto:
                    status = "(临时)" if bl['is_provisional'] else ""
                    print(f"     {bl['address']} - {bl['reason']} {status} ({bl['added_at']})")
            else:
                print("\n   无自动黑名单记录")
                
        # 导出数据
        if args.export:
            print("\n📤 导出数据...")
            exported_files = await verifier.export_to_csv()
            if exported_files:
                print("   已导出文件:")
                for file in exported_files:
                    print(f"     - {file}")
            else:
                print("   无数据可导出")

        print("\n" + "=" * 50)
        print("✅ 验证完成")

        await close_db_pool()

    except Exception as e:
        print(f"❌ 验证失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
