#!/usr/bin/env python3
"""
清理地址关联数据脚本

此脚本用于清空以前的地址关联数据，重新开始自动关联功能。
包含以下功能：
1. 清空黑名单关联记录表 (blacklist_associations)
2. 清空由自动关联产生的黑名单记录 (type='auto_associated')
3. 重置关联功能的设置状态
4. 提供数据备份和恢复选项

使用方法：
    python clear_associations.py [选项]

选项：
    --backup-only      只备份数据，不执行清理
    --clear-all       清理所有关联数据（包括关联表和自动添加的黑名单）
    --clear-associations-only  只清理关联表，保留自动添加的黑名单
    --restore         从备份恢复数据
    --dry-run         预览操作，不实际执行
    --force           强制执行，跳过确认提示
"""

import asyncio
import asyncpg
import argparse
import json
import os
import sys
from datetime import datetime
from typing import List, Dict, Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()


class AssociationCleaner:
    """地址关联数据清理器"""
    
    def __init__(self):
        self.database_url = os.getenv("DATABASE_URL")
        if not self.database_url:
            raise ValueError("请在.env文件中设置DATABASE_URL")
        self._connection_pool: Optional[asyncpg.pool.Pool] = None
        
    async def init_database(self):
        """初始化数据库连接"""
        try:
            self._connection_pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=5,
                command_timeout=60
            )
            print("✅ 数据库连接成功")
        except Exception as e:
            print(f"❌ 数据库连接失败: {e}")
            raise
            
    async def backup_data(self, backup_file: Optional[str] = None) -> str:
        """备份关联数据"""
        if backup_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"associations_backup_{timestamp}.json"
            
        print("🔄 正在备份关联数据...")
        
        async with self._connection_pool.acquire() as conn:
            # 备份关联表
            associations = await conn.fetch(
                "SELECT * FROM blacklist_associations ORDER BY created_at"
            )
            
            # 备份自动关联的黑名单
            auto_blacklist = await conn.fetch(
                "SELECT * FROM blacklist WHERE type = 'auto_associated' ORDER BY added_at"
            )
            
            # 备份设置
            settings = await conn.fetch(
                "SELECT * FROM bot_settings WHERE key LIKE '%association%'"
            )
            
        backup_data = {
            "timestamp": datetime.now().isoformat(),
            "associations": [dict(row) for row in associations],
            "auto_blacklist": [dict(row) for row in auto_blacklist],
            "settings": [dict(row) for row in settings]
        }
        
        # 序列化datetime对象
        def serialize_datetime(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
            
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2, default=serialize_datetime)
            
        print(f"✅ 数据已备份到: {backup_file}")
        print(f"   - 关联记录: {len(backup_data['associations'])} 条")
        print(f"   - 自动黑名单: {len(backup_data['auto_blacklist'])} 条")
        print(f"   - 设置项: {len(backup_data['settings'])} 条")
        
        return backup_file
        
    async def get_stats(self) -> Dict:
        """获取当前数据统计"""
        async with self._connection_pool.acquire() as conn:
            # 关联记录统计
            associations_count = await conn.fetchval(
                "SELECT COUNT(*) FROM blacklist_associations"
            )
            
            # 自动关联黑名单统计
            auto_blacklist_count = await conn.fetchval(
                "SELECT COUNT(*) FROM blacklist WHERE type = 'auto_associated' AND is_active = true"
            )
            
            # 手动黑名单统计
            manual_blacklist_count = await conn.fetchval(
                "SELECT COUNT(*) FROM blacklist WHERE type = 'manual' AND is_active = true"
            )
            
            # 临时黑名单统计
            provisional_count = await conn.fetchval(
                "SELECT COUNT(*) FROM blacklist WHERE is_provisional = true AND is_active = true"
            )
            
        return {
            "associations": associations_count,
            "auto_blacklist": auto_blacklist_count,
            "manual_blacklist": manual_blacklist_count,
            "provisional_blacklist": provisional_count
        }
        
    async def clear_associations_only(self, dry_run: bool = False) -> Dict:
        """只清理关联表，保留自动添加的黑名单"""
        print("🔄 开始清理关联记录表...")
        
        if dry_run:
            print("⚠️  [预览模式] 以下操作将被执行:")
            
        async with self._connection_pool.acquire() as conn:
            # 获取要删除的记录数
            count = await conn.fetchval("SELECT COUNT(*) FROM blacklist_associations")
            
            if dry_run:
                print(f"   - 将删除 {count} 条关联记录")
                return {"associations_cleared": count}
                
            # 执行清理
            await conn.execute("DELETE FROM blacklist_associations")
            
        print(f"✅ 已清理关联记录: {count} 条")
        return {"associations_cleared": count}
        
    async def clear_all_associations(self, dry_run: bool = False) -> Dict:
        """清理所有关联数据（关联表 + 自动添加的黑名单）"""
        print("🔄 开始清理所有关联数据...")
        
        if dry_run:
            print("⚠️  [预览模式] 以下操作将被执行:")
            
        async with self._connection_pool.acquire() as conn:
            # 获取要删除的记录数
            associations_count = await conn.fetchval("SELECT COUNT(*) FROM blacklist_associations")
            auto_blacklist_count = await conn.fetchval(
                "SELECT COUNT(*) FROM blacklist WHERE type = 'auto_associated' AND is_active = true"
            )
            
            if dry_run:
                print(f"   - 将删除 {associations_count} 条关联记录")
                print(f"   - 将移除 {auto_blacklist_count} 条自动关联的黑名单")
                return {
                    "associations_cleared": associations_count,
                    "auto_blacklist_cleared": auto_blacklist_count
                }
                
            # 执行清理
            await conn.execute("DELETE FROM blacklist_associations")
            await conn.execute(
                "UPDATE blacklist SET is_active = false WHERE type = 'auto_associated'"
            )
            
        print(f"✅ 已清理关联记录: {associations_count} 条")
        print(f"✅ 已移除自动关联黑名单: {auto_blacklist_count} 条")
        
        return {
            "associations_cleared": associations_count,
            "auto_blacklist_cleared": auto_blacklist_count
        }
        
    async def reset_association_settings(self, dry_run: bool = False) -> None:
        """重置关联功能设置"""
        print("🔄 重置关联功能设置...")
        
        if dry_run:
            print("⚠️  [预览模式] 将重置关联功能设置为启用状态")
            return
            
        async with self._connection_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES ('blacklist_association_enabled', 'true', NOW())
                ON CONFLICT (key)
                DO UPDATE SET value = 'true', updated_at = NOW()
                """
            )
            
        print("✅ 关联功能设置已重置为启用状态")
        
    async def restore_from_backup(self, backup_file: str) -> None:
        """从备份文件恢复数据"""
        print(f"🔄 从备份文件恢复数据: {backup_file}")
        
        if not os.path.exists(backup_file):
            raise FileNotFoundError(f"备份文件不存在: {backup_file}")
            
        with open(backup_file, 'r', encoding='utf-8') as f:
            backup_data = json.load(f)
            
        async with self._connection_pool.acquire() as conn:
            async with conn.transaction():
                # 恢复关联记录
                for assoc in backup_data['associations']:
                    await conn.execute(
                        """
                        INSERT INTO blacklist_associations (source_address, target_address, created_at)
                        VALUES ($1, $2, $3)
                        ON CONFLICT DO NOTHING
                        """,
                        assoc['source_address'],
                        assoc['target_address'],
                        datetime.fromisoformat(assoc['created_at'])
                    )
                    
                # 恢复自动黑名单
                for bl in backup_data['auto_blacklist']:
                    await conn.execute(
                        """
                        INSERT INTO blacklist (address, reason, type, added_by, added_at, is_active, is_provisional)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (address) 
                        DO UPDATE SET 
                            reason = EXCLUDED.reason,
                            type = EXCLUDED.type,
                            is_active = EXCLUDED.is_active,
                            is_provisional = EXCLUDED.is_provisional
                        """,
                        bl['address'], bl['reason'], bl['type'], bl['added_by'],
                        datetime.fromisoformat(bl['added_at']),
                        bl['is_active'], bl['is_provisional']
                    )
                    
                # 恢复设置
                for setting in backup_data['settings']:
                    await conn.execute(
                        """
                        INSERT INTO bot_settings (key, value, updated_at)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (key)
                        DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
                        """,
                        setting['key'], setting['value'],
                        datetime.fromisoformat(setting['updated_at'])
                    )
                    
        print(f"✅ 数据恢复完成")
        print(f"   - 关联记录: {len(backup_data['associations'])} 条")
        print(f"   - 自动黑名单: {len(backup_data['auto_blacklist'])} 条")
        print(f"   - 设置项: {len(backup_data['settings'])} 条")
        
    async def close(self):
        """关闭数据库连接"""
        if self._connection_pool:
            await self._connection_pool.close()


async def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="清理地址关联数据脚本")
    parser.add_argument("--backup-only", action="store_true", help="只备份数据，不执行清理")
    parser.add_argument("--clear-all", action="store_true", help="清理所有关联数据")
    parser.add_argument("--clear-associations-only", action="store_true", help="只清理关联表")
    parser.add_argument("--restore", type=str, help="从备份文件恢复数据")
    parser.add_argument("--dry-run", action="store_true", help="预览操作，不实际执行")
    parser.add_argument("--force", action="store_true", help="强制执行，跳过确认提示")
    
    args = parser.parse_args()
    
    # 检查参数
    action_count = sum([
        args.backup_only,
        args.clear_all, 
        args.clear_associations_only,
        bool(args.restore)
    ])
    
    if action_count == 0:
        print("❌ 请指定要执行的操作:")
        print("   --backup-only              只备份数据")
        print("   --clear-all               清理所有关联数据")
        print("   --clear-associations-only  只清理关联表")
        print("   --restore <备份文件>        从备份恢复数据")
        print("\n添加 --dry-run 可以预览操作")
        return
        
    if action_count > 1:
        print("❌ 只能指定一个操作选项")
        return
        
    try:
        cleaner = AssociationCleaner()
        await cleaner.init_database()
        
        # 显示当前状态
        print("\n📊 当前数据统计:")
        stats = await cleaner.get_stats()
        for key, value in stats.items():
            print(f"   - {key}: {value}")
        print()
        
        if args.backup_only:
            # 只备份
            backup_file = await cleaner.backup_data()
            print(f"\n✅ 备份完成: {backup_file}")
            
        elif args.restore:
            # 恢复数据
            if not args.force:
                confirm = input(f"确认要从 {args.restore} 恢复数据吗？这将覆盖现有数据 (y/N): ")
                if confirm.lower() != 'y':
                    print("操作已取消")
                    return
                    
            await cleaner.restore_from_backup(args.restore)
            
        else:
            # 清理操作
            if not args.dry_run and not args.force:
                print("⚠️  警告: 此操作将删除关联数据")
                
                if args.clear_all:
                    print("   - 删除所有关联记录")
                    print("   - 移除所有自动关联的黑名单")
                elif args.clear_associations_only:
                    print("   - 删除所有关联记录")
                    print("   - 保留自动关联的黑名单")
                    
                print("\n建议先使用 --backup-only 备份数据")
                confirm = input("\n确认继续吗？(y/N): ")
                if confirm.lower() != 'y':
                    print("操作已取消")
                    return
                    
            # 备份（除非是dry-run）
            if not args.dry_run:
                backup_file = await cleaner.backup_data()
                print()
                
            # 执行清理
            if args.clear_all:
                result = await cleaner.clear_all_associations(args.dry_run)
            elif args.clear_associations_only:
                result = await cleaner.clear_associations_only(args.dry_run)
                
            # 重置设置
            await cleaner.reset_association_settings(args.dry_run)
            
            if not args.dry_run:
                print(f"\n✅ 清理完成! 备份文件: {backup_file}")
                print("\n📋 下一步:")
                print("   1. 重启机器人以应用设置更改")
                print("   2. 新的关联数据将从现在开始重新积累")
                print("   3. 如需恢复，可使用: python clear_associations.py --restore <备份文件>")
            else:
                print("\n⚠️  这是预览模式，实际操作请移除 --dry-run 参数")
                
        await cleaner.close()
        
    except Exception as e:
        print(f"❌ 操作失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
