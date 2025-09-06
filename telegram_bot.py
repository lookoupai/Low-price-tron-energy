import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Set
import asyncio
import time
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from telegram.error import TelegramError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from functools import lru_cache
from cachetools import TTLCache

from tron_energy_finder import TronEnergyFinder
from blacklist_manager import BlacklistManager
from whitelist_manager import WhitelistManager
from settings_manager import SettingsManager

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
        
        # 初始化黑名单管理器
        self.blacklist_manager = BlacklistManager()
        # 初始化白名单管理器
        self.whitelist_manager = WhitelistManager()
        # 设置管理器
        self.settings_manager = SettingsManager()
        
        # 初始化调度器
        self.scheduler = AsyncIOScheduler()
        
        # 存储活跃的频道（启用了推送的频道）
        self.active_channels: Set[int] = set()
        
        # 添加并发控制
        self._query_lock = asyncio.Lock()
        self._query_semaphore = asyncio.Semaphore(3)  # 最多同时处理3个查询
        self._user_cooldowns = TTLCache(maxsize=1000, ttl=60)  # 用户冷却时间缓存
        self._min_query_interval = 60  # 用户查询间隔（秒）
        
        # TRON地址检测正则表达式
        self.tron_address_pattern = re.compile(r'\b(T[1-9A-HJ-NP-Za-km-z]{33})\b')
        
        # 回调负载缓存（避免超长callback_data）- 延长到7天
        self._cb_payloads: TTLCache = TTLCache(maxsize=1000, ttl=604800)  # 7天 = 7*24*3600秒

    def _store_cb_payload(self, payment: str, provider: str) -> str:
        import uuid
        key = uuid.uuid4().hex[:10]
        self._cb_payloads[key] = (payment, provider)
        return key

    def _get_cb_payload(self, key: str):
        return self._cb_payloads.get(key)
    
    def _parse_message_for_addresses(self, message_text: str) -> Optional[tuple]:
        """从消息文本中解析收款地址和能量提供方作为兜底方案"""
        try:
            lines = message_text.split('\n')
            payment_address = None
            provider_address = None
            
            for line in lines:
                # 查找收款地址行
                if '【收款地址】' in line and '`' in line:
                    # 提取反引号内的地址
                    start = line.find('`') + 1
                    end = line.rfind('`')
                    if start > 0 and end > start:
                        payment_address = line[start:end]
                
                # 查找能量提供方行  
                elif '【能量提供方】' in line and '`' in line:
                    start = line.find('`') + 1
                    end = line.rfind('`')
                    if start > 0 and end > start:
                        provider_address = line[start:end]
            
            # 验证提取的地址格式
            if payment_address and provider_address:
                if (self.blacklist_manager._validate_tron_address(payment_address) and 
                    self.blacklist_manager._validate_tron_address(provider_address)):
                    return (payment_address, provider_address)
            
            return None
            
        except Exception as e:
            logger.error(f"解析消息文本失败: {e}")
            return None
    
    def _is_message_expired(self, message_date, days=7) -> bool:
        """判断消息是否过期（默认7天）"""
        try:
            from datetime import datetime, timezone, timedelta
            current_time = datetime.now(timezone.utc)
            time_diff = current_time - message_date
            return time_diff > timedelta(days=days)
        except Exception as e:
            logger.error(f"判断消息过期失败: {e}")
            return False

    def _build_inline_keyboard(self, addr: Dict) -> InlineKeyboardMarkup:
        """为单条地址信息构建操作按钮"""
        payment = addr.get('address')
        provider = addr.get('energy_provider')
        payload_key = self._store_cb_payload(payment, provider)
        buttons = [
            [
                InlineKeyboardButton(
                    text='✅ 我已成功获得能量（两者加入白名单）',
                    callback_data=f"vote_success:{payload_key}"
                )
            ],
            [
                InlineKeyboardButton(
                    text='❌ 我未获得能量（两者加入黑名单）',
                    callback_data=f"vote_fail:{payload_key}"
                )
            ],
            [
                InlineKeyboardButton(
                    text='▶️ 更多操作',
                    callback_data=f"more_ops:{payload_key}"
                )
            ]
        ]
        return InlineKeyboardMarkup(buttons)
        
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
            "   /query - 立即查询一次\n"
            "   /channels - 查看活跃频道列表（仅管理员）\n\n"
            "3️⃣ 黑名单功能：\n"
            "   /blacklist_add <地址> [原因] - 添加地址到黑名单\n"
            "   /blacklist_check <地址> - 查询地址黑名单状态\n"
            "   /blacklist_remove <地址> - 从黑名单移除地址（仅管理员）\n"
            "   /blacklist_stats - 查看黑名单统计信息\n\n"
            "4️⃣ 白名单功能：\n"
            "   /whitelist_add <地址> <payment|provider> [原因] - 添加地址到白名单\n"
            "   /whitelist_check <地址> <payment|provider> - 查询白名单状态\n"
            "   /whitelist_remove <地址> <payment|provider> - 移除白名单（仅管理员）\n"
            "   /whitelist_stats - 查看白名单统计信息\n\n"
            "5️⃣ 管理员设置：\n"
            "   /assoc on|off|status - 黑名单关联开关（仅管理员）\n\n"
            "6️⃣ 地址检测：\n"
            "   直接发送TRON地址自动检查黑名单状态\n\n"
            "💡 注意事项：\n"
            "   • 频道/群组中使用命令需要授予机器人管理员权限\n"
            "   • 查询结果中会显示黑/白名单警告信息\n"
            "   • 发现可疑地址请及时举报到黑名单"
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
            
    async def blacklist_add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """添加地址到黑名单"""
        try:
            # 检查参数
            if not context.args:
                await update.message.reply_text("❌ 请提供地址参数\n\n使用方法: `/blacklist_add <地址> [原因]`", parse_mode='Markdown')
                return
                
            address = context.args[0]
            reason = " ".join(context.args[1:]) if len(context.args) > 1 else f"用户 {update.effective_user.id} 举报"
            
            # 验证地址格式
            if not self.blacklist_manager._validate_tron_address(address):
                await update.message.reply_text("❌ 无效的TRON地址格式")
                return
                
            # 初始化黑名单管理器
            if self.blacklist_manager._connection_pool is None:
                await self.blacklist_manager.init_database()
                
            # 添加到黑名单
            success = await self.blacklist_manager.add_to_blacklist(
                address, reason, update.effective_user.id
            )
            
            if success:
                await update.message.reply_text(
                    f"✅ 地址已添加到黑名单\n\n"
                    f"📍 **地址**: `{address}`\n"
                    f"📝 **原因**: {reason}\n"
                    f"👤 **提交者**: {update.effective_user.id}",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ 添加失败，请检查地址格式或稍后重试")
                
        except Exception as e:
            logger.error(f"添加黑名单命令出错: {e}")
            await self._handle_error(update, context, str(e))
            
    async def blacklist_check_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """查询地址黑名单状态"""
        try:
            # 检查参数
            if not context.args:
                await update.message.reply_text("❌ 请提供地址参数\n\n使用方法: `/blacklist_check <地址>`", parse_mode='Markdown')
                return
                
            address = context.args[0]
            
            # 验证地址格式
            if not self.blacklist_manager._validate_tron_address(address):
                await update.message.reply_text("❌ 无效的TRON地址格式")
                return
                
            # 初始化黑名单管理器
            if self.blacklist_manager._connection_pool is None:
                await self.blacklist_manager.init_database()
                
            # 检查黑名单
            blacklist_info = await self.blacklist_manager.check_blacklist(address)
            
            if blacklist_info:
                added_time = blacklist_info['added_at'].strftime("%Y-%m-%d %H:%M:%S") if blacklist_info['added_at'] else "未知"
                
                message = f"""🔍 **黑名单查询结果**

📍 **地址**: `{address}`

❌ **状态**: 已列入黑名单
📝 **原因**: {blacklist_info['reason'] or '未提供原因'}
⏰ **添加时间**: {added_time}
🔖 **类型**: {'手动添加' if blacklist_info['type'] == 'manual' else '自动关联'}
👤 **添加者**: {blacklist_info['added_by'] or '未知'}

⚠️ **风险提醒**: 此地址可能存在白名单限制，直接转TRX可能无法获得能量！"""
            else:
                message = f"""🔍 **黑名单查询结果**

📍 **地址**: `{address}`

✅ **状态**: 未列入黑名单
💡 **提示**: 该地址目前没有被举报"""
                
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"查询黑名单命令出错: {e}")
            await self._handle_error(update, context, str(e))
            
    async def blacklist_remove_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """从黑名单中移除地址（仅管理员）"""
        try:
            # 检查管理员权限
            if not await self.check_admin_rights(update, context):
                await update.message.reply_text("❌ 您没有权限执行此操作，只有管理员可以移除黑名单")
                return
                
            # 检查参数
            if not context.args:
                await update.message.reply_text("❌ 请提供地址参数\n\n使用方法: `/blacklist_remove <地址>`", parse_mode='Markdown')
                return
                
            address = context.args[0]
            
            # 验证地址格式
            if not self.blacklist_manager._validate_tron_address(address):
                await update.message.reply_text("❌ 无效的TRON地址格式")
                return
                
            # 初始化黑名单管理器
            if self.blacklist_manager._connection_pool is None:
                await self.blacklist_manager.init_database()
                
            # 从黑名单中移除
            success = await self.blacklist_manager.remove_from_blacklist(address)
            
            if success:
                await update.message.reply_text(
                    f"✅ 地址已从黑名单中移除\n\n"
                    f"📍 **地址**: `{address}`\n"
                    f"👤 **操作者**: {update.effective_user.id}",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("❌ 移除失败，请稍后重试")
                
        except Exception as e:
            logger.error(f"移除黑名单命令出错: {e}")
            await self._handle_error(update, context, str(e))
            
    async def blacklist_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """查看黑名单统计信息"""
        try:
            # 初始化黑名单管理器
            if self.blacklist_manager._connection_pool is None:
                await self.blacklist_manager.init_database()
                
            # 获取统计信息
            stats = await self.blacklist_manager.get_blacklist_stats()
            
            if stats:
                message = f"""📊 **黑名单统计信息**

📈 **总数量**: {stats.get('total', 0)} 个地址
👤 **手动添加**: {stats.get('manual', 0)} 个地址
🔗 **自动关联**: {stats.get('auto_associated', 0)} 个地址

💡 **说明**: 
- 手动添加：用户主动举报的地址
- 自动关联：系统检测到与黑名单地址有关联的地址"""
            else:
                message = "📊 **黑名单统计信息**\n\n暂无统计数据"
                
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"查看黑名单统计出错: {e}")
            await self._handle_error(update, context, str(e))

    async def whitelist_add_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            if not context.args or len(context.args) < 2:
                await update.message.reply_text("❌ 用法：/whitelist_add <地址> <payment|provider> [原因]")
                return
            address = context.args[0]
            addr_type = context.args[1]
            reason = " ".join(context.args[2:]) if len(context.args) > 2 else f"用户 {update.effective_user.id} 添加"
            await self.whitelist_manager.add_address(address, addr_type, reason, update.effective_user.id, is_provisional=True)
            await update.message.reply_text(f"✅ 已将 {address} 作为 {addr_type} 加入白名单（临时）。")
        except Exception as e:
            logger.error(f"whitelist_add 出错: {e}")
            await self._handle_error(update, context, str(e))

    async def whitelist_check_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            if not context.args or len(context.args) < 2:
                await update.message.reply_text("❌ 用法：/whitelist_check <地址> <payment|provider>")
                return
            address = context.args[0]
            addr_type = context.args[1]
            info = await self.whitelist_manager.check_address(address, addr_type)
            if info:
                provisional = '（临时）' if info.get('is_provisional') else ''
                await update.message.reply_text(
                    f"✅ 白名单：{address} ({addr_type}) {provisional}\n次数：{info.get('success_count', 1)}"
                )
            else:
                await update.message.reply_text("ℹ️ 未找到白名单记录")
        except Exception as e:
            logger.error(f"whitelist_check 出错: {e}")
            await self._handle_error(update, context, str(e))

    async def whitelist_remove_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            if not await self.check_admin_rights(update, context):
                await update.message.reply_text("❌ 您没有权限执行此操作，只有管理员可以移除白名单")
                return
            if not context.args or len(context.args) < 2:
                await update.message.reply_text("❌ 用法：/whitelist_remove <地址> <payment|provider>")
                return
            address = context.args[0]
            addr_type = context.args[1]
            await self.whitelist_manager.remove_address(address, addr_type)
            await update.message.reply_text(f"✅ 已移除白名单：{address} ({addr_type})")
        except Exception as e:
            logger.error(f"whitelist_remove 出错: {e}")
            await self._handle_error(update, context, str(e))

    async def whitelist_stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            stats = await self.whitelist_manager.get_stats()
            await update.message.reply_text(
                f"📊 白名单统计：\n单地址：{stats.get('addresses', 0)}\n组合：{stats.get('pairs', 0)}"
            )
        except Exception as e:
            logger.error(f"whitelist_stats 出错: {e}")
            await self._handle_error(update, context, str(e))

    async def assoc_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """黑名单关联开关：/assoc on | off | status"""
        try:
            if not await self.check_admin_rights(update, context):
                await update.message.reply_text("❌ 您没有权限执行此操作，只有管理员可以配置关联开关")
                return
            if not context.args:
                await update.message.reply_text("用法：/assoc on|off|status")
                return
            sub = context.args[0].lower()
            if sub == 'on':
                await self.settings_manager.set_blacklist_association_enabled(True)
                await update.message.reply_text("✅ 已开启黑名单单向关联（提供方→收款地址）")
            elif sub == 'off':
                await self.settings_manager.set_blacklist_association_enabled(False)
                await update.message.reply_text("✅ 已关闭黑名单单向关联")
            else:
                enabled = await self.settings_manager.is_blacklist_association_enabled()
                await update.message.reply_text(
                    f"当前状态：{'开启' if enabled else '关闭'}"
                )
        except Exception as e:
            logger.error(f"assoc 命令出错: {e}")
            await self._handle_error(update, context, str(e))

    async def channels_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """查看当前活跃频道列表：/channels"""
        try:
            if not await self.check_admin_rights(update, context):
                await update.message.reply_text("❌ 您没有权限执行此操作，只有管理员可以查看频道列表")
                return
                
            if not self.active_channels:
                await update.message.reply_text("📋 **活跃频道列表**\n\n暂无活跃频道", parse_mode='Markdown')
                return
                
            message = "📋 **活跃频道列表**\n\n"
            message += f"📊 **总数：** {len(self.active_channels)} 个频道\n\n"
            
            for i, channel_id in enumerate(self.active_channels, 1):
                try:
                    # 尝试获取频道信息
                    chat = await context.bot.get_chat(channel_id)
                    chat_title = chat.title or f"未知频道 ({channel_id})"
                    chat_type = chat.type
                    message += f"{i}. **{chat_title}**\n   ID: `{channel_id}`\n   类型: {chat_type}\n\n"
                except Exception as e:
                    # 如果无法获取频道信息，显示错误
                    message += f"{i}. **无效频道**\n   ID: `{channel_id}`\n   错误: {str(e)[:50]}\n\n"
                    
            message += "📝 **说明：**\n"
            message += "- 使用 `/stop_push` 在对应频道中关闭推送\n"
            message += "- 无效频道将在下次发送失败时自动移除"
                
            await update.message.reply_text(message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"channels 命令出错: {e}")
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

    def _escape_markdown(self, text: str) -> str:
        """转义 Markdown 特殊字符"""
        # 转义 Markdown 中的特殊字符
        special_chars = ['<', '>', '[', ']', '(', ')', '*', '_', '`', '~']
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text

    def format_address_info(self, addr: Dict) -> str:
        """格式化地址信息为消息文本，包含分层状态展示（方案A）"""
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
        )

        # 分层状态展示
        message += "📊 状态分析：\n"
        # 白名单
        wl_notice = addr.get('whitelist_notice') or ""
        if wl_notice:
            message += f"✅ 白名单状态：\n  └ {wl_notice}\n"
        else:
            # 逐项显示
            if addr.get('payment_whitelisted'):
                message += "✅ 白名单状态：\n  └ 收款地址：已在白名单\n"
            if addr.get('provider_whitelisted'):
                if '✅ 白名单状态' not in message:
                    message += "✅ 白名单状态：\n"
                message += "  └ 能量提供方：已在白名单\n"
            if not (addr.get('payment_whitelisted') or addr.get('provider_whitelisted')):
                message += "✅ 白名单状态：暂无记录\n"

        # 黑名单
        bl_warn = addr.get('blacklist_warning') or ""
        if bl_warn:
            message += f"\n⚠️ 黑名单状态：\n{bl_warn}\n"
        else:
            message += "\n⚠️ 黑名单状态：暂无记录\n"
            
        message += f"\n🈹 TRX #{addr['purchase_amount']}\n"
        message += "\n按钮说明：成功=两者加白；未成功=两者加黑；更多=展开单独添加/撤回"

        # 如果配置了广告内容，添加到消息末尾
        if self.advertisement:
            # 对于广告内容，我们使用 HTML 模式发送，避免 Markdown 解析问题
            # 但这里先简单替换最常见的问题字符
            safe_ad = self.advertisement.replace('\\n', '\n')  # 处理换行符
            message += f"\n\n{safe_ad}"
            
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
                    
                # 删除等待消息，避免出现额外的时间/提示消息
                try:
                    await wait_message.delete()
                except Exception:
                    pass

                # 为每条地址单独发送消息，并在顶部包含时间
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                prefix = f"🎯 查询时间：{current_time}\n\n"
                for addr in addresses:
                    text = prefix + self.format_address_info(addr)
                    markup = self._build_inline_keyboard(addr)
                    try:
                        await update.message.reply_text(
                            text=text,
                            parse_mode='Markdown',
                            disable_web_page_preview=True,
                            reply_markup=markup,
                        )
                    except Exception:
                        await update.message.reply_text(
                            text=text,
                            disable_web_page_preview=True,
                            reply_markup=markup,
                        )
            
        except Exception as e:
            logger.error(f"查询出错: {e}")
            try:
                await wait_message.edit_text("❌ 查询过程中出现错误，请稍后重试")
            except:
                await update.message.reply_text("❌ 查询过程中出现错误，请稍后重试")

    async def inline_button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理内联按钮回调"""
        try:
            query = update.callback_query
            if not query or not query.data:
                return
            
            # 检查消息是否过期（7天）
            message_date = query.message.date if query.message else None
            is_expired = message_date and self._is_message_expired(message_date)
            
            action, _, key = query.data.partition(":")
            payload = self._get_cb_payload(key)
            
            # 处理过期或缓存丢失的情况
            if not payload or is_expired:
                # 尝试从消息文本解析地址作为兜底
                fallback_addresses = None
                if query.message and query.message.text:
                    fallback_addresses = self._parse_message_for_addresses(query.message.text)
                
                if not fallback_addresses:
                    # 无法解析，提示过期并更新按钮
                    await query.answer("该消息已过期且无法解析地址信息，请使用最新结果", show_alert=True)
                    try:
                        expired_buttons = [[
                            InlineKeyboardButton("已过期（获取最新）", callback_data="expired_get_new")
                        ]]
                        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(expired_buttons))
                    except Exception:
                        pass
                    return
                else:
                    # 成功解析到地址，询问是否继续
                    if action in ['vote_success', 'vote_fail', 'more_ops']:
                        await query.answer("该消息已过期，是否仍要操作？", show_alert=True)
                        try:
                            continue_buttons = [[
                                InlineKeyboardButton("仍要操作", callback_data=f"continue_{action}:{key}"),
                                InlineKeyboardButton("取消", callback_data="cancel_expired")
                            ]]
                            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(continue_buttons))
                        except Exception:
                            pass
                        return
                    elif action.startswith('continue_'):
                        # 用户确认继续操作，使用解析出的地址
                        payment, provider = fallback_addresses
                        actual_action = action.replace('continue_', '')
                        await query.answer("已使用解析的地址信息执行操作")
                        # 继续执行下面的逻辑，使用解析出的地址
                        action = actual_action
                    else:
                        await query.answer("操作已取消")
                        return
            else:
                # 未过期且有缓存数据，正常处理
                payment, provider = payload
                await query.answer()

            user_id = update.effective_user.id if update.effective_user else None
            if action == 'vote_success':
                # 两者加入白名单（临时）+ 组合白名单
                await self.whitelist_manager.add_address(payment, 'payment', f'用户{user_id}反馈成功', user_id, is_provisional=True)
                await self.whitelist_manager.add_address(provider, 'provider', f'用户{user_id}反馈成功', user_id, is_provisional=True)
                await self.whitelist_manager.add_pair(payment, provider, user_id, is_provisional=True)
                
                # 发送确认消息（不编辑原文）
                confirmation_text = (
                    "✅ 已记录：您已成功获得能量\n\n"
                    "• 收款地址与能量提供方已加入白名单（临时）\n"
                    "• 如需撤回，请联系管理员\n"
                    "• 感谢您的反馈，已帮助他人判断可信地址"
                )
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=confirmation_text,
                    reply_to_message_id=query.message.message_id
                )
                
                # 更新按钮为已记录状态
                try:
                    recorded_buttons = [[
                        InlineKeyboardButton("✅ 已记录为成功", callback_data="recorded_success"),
                        InlineKeyboardButton("撤回", callback_data=f"revoke_success:{key}")
                    ]]
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(recorded_buttons))
                except Exception:
                    pass
            elif action == 'vote_fail':
                # 两者加入黑名单（临时），并尝试单向关联（提供方→收款地址）
                await self.blacklist_manager.add_to_blacklist(payment, f'用户{user_id}反馈未成功', user_id, 'manual', is_provisional=True)
                await self.blacklist_manager.add_to_blacklist(provider, f'用户{user_id}反馈未成功', user_id, 'manual', is_provisional=True)
                # 触发一次关联逻辑（内部有开关）
                try:
                    await self.blacklist_manager.auto_associate_addresses(payment, provider)
                except Exception:
                    pass
                
                # 发送确认消息（不编辑原文）
                confirmation_text = (
                    "❌ 已记录：您未成功获得能量\n\n"
                    "• 收款地址与能量提供方已加入黑名单（临时）\n"
                    "• 如需撤回，请联系管理员\n"
                    "• 感谢您的反馈，已帮助他人规避风险"
                )
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=confirmation_text,
                    reply_to_message_id=query.message.message_id
                )
                
                # 更新按钮为已记录状态
                try:
                    recorded_buttons = [[
                        InlineKeyboardButton("❌ 已记录为失败", callback_data="recorded_fail"),
                        InlineKeyboardButton("撤回", callback_data=f"revoke_fail:{key}")
                    ]]
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(recorded_buttons))
                except Exception:
                    pass
            elif action == 'more_ops':
                # 展开更多操作选择
                buttons = [
                    [InlineKeyboardButton('🧩 仅收款地址成功（加白）', callback_data=f'only_pay_wl:{key}')],
                    [InlineKeyboardButton('🔋 仅提供方成功（加白）', callback_data=f'only_prov_wl:{key}')],
                    [InlineKeyboardButton('🚩 仅收款地址有问题（加黑）', callback_data=f'only_pay_bl:{key}')],
                    [InlineKeyboardButton('🧨 仅提供方有问题（加黑）', callback_data=f'only_prov_bl:{key}')],
                    [InlineKeyboardButton('↩️ 撤回我的反馈', callback_data=f'revoke:{key}')],
                    [InlineKeyboardButton('❌ 取消', callback_data=f'cancel:{key}')],
                ]
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
            elif action == 'only_pay_wl':
                await self.whitelist_manager.add_address(payment, 'payment', '用户反馈：仅收款地址成功', user_id, is_provisional=True)
                # 发送确认消息
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text='✅ 已记录：仅收款地址加入白名单（临时）。如需撤回，请联系管理员。',
                    reply_to_message_id=query.message.message_id
                )
                # 更新按钮
                try:
                    recorded_buttons = [[InlineKeyboardButton("✅ 收款地址已加白", callback_data="recorded")]]
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(recorded_buttons))
                except Exception:
                    pass
            elif action == 'only_prov_wl':
                await self.whitelist_manager.add_address(provider, 'provider', '用户反馈：仅提供方成功', user_id, is_provisional=True)
                # 发送确认消息
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text='✅ 已记录：仅能量提供方加入白名单（临时）。如需撤回，请联系管理员。',
                    reply_to_message_id=query.message.message_id
                )
                # 更新按钮
                try:
                    recorded_buttons = [[InlineKeyboardButton("✅ 提供方已加白", callback_data="recorded")]]
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(recorded_buttons))
                except Exception:
                    pass
            elif action == 'only_pay_bl':
                await self.blacklist_manager.add_to_blacklist(payment, '用户反馈：仅收款地址有问题', user_id, 'manual', is_provisional=True)
                # 发送确认消息
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text='❌ 已记录：仅收款地址加入黑名单（临时）。如需撤回，请联系管理员。',
                    reply_to_message_id=query.message.message_id
                )
                # 更新按钮
                try:
                    recorded_buttons = [[InlineKeyboardButton("❌ 收款地址已加黑", callback_data="recorded")]]
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(recorded_buttons))
                except Exception:
                    pass
            elif action == 'only_prov_bl':
                await self.blacklist_manager.add_to_blacklist(provider, '用户反馈：仅提供方有问题', user_id, 'manual', is_provisional=True)
                try:
                    await self.blacklist_manager.auto_associate_addresses(payment, provider)
                except Exception:
                    pass
                # 发送确认消息
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text='❌ 已记录：仅能量提供方加入黑名单（临时）。如需撤回，请联系管理员。',
                    reply_to_message_id=query.message.message_id
                )
                # 更新按钮
                try:
                    recorded_buttons = [[InlineKeyboardButton("❌ 提供方已加黑", callback_data="recorded")]]
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(recorded_buttons))
                except Exception:
                    pass
            elif action == 'revoke':
                # 预留：撤回逻辑后续实现（需要记录投票表与时间戳）
                await query.answer('ℹ️ 撤回功能即将上线，暂请联系管理员处理。', show_alert=True)
            elif action == 'cancel' or action == 'cancel_expired':
                # 取消操作：恢复原始按钮
                if payment and provider:
                    original_markup = self._build_inline_keyboard({
                        'address': payment,
                        'energy_provider': provider
                    })
                    try:
                        await query.edit_message_reply_markup(reply_markup=original_markup)
                        await query.answer("已取消，已恢复原始选项")
                    except Exception:
                        await query.answer("操作已取消")
                else:
                    await query.answer("操作已取消")
            elif action in ['recorded_success', 'recorded_fail', 'recorded', 'expired_get_new']:
                # 已记录状态的按钮点击
                if action == 'expired_get_new':
                    await query.answer("请使用 /query 命令获取最新地址信息", show_alert=True)
                else:
                    await query.answer("该操作已记录，如需撤回请联系管理员")
            else:
                await query.answer('操作已完成')
        except Exception as e:
            logger.error(f"处理回调失败: {e}")
            
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
                
                # 为每条地址发送一条带按钮的消息，时间包含在每条消息顶部
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                prefix = f"⏰ 定时推送 - {current_time}\n\n"

                async def send_to(chat_id: int):
                    for addr in addresses:
                        text = prefix + self.format_address_info(addr)
                        markup = self._build_inline_keyboard(addr)
                        try:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=text,
                                parse_mode='Markdown',
                                disable_web_page_preview=True,
                                reply_markup=markup,
                            )
                        except Exception as e:
                            logger.error(f"发送消息到频道 {chat_id} 失败: {e}")
                            # 如果是因为机器人被屏蔽或频道不存在，从活跃列表中移除
                            if "Forbidden" in str(e) or "Bad Request" in str(e):
                                logger.info(f"从活跃频道列表中移除无效频道: {chat_id}")
                                self.active_channels.discard(chat_id)

                if specific_chat_id is not None:
                    await send_to(specific_chat_id)
                    return

                for channel_id in self.active_channels:
                    await send_to(channel_id)
            
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
            
            # 添加黑名单相关命令处理器
            self.application.add_handler(CommandHandler("blacklist_add", self.blacklist_add_command))
            self.application.add_handler(CommandHandler("blacklist_check", self.blacklist_check_command))
            self.application.add_handler(CommandHandler("blacklist_remove", self.blacklist_remove_command))
            self.application.add_handler(CommandHandler("blacklist_stats", self.blacklist_stats_command))

            # 白名单相关命令
            self.application.add_handler(CommandHandler("whitelist_add", self.whitelist_add_command))
            self.application.add_handler(CommandHandler("whitelist_check", self.whitelist_check_command))
            self.application.add_handler(CommandHandler("whitelist_remove", self.whitelist_remove_command))
            self.application.add_handler(CommandHandler("whitelist_stats", self.whitelist_stats_command))

            # 黑名单关联开关
            self.application.add_handler(CommandHandler("assoc", self.assoc_command))
            
            # 查看活跃频道列表
            self.application.add_handler(CommandHandler("channels", self.channels_command))

            # 回调按钮处理
            self.application.add_handler(CallbackQueryHandler(self.inline_button_handler))
            
            # 添加新成员处理器
            self.application.add_handler(MessageHandler(
                filters.StatusUpdate.NEW_CHAT_MEMBERS,
                self.handle_new_chat_members
            ))
            
            # 添加地址检查处理器 - 监听所有文本消息
            self.application.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.address_check_handler
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

    async def address_check_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理用户发送的TRON地址，自动检查黑名单"""
        try:
            message = update.message
            if not message or not message.text:
                return
                
            # 检测消息中的TRON地址
            addresses = self.tron_address_pattern.findall(message.text)
            if not addresses:
                return
                
            # 去重
            unique_addresses = list(set(addresses))
            
            for address in unique_addresses:
                # 检查黑名单
                blacklist_info = await self.blacklist_manager.check_blacklist(address)
                
                if blacklist_info:
                    # 地址在黑名单中，发送警告
                    await self._send_blacklist_warning(message, address, blacklist_info)
                
        except Exception as e:
            logger.error(f"地址检查处理失败: {e}")
            
    async def _send_blacklist_warning(self, message, address: str, blacklist_info: Dict) -> None:
        """发送黑名单警告消息"""
        try:
            # 格式化添加时间
            added_time = blacklist_info['added_at'].strftime("%Y-%m-%d %H:%M:%S") if blacklist_info['added_at'] else "未知"
            
            # 构建警告消息
            warning_message = f"""🔍 **地址查询结果**

📍 **地址**: `{address}`

❌ **黑名单状态**: 已列入黑名单
⚠️ **风险提醒**: 此地址已被用户举报，可能存在白名单限制
📝 **举报原因**: {blacklist_info['reason'] or '未提供原因'}
⏰ **添加时间**: {added_time}
🔖 **添加类型**: {'手动添加' if blacklist_info['type'] == 'manual' else '自动关联'}

💡 **建议**: 直接转TRX可能无法获得能量，请谨慎操作！

如有疑问，请联系管理员。"""

            await message.reply_text(warning_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"发送黑名单警告失败: {e}")
            # 发送简化版本
            simple_warning = f"⚠️ 警告：地址 {address} 已被列入黑名单，可能存在白名单限制！"
            await message.reply_text(simple_warning)

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