#!/usr/bin/env python3
"""
简单的数据库连接测试
"""
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

def test_connection():
    """测试数据库连接"""
    try:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            print("❌ 未找到DATABASE_URL环境变量")
            return False
            
        print(f"✅ 数据库连接字符串已配置")
        print(f"连接信息: {database_url[:50]}...")
        
        # 尝试导入asyncpg
        try:
            import asyncpg
            print("✅ asyncpg模块导入成功")
        except ImportError as e:
            print(f"❌ asyncpg模块导入失败: {e}")
            return False
            
        print("🔄 数据库连接测试将在机器人启动时进行")
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False

if __name__ == "__main__":
    test_connection() 