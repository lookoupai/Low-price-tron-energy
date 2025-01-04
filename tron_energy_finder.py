import requests
import json
import time
from datetime import datetime
from tqdm import tqdm
from typing import List, Dict, Optional
import os
from dotenv import load_dotenv
import pathlib

# 加载环境变量
load_dotenv()

class TronEnergyFinder:
    def __init__(self):
        self.base_url = "https://apilist.tronscan.org/api"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "TRON-PRO-API-KEY": os.getenv("TRON_API_KEY", "")  # 从环境变量获取API Key
        }
        self.retry_count = 3
        self.retry_delay = 2  # 秒
        
        # 创建results目录
        self.results_dir = pathlib.Path("results")
        self.results_dir.mkdir(exist_ok=True)
        
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
        
    def _save_results(self, addresses: List[Dict]):
        """保存结果到文件"""
        if not addresses:
            return
            
        # 加载已有结果
        results = self._load_existing_results()
        
        # 获取已存在的地址集合
        existing_addresses = {record["address"] for record in results["records"]}
        
        # 添加新记录
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        new_records = []
        for addr in addresses:
            if addr["address"] not in existing_addresses:
                addr["found_time"] = current_time
                new_records.append(addr)
                existing_addresses.add(addr["address"])
        
        if new_records:
            results["records"].extend(new_records)
            
            # 保存到文件
            result_file = self._get_result_file()
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            
            print(f"\n✅ 已保存 {len(new_records)} 个新地址到文件: {result_file}")
        else:
            print("\n📝 没有新的地址需要保存")
        
    def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        """带重试机制的请求方法"""
        for attempt in range(self.retry_count):
            try:
                response = requests.get(url, params=params, headers=self.headers)
                response.raise_for_status()
                return response.json()
            except Exception as e:
                if attempt == self.retry_count - 1:
                    print(f"请求失败 ({url}): {e}")
                    return None
                print(f"请求失败，{self.retry_delay}秒后重试: {e}")
                time.sleep(self.retry_delay)
        return None

    def get_latest_block(self) -> Optional[int]:
        """获取最新区块号"""
        try:
            response = self._make_request(f"{self.base_url}/block", {
                "sort": "-number",
                "limit": 1,
                "count": True
            })
            if response and "data" in response and response["data"]:
                return response["data"][0]["number"]
            return None
        except Exception as e:
            print(f"获取最新区块失败: {e}")
            return None

    def get_block_transactions(self, block_number: int) -> List[Dict]:
        """获取区块交易详情"""
        try:
            print(f"正在获取区块 {block_number} 的交易信息...")
            
            # 首先获取总交易数
            response = self._make_request(f"{self.base_url}/transaction", {
                "block": block_number,
                "limit": 1,
                "start": 0,
                "count": True
            })
            
            if not response:
                return []
                
            total_transactions = response.get("total", 0)
            print(f"区块总交易数: {total_transactions}")
            
            # 分批获取所有交易
            all_transactions = []
            start = 0
            limit = 200  # 每次获取200条
            
            while start < total_transactions:
                response = self._make_request(f"{self.base_url}/transaction", {
                    "block": block_number,
                    "limit": limit,
                    "start": start,
                    "count": True
                })
                
                if not response or "data" not in response:
                    break
                    
                transactions = response.get("data", [])
                if not transactions:
                    break
                    
                all_transactions.extend(transactions)
                start += len(transactions)
                print(f"已获取 {len(all_transactions)}/{total_transactions} 条交易记录")
                time.sleep(0.5)  # 避免请求过快
            
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
                        print("\n找到代理资源交易:")
                        print(f"交易哈希: {tx.get('hash')}")
                        print(f"发送人: {contract_data.get('owner_address')}")
                        print(f"接收人: {contract_data.get('receiver_address')}")
                        print(f"代理数量: {contract_data.get('balance')} Energy")
            
            if proxy_transactions:
                print(f"\n找到 {len(proxy_transactions)} 笔代理资源交易")
            else:
                print("\n未找到代理资源交易记录")
                
            return proxy_transactions
            
        except Exception as e:
            print(f"获取区块交易详情失败: {e}")
            return []

    def get_transaction_info(self, tx_hash: str) -> Dict:
        """获取交易详细信息"""
        try:
            response = self._make_request(f"{self.base_url}/transaction-info", {
                "hash": tx_hash
            })
            return response or {}
        except Exception as e:
            print(f"获取交易详情失败: {e}")
            return {}

    def analyze_address(self, address: str) -> Optional[Dict]:
        """分析地址的交易记录"""
        try:
            print(f"\n分析地址: {address}")
            
            # 获取地址的最近交易记录
            response = self._make_request(f"{self.base_url}/transaction", {
                "address": address,
                "limit": 50,
                "sort": "-timestamp"
            })
            
            if not response or "data" not in response:
                return None
                
            transactions = response["data"]
            
            # 先找到代理资源交易
            for i, tx in enumerate(transactions):
                # 检查是否是代理资源交易
                if tx.get("contractType") == 57:
                    contract_data = tx.get("contractData", {})
                    if contract_data.get("resource") == "ENERGY":
                        # 获取代理能量数量
                        energy_amount = contract_data.get("balance", 0)
                        proxy_time = tx.get("timestamp", 0)
                        energy_provider = contract_data.get("owner_address")  # 能量提供方
                        
                        # 向后查找是否有对应的TRX转账（时间更早的交易）
                        for j in range(i + 1, len(transactions)):
                            prev_tx = transactions[j]
                            if (prev_tx.get("contractType") == 1 and  # TRX 转账
                                prev_tx.get("timestamp", 0) < proxy_time):  # 确保转账在代理之前
                                try:
                                    amount = float(prev_tx.get("amount", 0)) / 1_000_000  # 转换为TRX
                                    amount = round(amount, 4)  # 四舍五入到4位小数
                                    if 0.1 <= amount <= 2:  # 金额范围0.1-2 TRX
                                        # 获取收取TRX的地址
                                        trx_receiver = prev_tx.get("toAddress")
                                        
                                        # 获取收款地址的最近交易记录
                                        receiver_response = self._make_request(f"{self.base_url}/transaction", {
                                            "address": trx_receiver,
                                            "limit": 50,
                                            "sort": "-timestamp"
                                        })
                                        
                                        if not receiver_response or "data" not in receiver_response:
                                            continue
                                            
                                        receiver_txs = receiver_response["data"]
                                        
                                        # 分析收款地址的最近交易
                                        current_time = int(time.time() * 1000)
                                        amount_count = {}
                                        
                                        print(f"\n分析收款地址 {trx_receiver} 的最近交易...")
                                        for rtx in receiver_txs:  # 分析所有获取到的交易
                                            # 检查是否在24小时内
                                            tx_time = rtx.get("timestamp", 0)
                                            if current_time - tx_time > 24 * 60 * 60 * 1000:
                                                continue
                                                
                                            if rtx.get("contractType") == 1:  # TRX转账
                                                try:
                                                    rtx_amount = float(rtx.get("amount", 0)) / 1_000_000
                                                    rtx_amount = round(rtx_amount, 4)
                                                    if 0.1 <= rtx_amount <= 2:
                                                        amount_count[rtx_amount] = amount_count.get(rtx_amount, 0) + 1
                                                        print(f"找到符合金额范围的TRX转账: {rtx_amount} TRX, 当前计数: {amount_count[rtx_amount]}")
                                                except (ValueError, TypeError):
                                                    continue
                                        
                                        # 检查是否有至少5笔相同金额的交易
                                        max_count = max(amount_count.values()) if amount_count else 0
                                        max_amount = None
                                        for amt, cnt in amount_count.items():
                                            if cnt == max_count:
                                                max_amount = amt
                                                break
                                                
                                        if max_count >= 5:
                                            print(f"找到符合条件的交易对:")
                                            print(f"TRX转账: {prev_tx.get('hash')} - {amount} TRX")
                                            print(f"收款地址: {trx_receiver}")
                                            print(f"代理资源: {tx.get('hash')} - {energy_amount} Energy")
                                            print(f"能量提供方: {energy_provider}")
                                            print(f"24小时内相同金额交易数: {max_count}")
                                            print(f"最多交易的金额: {max_amount} TRX")
                                            
                                            return {
                                                "address": trx_receiver,
                                                "energy_provider": energy_provider,
                                                "purchase_amount": amount,
                                                "energy_quantity": energy_amount,
                                                "tx_hash": prev_tx.get("hash"),
                                                "proxy_tx_hash": tx.get("hash"),
                                                "recent_tx_count": max_count,
                                                "recent_tx_amount": max_amount,
                                                "status": "正常使用"
                                            }
                                except (ValueError, TypeError):
                                    continue
            
            return None
            
        except Exception as e:
            print(f"分析地址 {address} 时出错: {e}")
            return None

    def find_low_cost_energy_addresses(self):
        """主函数：查找低价能量地址"""
        print("🔍 开始查找低价TRON能量地址...")
        
        # 获取最新区块
        latest_block = self.get_latest_block()
        if not latest_block:
            print("获取最新区块失败")
            return
            
        print(f"\n📦 正在分析区块 {latest_block}")
        
        # 获取区块交易
        transactions = self.get_block_transactions(latest_block)
        if not transactions:
            print("未找到代理资源交易记录")
            return
            
        found_addresses = []
        unique_addresses = set()
        
        # 分析每个代理资源交易的接收地址
        for tx in tqdm(transactions, desc="分析交易"):
            try:
                # 获取接收地址
                address = tx.get("toAddress")
                if not address or address in unique_addresses:
                    continue
                    
                unique_addresses.add(address)
                result = self.analyze_address(address)
                if result:
                    found_addresses.append(result)
                
            except Exception as e:
                print(f"处理交易时出错: {e}")
                continue
            
            time.sleep(0.5)  # 避免请求过快
            
        # 输出结果
        self._print_results(found_addresses)
        
        # 保存结果
        self._save_results(found_addresses)
        
    def _print_results(self, addresses):
        """格式化输出结果"""
        if not addresses:
            print("\n❌ 未找到符合条件的低价能量地址")
            return
            
        print("\n🎉 找到以下低价能量地址：\n")
        for addr in addresses:
            print(f"""🔹 【收款地址】: {addr['address']}
🔹 【能量提供方】: {addr['energy_provider']}
🔹 【购买记录】: https://tronscan.org/#/address/{addr['address']}
🔹 【收款金额】: {addr['purchase_amount']} TRX
🔹 【能量数量】: {addr['energy_quantity']} Energy
🔹 【24h交易数】: {addr['recent_tx_count']} 笔
🔹 【转账哈希】: {addr['tx_hash']}
🔹 【代理哈希】: {addr['proxy_tx_hash']}

【地址信息】{addr['status']}
""")

def main():
    # 检查API Key
    if not os.getenv("TRON_API_KEY"):
        print("警告: 未设置TRON_API_KEY环境变量，API访问可能受限")
        print("请在.env文件中设置TRON_API_KEY=你的API密钥")
    
    finder = TronEnergyFinder()
    finder.find_low_cost_energy_addresses()

if __name__ == "__main__":
    main() 