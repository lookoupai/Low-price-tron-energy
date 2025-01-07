import json
import time
from datetime import datetime
from tqdm import tqdm
from typing import List, Dict, Optional, Set
import os
from dotenv import load_dotenv
import pathlib
import asyncio
from asyncio import Lock
from cachetools import TTLCache
import aiohttp

# 配置日志级别
import logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,  # 改为 INFO 级别以显示更多信息
    handlers=[
        logging.StreamHandler(),  # 输出到控制台
        logging.FileHandler('tron_energy_finder.log')  # 同时保存到文件
    ]
)
logger = logging.getLogger(__name__)

class TronEnergyFinder:
    def __init__(self):
        # 获取当前文件所在目录
        current_dir = pathlib.Path(__file__).parent.absolute()
        env_path = current_dir / '.env'
        
        logger.info(f"当前目录: {current_dir}")
        logger.info(f"环境变量文件路径: {env_path}")
        logger.info(f"环境变量文件是否存在: {env_path.exists()}")
        
        # 加载环境变量
        load_dotenv(dotenv_path=env_path)
        
        # 检查并记录 API Key 状态
        api_key = os.getenv("TRON_API_KEY")
        if not api_key:
            logger.warning("未设置TRON_API_KEY环境变量，TronScan API访问可能受限")
            logger.warning("请在.env文件中设置TRON_API_KEY=你的TronScan API密钥")
        else:
            logger.info(f"成功加载 TRON_API_KEY: {api_key[:8]}...")
        
        self.tronscan_api = "https://apilist.tronscan.org/api"
        
        # TronScan API Key
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "TRON-PRO-API-KEY": api_key or ""
        }
        
        self.retry_count = 3
        self.retry_delay = 2
        
        # 创建results目录
        self.results_dir = pathlib.Path("results")
        self.results_dir.mkdir(exist_ok=True)
        
        # 初始化缓存
        self._analyzed_addresses: Set[str] = set()
        self._energy_amount_cache: Dict[str, float] = {}
        self._transaction_info_cache: Dict[str, Dict] = {}
        
        # 添加锁机制
        self._api_lock = Lock()
        self._cache_lock = Lock()
        
        # 添加缓存
        self._results_cache = TTLCache(maxsize=100, ttl=60)  # 结果缓存60秒
        self._block_cache = TTLCache(maxsize=10, ttl=30)     # 区块缓存30秒
        self._tx_cache = TTLCache(maxsize=1000, ttl=300)     # 交易缓存5分钟
        
        # 添加API请求限制
        self._last_api_call = 0
        self._min_api_interval = 0.1  # 最小API调用间隔（秒）
        
    def _get_result_file(self) -> pathlib.Path:
        """获取当天的结果文件路径"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.results_dir / f"energy_addresses_{today}.json"
        
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
        
    async def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        """带限制和重试机制的异步请求方法"""
        async with self._api_lock:
            await self._wait_for_api_limit()
            
            for attempt in range(self.retry_count):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(url, params=params, headers=self.headers) as response:
                            response.raise_for_status()
                            return await response.json()
                except Exception as e:
                    if attempt == self.retry_count - 1:
                        logger.error(f"请求失败 ({url}): {e}")
                        return None
                    logger.warning(f"请求失败，{self.retry_delay}秒后重试: {e}")
                    await asyncio.sleep(self.retry_delay)
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
                energy_amount = staked_trx * 11.3661
                
            if energy_amount is not None:
                self._energy_amount_cache[tx_hash] = energy_amount
                return energy_amount
                
        return None

    async def analyze_address(self, address: str) -> Optional[Dict]:
        """分析地址的交易记录"""
        # 检查是否已分析过
        if address in self._analyzed_addresses:
            return None
            
        self._analyzed_addresses.add(address)
        
        try:
            logger.info(f"分析地址: {address}")
            
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
                                    if 0.1 <= amount <= 1:
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
                                                    if 0.1 <= rtx_amount <= 1:
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
                                            logger.info(f"地址 {trx_receiver} 24小时内总交易: {total_count}笔，最多重复金额: {max_count}笔")
                                            energy_amount = await self.get_energy_amount(tx.get("hash"))
                                            
                                            if energy_amount is None:
                                                staked_trx = float(contract_data.get("balance", 0)) / 1_000_000
                                                energy_amount = staked_trx * 11.3661
                                                energy_source = "计算值"
                                            else:
                                                energy_source = "API值"
                                                
                                            logger.info(f"找到符合条件的地址: {trx_receiver}")
                                            
                                            return {
                                                "address": trx_receiver,
                                                "energy_provider": energy_provider,
                                                "purchase_amount": max_amount,
                                                "energy_quantity": f"{energy_amount:,.2f} 能量",
                                                "energy_source": energy_source,
                                                "tx_hash": prev_tx.get("hash"),
                                                "proxy_tx_hash": tx.get("hash"),
                                                "recent_tx_count": total_count,
                                                "recent_tx_amount": max_amount,
                                                "status": "正常使用"
                                            }
                                except (ValueError, TypeError):
                                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"分析地址 {address} 时出错: {e}")
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

    async def get_block_transactions(self, block_number: int) -> List[Dict]:
        """获取区块交易详情"""
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
                return []
                
            total_transactions = response.get("total", 0)
            logger.info(f"正在检查区块 {block_number}，总交易数: {total_transactions}")
            
            # 分批获取所有交易
            all_transactions = []
            start = 0
            limit = 200  # 每次获取200条
            
            while start < total_transactions:
                response = await self._make_request(f"{self.tronscan_api}/transaction", {
                    "block": str(block_number),
                    "limit": str(limit),
                    "start": str(start),
                    "count": "true"
                })
                
                if not response or "data" not in response:
                    break
                    
                transactions = response.get("data", [])
                if not transactions:
                    break
                    
                all_transactions.extend(transactions)
                start += len(transactions)
                logger.debug(f"已获取 {len(all_transactions)}/{total_transactions} 条交易记录")
                await asyncio.sleep(0.5)  # 避免请求过快
            
            # 筛选代理资源交易
            proxy_transactions = []
            for tx in all_transactions:
                # 检查合约类型和描述
                contract_type = tx.get("contractType")
                contract_data = tx.get("contractData", {})
                
                # 只检查代理资源交易 (Type 57)
                if contract_type == 57:
                    # 检查是否是能量代理
                    if (contract_data.get("resource") == "ENERGY" and 
                        "balance" in contract_data and 
                        "receiver_address" in contract_data and 
                        "owner_address" in contract_data):
                        
                        proxy_transactions.append(tx)
            
            if proxy_transactions:
                logger.info(f"区块 {block_number} 找到 {len(proxy_transactions)} 笔代理资源交易")
                # 缓存结果
                self._block_cache[cache_key] = proxy_transactions
            else:
                logger.debug(f"区块 {block_number} 未找到代理资源交易记录")
                
            return proxy_transactions
            
        except Exception as e:
            logger.error(f"获取区块交易详情失败: {e}")
            return []

    async def find_low_cost_energy_addresses(self):
        """查找低成本能量代理地址（带缓存和并发控制）"""
        cache_key = "latest_results"
        
        # 检查缓存
        if cache_key in self._results_cache:
            logger.info("使用缓存的结果")
            return self._results_cache[cache_key]
            
        try:
            # 获取最新区块
            latest_block = await self.get_latest_block()
            if not latest_block:
                logger.error("获取最新区块失败")
                return []
                
            logger.info(f"最新区块号: {latest_block}")
            
            # 初始化结果列表和计数器
            found_addresses = []
            current_block = latest_block
            max_blocks_to_check = 3  # 最多检查3个区块
            blocks_checked = 0
            
            # 清空缓存
            async with self._cache_lock:
                self._analyzed_addresses.clear()
                self._energy_amount_cache.clear()
                self._transaction_info_cache.clear()
            
            while blocks_checked < max_blocks_to_check:
                logger.info(f"正在检查区块 {current_block}...")
                
                transactions = await self.get_block_transactions(current_block)
                if not transactions:
                    logger.warning(f"区块 {current_block} 没有交易")
                    current_block -= 1
                    blocks_checked += 1
                    continue
                    
                logger.info(f"区块 {current_block} 有 {len(transactions)} 笔交易")
                proxy_count = 0
                
                # 分析每个代理交易
                for tx in transactions:
                    contract_data = tx.get("contractData", {})
                    if (tx.get("contractType") == 57 and 
                        contract_data.get("resource") == "ENERGY"):
                        
                        proxy_count += 1
                        logger.info(f"找到代理资源交易:\n"
                                  f"交易哈希: {tx.get('hash')}\n"
                                  f"发送人: {contract_data.get('owner_address')}\n"
                                  f"接收人: {contract_data.get('receiver_address')}\n"
                                  f"代理数量: {contract_data.get('balance', 0) / 1_000_000 * 11.3661:,.2f} 能量")
                        
                        receiver_address = contract_data.get("receiver_address")
                        if receiver_address:
                            address_info = await self.analyze_address(receiver_address)
                            if address_info:
                                # 找到符合条件的地址，保存到缓存并返回
                                found_addresses.append(address_info)
                                self._results_cache[cache_key] = found_addresses
                                await self._save_results(found_addresses)
                                await self._print_results(found_addresses)
                                logger.info("✅ 已找到符合条件的地址，停止查找")
                                return found_addresses
                
                logger.info(f"区块 {current_block} 检查完成，找到 {proxy_count} 笔代理资源交易")
                current_block -= 1
                blocks_checked += 1
                
            if not found_addresses:
                logger.warning(f"检查了 {blocks_checked} 个区块后仍未找到符合条件的地址")
                # 缓存空结果，避免频繁查询
                self._results_cache[cache_key] = found_addresses
            
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