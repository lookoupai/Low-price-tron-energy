import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Set
import asyncio
import time

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
from functools import lru_cache
from cachetools import TTLCache

from tron_energy_finder import TronEnergyFinder

# 配置日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 设置httpx日志级别为WARNING，避免显示敏感URL
logging.getLogger("httpx").setLevel(logging.WARNING)

class TronEnergyBot:
    def __init__(self):
        # 加载环境变量
        load_dotenv()
        
        # 获取Telegram Bot Token
        self.token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.token:
            raise ValueError("请在.env文件中设置TELEGRAM_BOT_TOKEN")
        else:
            # 只显示token的前8位，其余用*代替
            token_preview = self.token[:8] + "*" * (len(self.token) - 8)
            logger.info(f"成功加载 TELEGRAM_BOT_TOKEN: {token_preview}")
            
        # 获取广告内容
        self.advertisement = os.getenv("BOT_ADVERTISEMENT", "").strip()
        if self.advertisement:
            logger.info("成功加载广告内容")
            
        # 初始化TronEnergyFinder
        self.finder = TronEnergyFinder()
        
        # 初始化调度器
        self.scheduler = AsyncIOScheduler()
        
        # 存储活跃的频道（启用了推送的频道）
        self.active_channels: Set[int] = set()
        
        # 添加并发控制
        self._query_lock = asyncio.Lock()
        self._query_semaphore = asyncio.Semaphore(3)  # 最多同时处理3个查询
        self._user_cooldowns = TTLCache(maxsize=1000, ttl=60)  # 用户冷却时间缓存
        self._min_query_interval = 60  # 用户查询间隔（秒）
        
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
        
    async def check_admin_rights(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """检查命令发送者是否为管理员"""
        try:
            chat = update.effective_chat
            if not chat:
                return False
                
            # 私聊情况下不需要检查权限
            if chat.type == "private":
                return True
                
            # 频道消息直接返回True（因为只有管理员才能在频道发消息）
            if chat.type == "channel":
                return True
                
            # 获取用户在群组中的权限
            user = update.effective_user
            if not user:
                return False
                
            member = await chat.get_member(user.id)
            return member.status in ['creator', 'administrator']
            
        except TelegramError as e:
            logger.error(f"检查管理员权限时出错: {e}")
            return False
            
    async def start_push_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /start_push 命令"""
        try:
            # 获取聊天类型和ID
            chat = update.effective_chat
            if not chat:
                return
            
            logger.info(f"收到 start_push 命令，chat_id={chat.id}, chat_type={chat.type}")
            
            # 检查是否是频道或群组
            if chat.type in ['channel', 'supergroup', 'group']:
                # 对于频道消息，我们直接添加到活跃频道列表
                self.active_channels.add(chat.id)
                logger.info(f"已将频道 {chat.id} 添加到活跃列表")
                
                try:
                    # 发送确认消息
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text="✅ 已开启能量地址推送服务！正在为您查询最新地址..."
                    )
                    logger.info(f"已发送确认消息到频道 {chat.id}")
                    
                    # 立即执行一次查询
                    await self.broadcast_addresses(context, chat.id)
                    logger.info(f"已执行初始查询，chat_id={chat.id}")
                    
                except Exception as e:
                    logger.error(f"发送消息到频道 {chat.id} 失败: {e}")
                return
            
            # 如果是私聊，检查管理员权限
            is_admin = await self.check_admin_rights(update, context)
            if not is_admin:
                await update.message.reply_text("❌ 抱歉，只有管理员可以使用此命令。")
                return
            
            # 添加到活跃频道列表
            self.active_channels.add(chat.id)
            await update.message.reply_text("✅ 已开启能量地址推送服务！")
            logger.info(f"已启用聊天 {chat.id} 的推送服务")
            
        except Exception as e:
            logger.error(f"处理 start_push 命令时出错: {e}", exc_info=True)
            await self._handle_error(update, context, str(e))

    async def stop_push_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /stop_push 命令"""
        try:
            # 获取聊天类型和ID
            chat = update.effective_chat
            if not chat:
                return
            
            # 检查是否是频道或群组
            if chat.type in ['channel', 'supergroup', 'group']:
                # 对于频道消息，直接从活跃频道列表中移除
                self.active_channels.discard(chat.id)
                
                # 发送确认消息
                await context.bot.send_message(
                    chat_id=chat.id,
                    text="✅ 已关闭能量地址推送服务。如需重新开启，请使用 /start_push 命令。"
                )
                logger.info(f"已禁用频道 {chat.id} 的推送服务")
                return
            
            # 如果是私聊，检查管理员权限
            is_admin = await self.check_admin_rights(update, context)
            if not is_admin:
                await update.message.reply_text("❌ 抱歉，只有管理员可以使用此命令。")
                return
            
            # 从活跃频道列表中移除
            self.active_channels.discard(chat.id)
            await update.message.reply_text("✅ 已关闭能量地址推送服务。")
            logger.info(f"已禁用聊天 {chat.id} 的推送服务")
            
        except Exception as e:
            logger.error(f"处理 stop_push 命令时出错: {e}")
            await self._handle_error(update, context, str(e))

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
            
        message = (
            f"🔹 【收款地址】: `{addr['address']}`\n"
            f"🔹 【能量提供方】: `{addr['energy_provider']}`\n"
            f"🔹 【购买记录】: [查看](https://tronscan.org/#/address/{addr['address']})\n"
            f"🔹 【收款金额】: {addr['purchase_amount']} TRX\n"
            f"🔹 【能量数量】: {energy_display}\n"
            f"🔹 【24h交易数】: {addr['recent_tx_count']} 笔\n"
            f"🔹 【转账哈希】: `{addr['tx_hash']}`\n"
            f"🔹 【代理哈希】: `{addr['proxy_tx_hash']}`\n\n"
            f"🎊 【地址状态】{addr['status']}\n\n"
            f"🈹 TRX #{addr['purchase_amount']}"  # 添加金额标签
        )

        # 如果配置了广告内容，添加到消息末尾
        if self.advertisement:
            message += f"\n\n{self.advertisement}"
            
        return message
        
    async def _check_user_cooldown(self, user_id: int) -> bool:
        """检查用户是否在冷却时间内"""
        if user_id in self._user_cooldowns:
            last_query_time = self._user_cooldowns[user_id]
            time_passed = time.time() - last_query_time
            if time_passed < self._min_query_interval:
                return False
        return True
        
    async def query_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理/query命令"""
        try:
            user = update.effective_user
            if not user:
                return
                
            # 检查用户冷却时间
            if not await self._check_user_cooldown(user.id):
                remaining_time = int(self._min_query_interval - (time.time() - self._user_cooldowns[user.id]))
                await update.message.reply_text(
                    f"⏳ 请等待 {remaining_time} 秒后再次查询"
                )
                return
                
            # 使用信号量控制并发
            async with self._query_semaphore:
                # 更新用户最后查询时间
                self._user_cooldowns[user.id] = time.time()
                
                # 发送等待消息
                wait_message = await update.message.reply_text(
                    "🔍 正在查找低成本能量代理地址，请稍候..."
                )
                
                # 执行查找
                addresses = await self.finder.find_low_cost_energy_addresses()
                
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
            
    async def broadcast_addresses(self, context: ContextTypes.DEFAULT_TYPE, specific_chat_id: Optional[int] = None) -> None:
        """向活跃的频道广播地址信息"""
        try:
            logger.info(f"开始广播地址信息 specific_chat_id={specific_chat_id}")
            
            # 使用信号量控制并发
            async with self._query_semaphore:
                # 如果是定时任务调用且没有活跃频道，直接返回
                if specific_chat_id is None and not self.active_channels:
                    logger.info("没有活跃的频道，跳过广播")
                    return
                    
                addresses = await self.finder.find_low_cost_energy_addresses()
                
                if not addresses:
                    # 如果没找到地址，发送提示消息
                    message = "❌ 暂时没有找到符合条件的低价能量地址，稍后将继续为您查询..."
                    if specific_chat_id is not None:
                        try:
                            await context.bot.send_message(
                                chat_id=specific_chat_id,
                                text=message
                            )
                            logger.info(f"发送'未找到地址'消息到频道 {specific_chat_id}")
                        except Exception as e:
                            logger.error(f"发送消息到频道 {specific_chat_id} 失败: {e}")
                    return
                
                # 构建消息
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = f"⏰ 定时推送 - {current_time}\n\n"
                
                for addr in addresses:
                    message += self.format_address_info(addr) + "\n\n"
                
                # 如果指定了特定的chat_id，只发送给该chat
                if specific_chat_id is not None:
                    try:
                        logger.info(f"尝试发送消息到特定频道 {specific_chat_id}")
                        await context.bot.send_message(
                            chat_id=specific_chat_id,
                            text=message,
                            parse_mode='Markdown',
                            disable_web_page_preview=True
                        )
                        logger.info(f"成功发送消息到频道 {specific_chat_id}")
                    except Exception as e:
                        logger.error(f"发送消息到频道 {specific_chat_id} 失败: {e}")
                    return
                
                # 否则发送给所有活跃的频道
                logger.info(f"开始向所有活跃频道广播消息，活跃频道数: {len(self.active_channels)}")
                for channel_id in self.active_channels:
                    try:
                        logger.info(f"尝试发送消息到频道 {channel_id}")
                        await context.bot.send_message(
                            chat_id=channel_id,
                            text=message,
                            parse_mode='Markdown',
                            disable_web_page_preview=True
                        )
                        logger.info(f"成功发送消息到频道 {channel_id}")
                    except Exception as e:
                        logger.error(f"发送消息到频道 {channel_id} 失败: {e}")
                        continue
            
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
        logger.error(f"更新 {update} 导致错误 {context.error}", exc_info=context.error)
        
    def run(self):
        """运行机器人"""
        try:
            # 创建应用
            self.application = Application.builder().token(self.token).build()
            
            # 添加命令处理器，允许在频道中使用命令
            self.application.add_handler(CommandHandler("start", self.start_command, filters.ChatType.PRIVATE))
            self.application.add_handler(CommandHandler("help", self.help_command, filters.ChatType.PRIVATE))
            self.application.add_handler(CommandHandler("query", self.query_command))
            self.application.add_handler(CommandHandler(
                "start_push", 
                self.start_push_command,
                filters.ChatType.CHANNEL | filters.ChatType.GROUPS | filters.ChatType.PRIVATE
            ))
            self.application.add_handler(CommandHandler(
                "stop_push", 
                self.stop_push_command,
                filters.ChatType.CHANNEL | filters.ChatType.GROUPS | filters.ChatType.PRIVATE
            ))
            
            # 添加新成员处理器
            self.application.add_handler(MessageHandler(
                filters.StatusUpdate.NEW_CHAT_MEMBERS,
                self.handle_new_chat_members
            ))
            
            # 添加错误处理器
            self.application.add_error_handler(self.error_handler)
            
            # 设置定时任务（改为启动后5分钟开始第一次检查）
            job_queue = self.application.job_queue
            job_queue.run_repeating(
                self.broadcast_addresses,
                interval=3600,  # 每小时运行一次
                first=300  # 启动5分钟后运行第一次
            )
            
            logger.info("机器人启动成功，等待命令...")
            
            # 启动机器人，允许处理频道消息
            self.application.run_polling(
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            
        except Exception as e:
            logger.error(f"启动机器人时出错: {e}")
            raise

    async def _handle_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE, error_message: str) -> None:
        """统一的错误处理方法"""
        try:
            if update.effective_message:
                await update.effective_message.reply_text(
                    f"❌ 操作失败: {error_message}"
                )
        except Exception as e:
            logger.error(f"发送错误消息失败: {e}")

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