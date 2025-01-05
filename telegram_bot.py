import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Set

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from tron_energy_finder import TronEnergyFinder

# 配置日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TronEnergyBot:
    def __init__(self):
        # 加载环境变量
        load_dotenv()
        
        # 获取Telegram Bot Token
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("请在.env文件中设置TELEGRAM_BOT_TOKEN")
            
        # 初始化TronEnergyFinder
        self.finder = TronEnergyFinder()
        
        # 初始化调度器
        self.scheduler = AsyncIOScheduler()
        
        # 存储活跃的频道（启用了推送的频道）
        self.active_channels: Set[int] = set()
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理/start命令"""
        welcome_message = (
            "👋 欢迎使用Tron能量查找机器人！\n\n"
            "🔍 使用 /query 命令立即查找低成本能量代理地址\n"
            "ℹ️ 使用 /help 命令查看更多帮助信息"
        )
        await update.message.reply_text(welcome_message)
        
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理/help命令"""
        help_message = (
            "📖 机器人使用帮助：\n\n"
            "1️⃣ 私聊命令：\n"
            "   /query - 立即查找低成本能量代理地址\n"
            "   /help - 显示此帮助信息\n\n"
            "2️⃣ 频道/群组命令：\n"
            "   /start_push - 开启定时推送（仅管理员）\n"
            "   /stop_push - 关闭定时推送（仅管理员）\n"
            "   /query - 立即查询一次\n\n"
            "注意：在频道/群组中使用命令需要授予机器人管理员权限"
        )
        await update.message.reply_text(help_message)
        
    async def check_admin_rights(self, update: Update) -> bool:
        """检查命令发送者是否为管理员"""
        try:
            chat = update.effective_chat
            if not chat:
                return False
                
            # 私聊情况下不需要检查权限
            if chat.type == "private":
                return True
                
            # 获取用户在群组/频道中的权限
            user = update.effective_user
            if not user:
                return False
                
            member = await chat.get_member(user.id)
            return member.status in ['creator', 'administrator']
            
        except TelegramError as e:
            logger.error(f"检查管理员权限时出错: {e}")
            return False
            
    async def start_push_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理/start_push命令"""
        try:
            # 检查权限
            if not await self.check_admin_rights(update):
                await update.message.reply_text("❌ 只有管理员才能使用此命令")
                return
                
            chat = update.effective_chat
            if not chat:
                return
                
            # 检查是否是群组或频道
            if chat.type not in ['group', 'supergroup', 'channel']:
                await update.message.reply_text("❌ 此命令只能在群组或频道中使用")
                return
                
            # 添加到活跃频道列表
            if chat.id not in self.active_channels:
                self.active_channels.add(chat.id)
                await self.send_message_to_chat(
                    chat.id,
                    "✅ 已开启定时推送功能\n⏰ 每小时将自动推送最新的能量地址"
                )
                
                # 立即执行一次推送
                await self.broadcast_addresses(specific_chat_id=chat.id)
            else:
                await self.send_message_to_chat(
                    chat.id,
                    "ℹ️ 定时推送功能已经处于开启状态"
                )
                
        except Exception as e:
            logger.error(f"开启推送时出错: {e}")
            await self.send_error_message(update)
            
    async def stop_push_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理/stop_push命令"""
        try:
            # 检查权限
            if not await self.check_admin_rights(update):
                await update.message.reply_text("❌ 只有管理员才能使用此命令")
                return
                
            chat = update.effective_chat
            if not chat:
                return
                
            # 检查是否是群组或频道
            if chat.type not in ['group', 'supergroup', 'channel']:
                await update.message.reply_text("❌ 此命令只能在群组或频道中使用")
                return
                
            # 从活跃频道列表中移除
            if chat.id in self.active_channels:
                self.active_channels.remove(chat.id)
                await self.send_message_to_chat(
                    chat.id,
                    "✅ 已关闭定时推送功能"
                )
            else:
                await self.send_message_to_chat(
                    chat.id,
                    "ℹ️ 定时推送功能已经处于关闭状态"
                )
                
        except Exception as e:
            logger.error(f"关闭推送时出错: {e}")
            await self.send_error_message(update)
            
    async def send_message_to_chat(self, chat_id: int, text: str, **kwargs) -> None:
        """发送消息到指定聊天"""
        try:
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown',
                disable_web_page_preview=True,
                **kwargs
            )
        except Exception as e:
            logger.error(f"发送消息到 {chat_id} 失败: {e}")
            
    async def send_error_message(self, update: Update) -> None:
        """发送错误消息"""
        try:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "❌ 操作过程中出现错误，请稍后重试"
                )
        except Exception as e:
            logger.error(f"发送错误消息失败: {e}")

    def format_address_info(self, addr: Dict) -> str:
        """格式化地址信息为消息文本"""
        energy_display = addr['energy_quantity']
        if addr['energy_source'] == "计算值":
            energy_display = f"{energy_display} (计算值，仅供参考)"
            
        return (
            f"🔹 【收款地址】: `{addr['address']}`\n"
            f"🔹 【能量提供方】: `{addr['energy_provider']}`\n"
            f"🔹 【购买记录】: [查看](https://tronscan.org/#/address/{addr['address']})\n"
            f"🔹 【收款金额】: {addr['purchase_amount']} TRX\n"
            f"🔹 【能量数量】: {energy_display}\n"
            f"🔹 【24h交易数】: {addr['recent_tx_count']} 笔\n"
            f"🔹 【转账哈希】: `{addr['tx_hash']}`\n"
            f"🔹 【代理哈希】: `{addr['proxy_tx_hash']}`\n\n"
            f"【地址状态】{addr['status']}"
        )
        
    async def query_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理/query命令"""
        try:
            # 发送等待消息
            wait_message = await update.message.reply_text(
                "🔍 正在查找低成本能量代理地址，请稍候..."
            )
            
            # 执行查找
            addresses = self.finder.find_low_cost_energy_addresses()
            
            if not addresses:
                await wait_message.edit_text("❌ 未找到符合条件的低价能量地址，请稍后再试")
                return
                
            # 更新等待消息为结果
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            result_message = f"🎯 查询时间：{current_time}\n\n"
            
            for addr in addresses:
                result_message += self.format_address_info(addr) + "\n\n"
                
            # 分段发送消息，避免消息过长
            if len(result_message) > 4000:
                # 如果消息太长，分段发送
                await wait_message.delete()
                chunks = [result_message[i:i+4000] for i in range(0, len(result_message), 4000)]
                for chunk in chunks:
                    await update.message.reply_text(
                        chunk,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
            else:
                # 消息长度合适，直接更新
                try:
                    await wait_message.edit_text(
                        result_message,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                except Exception as e:
                    # 如果编辑失败，尝试发送新消息
                    await wait_message.delete()
                    await update.message.reply_text(
                        result_message,
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
            
        except Exception as e:
            logger.error(f"查询出错: {e}")
            try:
                await wait_message.edit_text("❌ 查询过程中出现错误，请稍后重试")
            except:
                await update.message.reply_text("❌ 查询过程中出现错误，请稍后重试")
            
    async def broadcast_addresses(self, specific_chat_id: Optional[int] = None) -> None:
        """向活跃的频道广播地址信息"""
        try:
            addresses = self.finder.find_low_cost_energy_addresses()
            if not addresses:
                return
                
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            message = f"⏰ 定时推送 - {current_time}\n\n"
            
            for addr in addresses:
                message += self.format_address_info(addr) + "\n\n"
                
            # 如果指定了特定的chat_id，只发送给该chat
            if specific_chat_id is not None:
                await self.send_message_to_chat(specific_chat_id, message)
                return
                
            # 否则发送给所有活跃的频道
            for channel_id in self.active_channels:
                await self.send_message_to_chat(channel_id, message)
                    
        except Exception as e:
            logger.error(f"广播地址时出错: {e}")
            
    async def handle_new_chat_members(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理机器人被添加到新频道的事件"""
        try:
            chat = update.message.chat
            if chat.type in ['channel', 'supergroup']:
                if chat.id not in self.subscribed_channels:
                    self.subscribed_channels.append(chat.id)
                    logger.info(f"机器人被添加到新频道: {chat.id}")
                    
                    # 立即发送一次地址信息
                    await self.broadcast_addresses()
                    
        except Exception as e:
            logger.error(f"处理新成员事件时出错: {e}")
            
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理错误"""
        logger.error(f"更新 {update} 导致错误 {context.error}")
        
    def run(self):
        """运行机器人"""
        try:
            # 创建应用
            self.application = Application.builder().token(self.token).build()
            
            # 添加命令处理器
            self.application.add_handler(CommandHandler("start", self.start_command))
            self.application.add_handler(CommandHandler("help", self.help_command))
            self.application.add_handler(CommandHandler("query", self.query_command))
            self.application.add_handler(CommandHandler("start_push", self.start_push_command))
            self.application.add_handler(CommandHandler("stop_push", self.stop_push_command))
            
            # 添加错误处理器
            self.application.add_error_handler(self.error_handler)
            
            # 设置定时任务
            self.scheduler.add_job(
                self.broadcast_addresses,
                'interval',
                hours=1,
                id='broadcast_job'
            )
            
            # 启动调度器
            self.scheduler.start()
            
            # 启动机器人
            self.application.run_polling()
            
        except Exception as e:
            logger.error(f"启动机器人时出错: {e}")
            raise

def main():
    """主函数"""
    try:
        bot = TronEnergyBot()
        bot.run()
    except Exception as e:
        logger.error(f"运行机器人时出错: {e}")
        raise

if __name__ == "__main__":
    main() 