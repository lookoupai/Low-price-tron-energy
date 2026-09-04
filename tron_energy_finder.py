import json
import time
from datetime import datetime, timedelta
from tqdm import tqdm
from typing import List, Dict, Optional, Set
import os
from dotenv import load_dotenv
import pathlib
import asyncio
from asyncio import Lock
from cachetools import TTLCache
import aiohttp
import random
import ssl

try:
    import certifi
except ImportError:
    certifi = None

# 配置日志级别
import logging

# 配置日志
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,  # 默认日志级别为 INFO
    handlers=[
        logging.StreamHandler(),  # 只输出到控制台
    ]
)

# 设置第三方库的日志级别为 WARNING，减少不必要的日志
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# 1 TRX 质押可得能量的回退值（全网质押量变化时会偏离，仅在动态拉取失败时使用）
FALLBACK_ENERGY_PER_TRX = 11.3661
# 动态单价缓存时长（秒）
ENERGY_PRICE_TTL = 600
# TRON 全节点接口（用于拉取全网能量参数）
TRON_FULLNODE_API = "https://api.trongrid.io"
# 拉取能量参数时使用的占位地址（接口要求传地址，返回的全网字段与地址无关）
ENERGY_PARAM_PROBE_ADDRESS = "TZ4UXDV5ZhNW7fb2AMSbgfAEZ7hWsnYS2g"
# 单个 API Key 每秒请求上限（TronScan 限制）
MAX_REQUESTS_PER_SECOND = 5
# 单个进程每日请求上限
MAX_DAILY_REQUESTS = 100000

class APIKeyManager:
    def __init__(self, api_keys: List[str]):
        """初始化 API Key 管理器"""
        self.api_keys = api_keys
        self.current_key_index = 0
        self.request_times = {key: [] for key in api_keys}  # 记录每个 key 的请求时间
        self.daily_request_count = 0  # 记录当天的总请求次数
        self.last_reset_time = datetime.now()  # 上次重置计数的时间
        self._lock = asyncio.Lock()

    async def get_next_key(self) -> str:
        """获取下一个可用的 API Key

        用循环代替递归：限流等待必须在释放锁之后进行，
        否则持锁递归会等待自己持有的锁，造成永久死锁。
        """
        while True:
            async with self._lock:
                # 检查是否需要重置每日计数
                now = datetime.now()
                if now.date() > self.last_reset_time.date():
                    self.daily_request_count = 0
                    self.last_reset_time = now

                # 检查是否达到每日限制
                if self.daily_request_count >= MAX_DAILY_REQUESTS:
                    raise Exception("已达到每日 API 请求限制")

                # 清理超过1秒的请求记录
                current_time = time.time()
                for key in self.api_keys:
                    self.request_times[key] = [t for t in self.request_times[key]
                                               if current_time - t < 1]

                # 查找可用的 key
                for _ in range(len(self.api_keys)):
                    key = self.api_keys[self.current_key_index]
                    if len(self.request_times[key]) < MAX_REQUESTS_PER_SECOND:
                        self.request_times[key].append(current_time)
                        self.daily_request_count += 1
                        return key

                    self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)

                # 所有 key 都触发限流，计算最早请求还需多久过期
                active_times = [t for times in self.request_times.values() for t in times]
                wait_time = max(0.01, 1 - (current_time - min(active_times))) if active_times else 0.01

            # 锁已释放，再等待限流窗口过期
            await asyncio.sleep(wait_time)

class TronEnergyFinder:
    def __init__(self):
        """初始化 Tron 能量查找器"""
        # 加载环境变量
        load_dotenv()
        
        # 获取当前目录
        current_dir = os.getcwd()
        env_path = os.path.join(current_dir, '.env')
        
        # 减少初始化时的日志输出
        logger.debug(f"当前目录: {current_dir}")
        logger.debug(f"环境变量文件路径: {env_path}")
        logger.debug(f"环境变量文件是否存在: {os.path.exists(env_path)}")
        
        # 获取 API Keys
        api_keys = []
        i = 1
        while True:
            key = os.getenv(f"TRON_API_KEY_{i}")
            if not key:
                break
            api_keys.append(key)
            logger.debug(f"成功加载 TRON_API_KEY_{i}: {key[:8]}...")  # 改为 DEBUG 级别
            i += 1
        
        if not api_keys:
            raise ValueError("请在.env文件中设置至少一个 TRON_API_KEY")
        
        logger.info(f"成功加载 {len(api_keys)} 个 API Key")  # 保留重要信息为 INFO 级别

        self.api_manager = APIKeyManager(api_keys)
        self.tronscan_api = "https://apilist.tronscan.org/api"
        self.min_trx_amount = float(os.getenv("MIN_TRX_AMOUNT", "0.01"))
        self.max_trx_amount = float(os.getenv("MAX_TRX_AMOUNT", "1"))
        if self.min_trx_amount <= 0 or self.max_trx_amount < self.min_trx_amount:
            raise ValueError("MIN_TRX_AMOUNT / MAX_TRX_AMOUNT 配置无效")
        logger.info(f"TRX 筛选区间: {self.min_trx_amount}-{self.max_trx_amount} TRX")
        
        # 创建results目录
        self.results_dir = pathlib.Path("results")
        self.results_dir.mkdir(exist_ok=True)
        
        # 文件清理配置
        self.cleanup_enabled = True              # 是否启用自动清理
        self.retention_days = 7                  # 保留天数（默认7天）
        self.max_cleanup_files = 100            # 单次最大清理文件数（防止意外删除过多文件）
        self.last_cleanup_date = None           # 记录最后清理的日期，避免同一天重复清理
        
        # 初始化缓存
        self._block_cache = {}  # 区块缓存
        self._analyzed_addresses = set()  # 已分析的地址集合
        self._energy_amount_cache = {}  # 能量数量缓存
        self._transaction_info_cache = {}  # 交易信息缓存
        self._results_cache = TTLCache(maxsize=100, ttl=60)  # 结果缓存60秒
        
        # 添加锁机制
        self._api_lock = Lock()
        self._cache_lock = Lock()
        self._analyze_semaphore = asyncio.Semaphore(3)  # 并发分析上限 3

        # 添加API请求限制
        self._last_api_call = 0
        self._min_api_interval = 0.1  # 最小API调用间隔（秒）
        
        # SSL上下文
        self._ssl_context = self._build_ssl_context()
        
        # 黑名单管理器（延迟初始化）
        self._blacklist_manager = None
        # 白名单管理器（延迟初始化）
        self._whitelist_manager = None
        # 反馈管理器（延迟初始化）
        self._feedback_manager = None

        # 能量单价缓存（动态拉取，失败时回退常量）
        self._energy_per_trx = None
        self._energy_per_trx_at = 0.0
        self._energy_price_lock = Lock()

    def _is_rental_amount(
        self,
        amount: float,
        min_trx: Optional[float] = None,
        max_trx: Optional[float] = None,
    ) -> bool:
        """判断金额是否落在低价租能量区间"""
        lo = self.min_trx_amount if min_trx is None else min_trx
        hi = self.max_trx_amount if max_trx is None else max_trx
        return lo <= amount <= hi

    def _build_ssl_context(self) -> ssl.SSLContext:
        """构建SSL上下文，解决证书验证问题"""
        try:
            if certifi:
                # 使用certifi提供的CA证书包
                logger.debug("使用certifi CA证书包")
                return ssl.create_default_context(cafile=certifi.where())
            else:
                # 使用系统默认的SSL context
                logger.debug("使用系统默认SSL context")
                return ssl.create_default_context()
        except Exception as e:
            logger.warning(f"创建SSL context失败: {e}，使用默认配置")
            return ssl.create_default_context()
        
    async def init_blacklist_manager(self):
        """初始化黑名单管理器"""
        if self._blacklist_manager is None:
            try:
                from blacklist_manager import BlacklistManager
                self._blacklist_manager = BlacklistManager()
                await self._blacklist_manager.init_database()
                logger.info("黑名单管理器初始化成功")
            except Exception as e:
                logger.warning(f"黑名单管理器初始化失败: {e}")
                self._blacklist_manager = None

    async def init_whitelist_manager(self):
        """初始化白名单管理器"""
        if self._whitelist_manager is None:
            try:
                from whitelist_manager import WhitelistManager
                self._whitelist_manager = WhitelistManager()
                await self._whitelist_manager.init_database()
                logger.info("白名单管理器初始化成功")
            except Exception as e:
                logger.warning(f"白名单管理器初始化失败: {e}")
                self._whitelist_manager = None

    async def init_feedback_manager(self):
        """初始化反馈管理器"""
        if self._feedback_manager is None:
            try:
                from feedback_manager import FeedbackManager
                self._feedback_manager = FeedbackManager()
                await self._feedback_manager.init_database()
                logger.info("反馈管理器初始化成功")
            except Exception as e:
                logger.warning(f"反馈管理器初始化失败: {e}")
                self._feedback_manager = None

    async def get_reliability(self, max_count: int, payment_address: str) -> Dict:
        """推导地址靠谱度，并附带该收款地址的有效票数

        max_count 为同一金额在 24h 内的代理笔数。入口筛选已要求 >=5，
        故只区分：>=7 正常使用 / 5-6 笔数偏少（可能有白名单限制）。
        """
        if max_count >= 7:
            status = "正常使用"
        else:
            status = "笔数偏少，可能有白名单限制"

        votes = {"success": 0, "fail": 0}
        await self.init_feedback_manager()
        if self._feedback_manager is not None:
            try:
                votes = await self._feedback_manager.count_votes(payment_address=payment_address)
            except Exception as e:
                logger.warning(f"读取反馈票数失败: {e}")

        return {
            "status": status,
            "proxy_count": max_count,
            "vote_success": votes.get("success", 0),
            "vote_fail": votes.get("fail", 0),
        }


    async def check_and_handle_blacklist(self, payment_address: str, energy_provider: str) -> Dict:
        """综合检查白名单与黑名单，并根据设置进行自动关联。

        白名单优先：
        - 若“收款地址+能量提供方”组合在白名单，则不显示黑名单警告，仅提示白名单成功信息。
        - 若只有其中一方在白名单，则提示"曾有人通过此【收款地址/能量提供方】成功，但不是当前组合"。
        - 其他情况下，展示黑名单信息与自动关联结果（仅保留 提供方→收款地址）。
        """
        result = {
            'payment_blacklisted': False,
            'provider_blacklisted': False,
            'blacklist_warning': '',
            'auto_associated': False,
            'payment_whitelisted': False,
            'provider_whitelisted': False,
            'pair_whitelisted': False,
            'whitelist_notice': ''
        }

        try:
            # 初始化管理器
            if self._blacklist_manager is None:
                await self.init_blacklist_manager()
            if self._whitelist_manager is None:
                await self.init_whitelist_manager()

            # 白名单检查
            pair_info = None
            payment_wl = None
            provider_wl = None
            if self._whitelist_manager is not None:
                pair_info = await self._whitelist_manager.check_pair(payment_address, energy_provider)
                payment_wl = await self._whitelist_manager.check_address(payment_address, 'payment')
                provider_wl = await self._whitelist_manager.check_address(energy_provider, 'provider')

            if pair_info:
                result['pair_whitelisted'] = True
                provisional_tag = '（临时）' if pair_info.get('is_provisional') else ''
                result['whitelist_notice'] = f"✅ 曾有人成功获得能量租凭，因此已加入白名单{provisional_tag}。"
                # 组合白名单优先，直接返回，不展示黑名单
                return result

            if payment_wl:
                result['payment_whitelisted'] = True
                provisional_tag = '（临时）' if payment_wl.get('is_provisional') else ''
                result['whitelist_notice'] += f"ℹ️ 曾有人通过此收款地址收到能量租凭，但不是这个能量提供方{provisional_tag}。\n"
            if provider_wl:
                result['provider_whitelisted'] = True
                provisional_tag = '（临时）' if provider_wl.get('is_provisional') else ''
                result['whitelist_notice'] += f"ℹ️ 曾有人通过此能量提供方收到能量租凭，但不是这个收款地址{provisional_tag}。\n"

            # 黑名单检查与关联（若无组合白名单）
            if self._blacklist_manager is None:
                return result

            payment_info = await self._blacklist_manager.check_blacklist(payment_address)
            if payment_info:
                result['payment_blacklisted'] = True
                provisional_tag = '（临时）' if payment_info.get('is_provisional') else ''
                result['blacklist_warning'] += f"⚠️ 收款地址已列入黑名单{provisional_tag}: {payment_info.get('reason', '未提供原因')}\n"

            provider_info = await self._blacklist_manager.check_blacklist(energy_provider)
            if provider_info:
                result['provider_blacklisted'] = True
                provisional_tag = '（临时）' if provider_info.get('is_provisional') else ''
                result['blacklist_warning'] += f"⚠️ 能量提供方已列入黑名单{provisional_tag}: {provider_info.get('reason', '未提供原因')}\n"

            # 自动关联：仅当任一方在黑名单时尝试，内部仅传播 提供方->收款地址
            if result['payment_blacklisted'] or result['provider_blacklisted']:
                success = await self._blacklist_manager.auto_associate_addresses(payment_address, energy_provider)
                if success:
                    result['auto_associated'] = True
                    result['blacklist_warning'] += "🔗 已自动关联相关地址到黑名单\n"
                result['blacklist_warning'] += "💡 此地址已被提交黑名单，有白名单限制，直接转TRX可能无法获得能量！"

        except Exception as e:
            logger.error(f"名单检查失败: {e}")

        return result
        
    def _get_result_file(self) -> pathlib.Path:
        """获取当天的结果文件路径"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.results_dir / f"energy_addresses_{today}.json"
        
    def _get_file_date_from_name(self, filename: str) -> Optional[datetime]:
        """从文件名中解析日期"""
        try:
            # 解析格式：energy_addresses_YYYY-MM-DD.json
            if not filename.startswith("energy_addresses_") or not filename.endswith(".json"):
                return None
            
            # 提取日期部分
            date_part = filename[len("energy_addresses_"):-len(".json")]
            
            # 验证日期格式 YYYY-MM-DD
            if len(date_part) != 10 or date_part[4] != '-' or date_part[7] != '-':
                return None
                
            # 解析日期
            return datetime.strptime(date_part, "%Y-%m-%d")
            
        except (ValueError, IndexError) as e:
            logger.debug(f"无法解析文件名中的日期 '{filename}': {e}")
            return None
            
    async def _cleanup_old_files(self) -> int:
        """清理过期的结果文件"""
        if not self.cleanup_enabled:
            return 0
            
        try:
            # 检查是否今天已经清理过
            today = datetime.now().date()
            if self.last_cleanup_date == today:
                logger.debug(f"今天 ({today}) 已经执行过清理，跳过重复清理")
                return 0
            
            # 计算截止日期
            cutoff_date = datetime.now() - timedelta(days=self.retention_days)
            logger.debug(f"开始清理 {cutoff_date.strftime('%Y-%m-%d')} 之前的结果文件")
            
            # 扫描results目录
            if not self.results_dir.exists():
                return 0
                
            files_to_delete = []
            
            # 遍历目录中的所有文件
            for file_path in self.results_dir.iterdir():
                if not file_path.is_file():
                    continue
                    
                filename = file_path.name
                file_date = self._get_file_date_from_name(filename)
                
                # 如果无法解析日期或文件不是结果文件格式，跳过
                if file_date is None:
                    continue
                    
                # 检查是否过期
                if file_date < cutoff_date:
                    files_to_delete.append(file_path)
                    
                # 防止单次删除过多文件
                if len(files_to_delete) >= self.max_cleanup_files:
                    logger.warning(f"达到单次清理文件数限制 ({self.max_cleanup_files})，停止扫描")
                    break
            
            # 执行删除操作
            deleted_count = 0
            for file_path in files_to_delete:
                try:
                    file_path.unlink()  # 删除文件
                    deleted_count += 1
                    logger.debug(f"已删除过期文件: {file_path.name}")
                except OSError as e:
                    logger.error(f"删除文件失败 {file_path.name}: {e}")
                    
            if deleted_count > 0:
                logger.info(f"清理完成：删除了 {deleted_count} 个过期的结果文件（保留最近 {self.retention_days} 天）")
            else:
                logger.debug("没有找到需要清理的过期文件")
            
            # 更新最后清理日期
            self.last_cleanup_date = today
            logger.debug(f"更新最后清理日期为: {today}")
                
            return deleted_count
            
        except Exception as e:
            logger.error(f"文件清理过程中发生错误: {e}")
            return 0
            
    async def cleanup_results_manually(self, retention_days: Optional[int] = None) -> int:
        """手动清理结果文件
        
        Args:
            retention_days: 保留天数，如果为None则使用默认配置
            
        Returns:
            int: 清理的文件数量
        """
        # 临时保存原始配置
        original_enabled = self.cleanup_enabled
        original_retention_days = self.retention_days
        
        try:
            # 启用清理并设置保留天数
            self.cleanup_enabled = True
            if retention_days is not None:
                self.retention_days = retention_days
                
            logger.info(f"开始手动清理结果文件（保留最近 {self.retention_days} 天）")
            cleaned_count = await self._cleanup_old_files()
            
            if cleaned_count > 0:
                logger.info(f"手动清理完成：删除了 {cleaned_count} 个过期文件")
            else:
                logger.info("手动清理完成：没有找到需要清理的文件")
                
            return cleaned_count
            
        finally:
            # 恢复原始配置
            self.cleanup_enabled = original_enabled
            self.retention_days = original_retention_days
        
    def _load_existing_results(self) -> Dict:
        """加载已有的结果"""
        result_file = self._get_result_file()
        if result_file.exists():
            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                print(f"警告: 结果文件 {result_file} 格式错误，将创建新文件")
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "records": []
        }
        
    async def _wait_for_api_limit(self):
        """等待API限制"""
        current_time = time.time()
        if current_time - self._last_api_call < self._min_api_interval:
            await asyncio.sleep(self._min_api_interval)
        self._last_api_call = current_time
        
    async def _make_request(self, url: str, params: Dict = None, timeout: int = 30) -> Optional[Dict]:
        """发送 API 请求，带超时控制"""
        try:
            # 获取下一个可用的 API Key
            api_key = await self.api_manager.get_next_key()

            headers = {
                "TRON-PRO-API-KEY": api_key,
                "Accept": "application/json"
            }

            connector = aiohttp.TCPConnector(ssl=self._ssl_context)
            timeout_obj = aiohttp.ClientTimeout(total=timeout)

            async with aiohttp.ClientSession(connector=connector, timeout=timeout_obj) as session:
                async with session.get(url, params=params, headers=headers) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"API请求失败: {response.status} - {await response.text()}")
                        return None

        except asyncio.TimeoutError:
            logger.warning(f"API请求超时 ({timeout}s): {url}")
            return None
        except Exception as e:
            logger.error(f"请求失败: {e}")
            return None

    async def get_latest_block(self) -> Optional[int]:
        """获取最新区块号"""
        try:
            response = await self._make_request(f"{self.tronscan_api}/block", {
                "sort": "-number",
                "limit": "1",
                "count": "true"
            })
            if response and "data" in response and response["data"]:
                return response["data"][0]["number"]
            return None
        except Exception as e:
            logger.error(f"获取最新区块失败: {e}")
            return None

    async def get_transaction_info(self, tx_hash: str) -> Dict:
        """获取交易详细信息（带缓存）"""
        if tx_hash in self._transaction_info_cache:
            return self._transaction_info_cache[tx_hash]
            
        try:
            response = await self._make_request(f"{self.tronscan_api}/transaction-info", {
                "hash": tx_hash
            })
            if response:
                self._transaction_info_cache[tx_hash] = response
            return response or {}
        except Exception as e:
            logger.error(f"获取交易详情失败: {e}")
            return {}

    async def get_energy_per_trx(self) -> float:
        """获取 1 TRX 质押可得的能量数（动态拉取，进程内缓存）

        全网单价 = TotalEnergyLimit / TotalEnergyWeight，随全网质押量浮动。
        拉取失败时回退到 FALLBACK_ENERGY_PER_TRX 并在日志标注。
        """
        now = time.time()
        if self._energy_per_trx is not None and now - self._energy_per_trx_at < ENERGY_PRICE_TTL:
            return self._energy_per_trx

        async with self._energy_price_lock:
            # 双重检查：等锁期间可能已被其他协程刷新
            now = time.time()
            if self._energy_per_trx is not None and now - self._energy_per_trx_at < ENERGY_PRICE_TTL:
                return self._energy_per_trx

            price = None
            try:
                connector = aiohttp.TCPConnector(ssl=self._ssl_context)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.post(
                        f"{TRON_FULLNODE_API}/wallet/getaccountresource",
                        json={"address": ENERGY_PARAM_PROBE_ADDRESS, "visible": True},
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            limit = data.get("TotalEnergyLimit")
                            weight = data.get("TotalEnergyWeight")
                            if limit and weight:
                                price = float(limit) / float(weight)
                        else:
                            logger.warning(f"拉取能量单价失败: HTTP {response.status}")
            except Exception as e:
                logger.warning(f"拉取能量单价异常: {e}")

            if price is None or price <= 0:
                logger.warning(
                    f"能量单价动态拉取失败，回退常量 {FALLBACK_ENERGY_PER_TRX}（换算结果仅供参考）"
                )
                price = FALLBACK_ENERGY_PER_TRX
            else:
                logger.info(f"能量单价已更新: 1 TRX ≈ {price:.4f} 能量")

            self._energy_per_trx = price
            self._energy_per_trx_at = time.time()
            return price

    async def get_energy_amount(self, tx_hash: str) -> Optional[float]:
        """获取交易中的实际能量数量（带缓存）"""
        if tx_hash in self._energy_amount_cache:
            return self._energy_amount_cache[tx_hash]
            
        tx_info = await self.get_transaction_info(tx_hash)
        if tx_info and "contractData" in tx_info:
            contract_data = tx_info["contractData"]
            energy_amount = None
            
            # 优先使用 resourceValue 字段
            if "resourceValue" in contract_data:
                energy_amount = float(contract_data["resourceValue"])
            # 如果没有 resourceValue，则使用 balance 计算
            elif "balance" in contract_data:
                staked_trx = float(contract_data["balance"]) / 1_000_000
                energy_amount = staked_trx * await self.get_energy_per_trx()
                
            if energy_amount is not None:
                self._energy_amount_cache[tx_hash] = energy_amount
                return energy_amount
                
        return None

    async def analyze_address(
        self,
        address: str,
        min_trx: Optional[float] = None,
        max_trx: Optional[float] = None,
        analyzed: Optional[Set[str]] = None,
    ) -> Optional[Dict]:
        """分析地址的交易记录（受信号量限制的并发执行）"""
        async with self._analyze_semaphore:
            analyzed_set = analyzed if analyzed is not None else self._analyzed_addresses
            if address in analyzed_set:
                return None
            analyzed_set.add(address)

            try:
                logger.debug(f"分析地址: {address}")

                # 获取地址的最近交易记录
                response = await self._make_request(f"{self.tronscan_api}/transaction", {
                    "address": address,
                    "limit": 50,
                    "sort": "-timestamp"
                })

                if not response or "data" not in response:
                    return None

                transactions = response["data"]

                # 先找到代理资源交易
                for i, tx in enumerate(transactions):
                    if tx.get("contractType") == 57:
                        contract_data = tx.get("contractData", {})
                        if contract_data.get("resource") == "ENERGY":
                            proxy_time = tx.get("timestamp", 0)
                            energy_provider = contract_data.get("owner_address")

                            # 向后查找是否有对应的TRX转账
                            for j in range(i + 1, len(transactions)):
                                prev_tx = transactions[j]
                                if (prev_tx.get("contractType") == 1 and
                                    prev_tx.get("timestamp", 0) < proxy_time):
                                    try:
                                        amount = float(prev_tx.get("amount", 0)) / 1_000_000
                                        amount = round(amount, 4)
                                        if self._is_rental_amount(amount, min_trx, max_trx):
                                            trx_receiver = prev_tx.get("toAddress")

                                            # 获取收款地址的最近交易记录
                                            receiver_response = await self._make_request(
                                                f"{self.tronscan_api}/transaction",
                                                {
                                                    "address": trx_receiver,
                                                    "limit": 50,
                                                    "sort": "-timestamp"
                                                }
                                            )

                                            if not receiver_response or "data" not in receiver_response:
                                                continue

                                            receiver_txs = receiver_response["data"]
                                            current_time = int(time.time() * 1000)
                                            amount_count = {}
                                            total_count = 0

                                            # 分析收款地址的最近交易
                                            for rtx in receiver_txs:
                                                tx_time = rtx.get("timestamp", 0)
                                                if current_time - tx_time > 24 * 60 * 60 * 1000:
                                                    continue

                                                if rtx.get("contractType") == 1:
                                                    try:
                                                        rtx_amount = float(rtx.get("amount", 0)) / 1_000_000
                                                        rtx_amount = round(rtx_amount, 4)
                                                        if self._is_rental_amount(rtx_amount, min_trx, max_trx):
                                                            amount_count[rtx_amount] = amount_count.get(rtx_amount, 0) + 1
                                                            total_count += 1
                                                    except (ValueError, TypeError):
                                                        continue

                                            # 检查交易数量
                                            max_count = max(amount_count.values()) if amount_count else 0
                                            max_amount = None
                                            for amt, cnt in amount_count.items():
                                                if cnt == max_count:
                                                    max_amount = amt
                                                    break

                                            # 只在找到符合条件的交易时输出日志
                                            if max_count >= 5 and total_count >= 20:
                                                logger.info(f"找到符合条件的地址: {trx_receiver}")
                                                energy_amount = await self.get_energy_amount(tx.get("hash"))

                                                if energy_amount is None:
                                                    staked_trx = float(contract_data.get("balance", 0)) / 1_000_000
                                                    energy_amount = staked_trx * await self.get_energy_per_trx()
                                                    energy_source = "计算值"
                                                else:
                                                    energy_source = "API值"

                                                # 执行黑名单检查
                                                blacklist_result = await self.check_and_handle_blacklist(trx_receiver, energy_provider)

                                                # 推导靠谱度并读取票数
                                                reliability = await self.get_reliability(max_count, trx_receiver)

                                                # 构建基础结果
                                                result = {
                                                    "address": trx_receiver,
                                                    "energy_provider": energy_provider,
                                                    "purchase_amount": max_amount,
                                                    "energy_quantity": f"{energy_amount:,.2f} 能量",
                                                    "energy_source": energy_source,
                                                    "tx_hash": prev_tx.get("hash"),
                                                    "proxy_tx_hash": tx.get("hash"),
                                                    "recent_tx_count": total_count,
                                                    "recent_tx_amount": max_amount,
                                                    "status": reliability["status"],
                                                    "proxy_count": reliability["proxy_count"],
                                                    "vote_success": reliability["vote_success"],
                                                    "vote_fail": reliability["vote_fail"]
                                                }

                                                # 添加黑名单和白名单相关信息
                                                result.update({
                                                    "payment_blacklisted": blacklist_result['payment_blacklisted'],
                                                    "provider_blacklisted": blacklist_result['provider_blacklisted'],
                                                    "blacklist_warning": blacklist_result['blacklist_warning'],
                                                    "auto_associated": blacklist_result['auto_associated'],
                                                    "payment_whitelisted": blacklist_result['payment_whitelisted'],
                                                    "provider_whitelisted": blacklist_result['provider_whitelisted'],
                                                    "pair_whitelisted": blacklist_result['pair_whitelisted'],
                                                    "whitelist_notice": blacklist_result['whitelist_notice']
                                                })

                                                return result
                                    except (ValueError, TypeError):
                                        continue

                return None

            except Exception as e:
                logger.error(f"分析地址时出错: {e}")
                return None

    async def _save_results(self, addresses: List[Dict]):
        """保存结果到文件"""
        if not addresses:
            return
            
        try:
            # 加载当天的结果文件
            results = self._load_existing_results()
            
            # 获取已存在的代理哈希集合
            existing_proxy_hashes = {record["proxy_tx_hash"] for record in results["records"]}
            
            # 添加新记录
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            new_records = []
            for addr in addresses:
                if addr["proxy_tx_hash"] not in existing_proxy_hashes:
                    addr["found_time"] = current_time
                    new_records.append(addr)
                    existing_proxy_hashes.add(addr["proxy_tx_hash"])
            
            if new_records:
                # 将新记录放在最前面
                results["records"] = new_records + results["records"]
                
                # 保存到文件
                result_file = self._get_result_file()
                with open(result_file, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                
                logger.info(f"已保存 {len(new_records)} 个新记录到文件: {result_file}")
            else:
                logger.info("没有新的记录需要保存")
            
            # 执行自动清理（如果启用）
            if self.cleanup_enabled:
                try:
                    cleaned_count = await self._cleanup_old_files()
                    if cleaned_count > 0:
                        logger.info(f"自动清理了 {cleaned_count} 个过期结果文件")
                except Exception as e:
                    logger.error(f"自动清理失败: {e}")
                
        except Exception as e:
            logger.error(f"保存结果时出错: {e}")

    async def _print_results(self, addresses):
        """格式化输出结果"""
        if not addresses:
            logger.warning("未找到符合条件的低价能量地址")
            return
            
        result_text = "\n🎉 找到以下低价能量地址：\n\n"
        for addr in addresses:
            # 如果是计算值，添加提示信息
            energy_display = addr['energy_quantity']
            if addr['energy_source'] == "计算值":
                energy_display = f"{energy_display} (计算值，仅供参考)"
                
            result_text += f"""🔹 【收款地址】: {addr['address']}
🔹 【能量提供方】: {addr['energy_provider']}
🔹 【购买记录】: https://tronscan.org/#/address/{addr['address']}
🔹 【收款金额】: {addr['purchase_amount']} TRX
🔹 【能量数量】: {energy_display}
🔹 【24h交易数】: {addr['recent_tx_count']} 笔
🔹 【转账哈希】: {addr['tx_hash']}
🔹 【代理哈希】: {addr['proxy_tx_hash']}

【地址信息】{addr['status']}
"""
        logger.info(result_text)

    async def get_block_transactions(self, block_number: int) -> Dict[str, List[Dict]]:
        """获取区块交易详情，返回 Type-1 转账和 Type-57 代理交易"""
        try:
            cache_key = f"block_{block_number}"

            # 检查缓存
            if cache_key in self._block_cache:
                logger.debug(f"使用缓存的区块 {block_number} 交易数据")
                return self._block_cache[cache_key]

            # 使用 TronScan API 获取交易信息
            response = await self._make_request(f"{self.tronscan_api}/transaction", {
                "block": str(block_number),
                "limit": "1",
                "start": "0",
                "count": "true"
            })

            if not response:
                return {"payments": [], "proxies": []}

            total_transactions = response.get("total", 0)
            logger.info(f"正在检查区块 {block_number}，总交易数: {total_transactions}")

            # 分批获取所有交易
            all_transactions = []
            start = 0
            limit = 200
            max_retries = 3
            retries = 0

            while start < total_transactions:
                await asyncio.sleep(0.5)

                response = await self._make_request(f"{self.tronscan_api}/transaction", {
                    "block": str(block_number),
                    "limit": str(limit),
                    "start": str(start),
                    "count": "true"
                })

                if not response or "data" not in response:
                    retries += 1
                    logger.warning(
                        f"获取区块 {block_number} 交易失败，重试 {retries}/{max_retries}..."
                    )
                    if retries >= max_retries:
                        break
                    await asyncio.sleep(1)
                    continue

                retries = 0
                transactions = response.get("data", [])
                if not transactions:
                    break

                all_transactions.extend(transactions)
                start += len(transactions)
                logger.info(f"已获取 {len(all_transactions)}/{total_transactions} 条交易记录")

            # 分类交易：Type-1 转账和 Type-57 代理
            energy_per_trx = await self.get_energy_per_trx()
            payment_transactions = []
            proxy_transactions = []

            for tx in all_transactions:
                contract_type = tx.get("contractType")
                contract_data = tx.get("contractData", {})

                # Type-1: TRX 转账
                if contract_type == 1 and "amount" in tx:
                    payment_transactions.append(tx)
                # Type-57: 代理资源交易
                elif contract_type == 57:
                    if (contract_data.get("resource") == "ENERGY" and
                        "balance" in contract_data and
                        "receiver_address" in contract_data and
                        "owner_address" in contract_data):

                        proxy_transactions.append(tx)
                        logger.info(f"找到代理资源交易:\n"
                                  f"交易哈希: {tx.get('hash')}\n"
                                  f"发送人: {contract_data.get('owner_address')}\n"
                                  f"接收人: {contract_data.get('receiver_address')}\n"
                                  f"代理数量: {contract_data.get('balance', 0) / 1_000_000 * energy_per_trx:,.2f} 能量")

            result = {"payments": payment_transactions, "proxies": proxy_transactions}
            logger.info(
                f"区块 {block_number} 找到 {len(payment_transactions)} 笔转账、"
                f"{len(proxy_transactions)} 笔代理资源交易"
            )

            # 缓存结果
            if proxy_transactions or payment_transactions:
                self._block_cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"获取区块交易详情失败: {e}")
            return {"payments": [], "proxies": []}

    def _pair_transactions_in_block(
        self,
        payments: List[Dict],
        proxies: List[Dict],
        min_trx: float,
        max_trx: float,
    ) -> Dict[str, Dict]:
        """块内预配对：找到接收方地址在块内同时有付款和代理记录的情况

        返回 {receiver_address: {"payment": tx, "proxy": tx, "amount": float}}
        """
        paired = {}

        for proxy in proxies:
            contract_data = proxy.get("contractData", {})
            receiver = contract_data.get("receiver_address")
            if not receiver:
                continue

            proxy_time = proxy.get("timestamp", 0)

            # 在付款交易中查找：同一接收方且时间早于代理
            for payment in payments:
                if payment.get("toAddress") != receiver:
                    continue
                payment_time = payment.get("timestamp", 0)
                if payment_time >= proxy_time:
                    continue

                try:
                    amount = round(float(payment.get("amount", 0)) / 1_000_000, 4)
                    if min_trx <= amount <= max_trx:
                        # 同一接收方可能有多笔，保留时间最接近的
                        if receiver not in paired or payment_time > paired[receiver]["payment"].get("timestamp", 0):
                            paired[receiver] = {
                                "payment": payment,
                                "proxy": proxy,
                                "amount": amount,
                            }
                except (ValueError, TypeError):
                    continue

        return paired

    async def find_low_cost_energy_addresses(
        self,
        min_trx: Optional[float] = None,
        max_trx: Optional[float] = None,
        max_results: int = 3,
        max_blocks: Optional[int] = None,
    ) -> List[Dict]:
        """查找低成本能量代理地址（带缓存和并发控制）"""
        lo = self.min_trx_amount if min_trx is None else round(float(min_trx), 4)
        hi = self.max_trx_amount if max_trx is None else round(float(max_trx), 4)
        if lo <= 0 or hi < lo:
            raise ValueError("MIN_TRX_AMOUNT / MAX_TRX_AMOUNT 配置无效")
        if max_results <= 0:
            max_results = 3
        if max_blocks is None:
            max_blocks = 15 if lo == hi else 8

        cache_key = f"{lo:.4f}:{hi:.4f}:{max_results}"
        if cache_key in self._results_cache:
            logger.info(f"使用缓存的结果: {cache_key}")
            return self._results_cache[cache_key]

        try:
            latest_block = await self.get_latest_block()
            if not latest_block:
                logger.error("获取最新区块失败")
                return []

            logger.info(
                f"最新区块号: {latest_block}，筛选 {lo}-{hi} TRX，最多 {max_results} 条，扫描 {max_blocks} 块"
            )

            found_addresses: List[Dict] = []
            seen_payments: Set[str] = set()
            analyzed: Set[str] = set()
            current_block = latest_block
            blocks_checked = 0

            async with self._cache_lock:
                self._energy_amount_cache.clear()
                self._transaction_info_cache.clear()

            while blocks_checked < max_blocks and len(found_addresses) < max_results:
                logger.info(f"正在检查区块 {current_block}...")

                block_data = await self.get_block_transactions(current_block)
                payments = block_data.get("payments", [])
                proxies = block_data.get("proxies", [])

                if not proxies:
                    logger.warning(f"区块 {current_block} 没有代理资源交易")
                    current_block -= 1
                    blocks_checked += 1
                    continue

                logger.info(
                    f"区块 {current_block} 有 {len(payments)} 笔转账、{len(proxies)} 笔代理资源交易"
                )

                # 块内预配对
                paired = self._pair_transactions_in_block(payments, proxies, lo, hi)
                logger.info(f"块内预配对找到 {len(paired)} 个候选接收方")

                # 优先分析配对成功的接收方，如果不够则分析所有代理接收方
                candidates = set(paired.keys())
                if len(candidates) < len(proxies):
                    for proxy in proxies:
                        contract_data = proxy.get("contractData", {})
                        receiver = contract_data.get("receiver_address")
                        if receiver:
                            candidates.add(receiver)

                logger.info(f"总候选接收方: {len(candidates)} 个（{len(paired)} 个块内配对 + {len(candidates) - len(paired)} 个未配对）")

                # 并发分析候选接收方
                tasks = []
                for receiver in candidates:
                    if len(found_addresses) >= max_results:
                        break
                    task = self.analyze_address(
                        receiver,
                        min_trx=lo,
                        max_trx=hi,
                        analyzed=analyzed,
                    )
                    tasks.append(task)

                # 等待所有分析任务完成
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 处理结果
                for address_info in results:
                    if len(found_addresses) >= max_results:
                        break
                    if isinstance(address_info, Exception):
                        logger.error(f"分析地址时发生异常: {address_info}")
                        continue
                    if not address_info:
                        continue

                    payment = address_info.get("address")
                    if not payment or payment in seen_payments:
                        continue

                    seen_payments.add(payment)
                    found_addresses.append(address_info)
                    logger.info(
                        f"已收集 {len(found_addresses)}/{max_results} 个地址: {payment}"
                    )

                logger.info(
                    f"区块 {current_block} 检查完成，找到 {len(proxies)} 笔代理资源交易"
                )
                current_block -= 1
                blocks_checked += 1

            self._results_cache[cache_key] = found_addresses
            if found_addresses:
                await self._save_results(found_addresses)
                await self._print_results(found_addresses)
            else:
                logger.warning(
                    f"检查了 {blocks_checked} 个区块后仍未找到符合 {lo}-{hi} TRX 的地址"
                )
            return found_addresses

        except Exception as e:
            logger.error(f"查找低成本能量代理地址时发生错误: {e}")
            return []

async def main():
    """主函数"""
    try:
        finder = TronEnergyFinder()
        await finder.find_low_cost_energy_addresses()
        
    except Exception as e:
        logger.error(f"运行出错: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main()) 