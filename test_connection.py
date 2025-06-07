#!/usr/bin/env python3
"""
测试数据库连接
"""

import asyncio
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

async def test_db_connection():
    """测试数据库连接"""
    try:
        import asyncpg
        
        # 获取数据库URL
        database_url = os.getenv('DATABASE_URL')
        print(f"🔍 数据库URL: {database_url[:50]}...")
        
        # 尝试连接
        print("🚀 尝试连接数据库...")
        connection = await asyncpg.connect(database_url)
        print("✅ 数据库连接成功!")
        
        # 测试查询
        result = await connection.fetch("SELECT 1 as test")
        print(f"📋 测试查询结果: {result}")
        
        # 检查表是否存在
        table_check = await connection.fetch("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'blacklist'
            )
        """)
        print(f"📊 黑名单表存在: {table_check[0]['exists']}")
        
        if table_check[0]['exists']:
            # 查询黑名单记录
            records = await connection.fetch("SELECT * FROM blacklist LIMIT 5")
            print(f"📝 黑名单记录数: {len(records)}")
            for record in records:
                print(f"   - {record['address']}: {record['reason']}")
        
        await connection.close()
        print("✅ 测试完成")
        
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_db_connection()) 