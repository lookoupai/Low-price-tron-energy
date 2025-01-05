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
        self.tronscan_api = "https://apilist.tronscan.org/api"  # TronScan API
        
        # TronScan API Key
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "TRON-PRO-API-KEY": os.getenv("TRON_API_KEY", "")
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
            
            print(f"\n✅ 已保存 {len(new_records)} 个新记录到文件: {result_file}")
        else:
            print("\n📝 没有新的记录需要保存")
        
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
            response = self._make_request(f"{self.tronscan_api}/block", {
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
            
            # 使用 TronScan API 获取交易信息
            response = self._make_request(f"{self.tronscan_api}/transaction", {
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
                response = self._make_request(f"{self.tronscan_api}/transaction", {
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
                        # 获取实际能量数量
                        energy_amount = self.get_energy_amount(tx.get("hash"))
                        
                        if energy_amount is None:
                            # 如果获取失败，使用合约数据计算
                            staked_trx = float(contract_data.get("balance", 0)) / 1_000_000
                            energy_amount = staked_trx * 11.3661
                            energy_source = "计算值"
                        else:
                            energy_source = "API值"
                        
                        proxy_transactions.append(tx)
                        print("\n找到代理资源交易:")
                        print(f"交易哈希: {tx.get('hash')}")
                        print(f"发送人: {contract_data.get('owner_address')}")
                        print(f"接收人: {contract_data.get('receiver_address')}")
                        print(f"代理数量: {energy_amount:,.2f} 能量")  # 格式化显示为中文
                        
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
            response = self._make_request(f"{self.tronscan_api}/transaction-info", {
                "hash": tx_hash
            })
            return response or {}
        except Exception as e:
            print(f"获取交易详情失败: {e}")
            return {}

    def get_energy_amount(self, tx_hash: str) -> Optional[float]:
        """获取交易中的实际能量数量"""
        tx_info = self.get_transaction_info(tx_hash)
        if tx_info and "contractData" in tx_info:
            contract_data = tx_info["contractData"]
            # 优先使用 resourceValue 字段
            if "resourceValue" in contract_data:
                print(f"使用 API 值: {contract_data['resourceValue']}")
                return float(contract_data["resourceValue"])
            # 如果没有 resourceValue，则使用 balance 计算
            elif "balance" in contract_data:
                print(f"使用计算值: balance = {contract_data['balance']}")
                staked_trx = float(contract_data["balance"]) / 1_000_000
                return staked_trx * 11.3661
        return None

    def analyze_address(self, address: str) -> Optional[Dict]:
        """分析地址的交易记录"""
        try:
            print(f"\n分析地址: {address}")
            
            # 获取地址的最近交易记录
            response = self._make_request(f"{self.tronscan_api}/transaction", {
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
                                    if 0.1 <= amount <= 1:  # 金额范围改回0.1-1 TRX
                                        # 获取收取TRX的地址
                                        trx_receiver = prev_tx.get("toAddress")
                                        
                                        # 获取收款地址的最近交易记录
                                        receiver_response = self._make_request(f"{self.tronscan_api}/transaction", {
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
                                                    if 0.1 <= rtx_amount <= 1:  # 金额范围0.1-1 TRX
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
                                                
                                        # 确保24小时内至少有20笔交易
                                        if max_count >= 5 and sum(amount_count.values()) >= 20:
                                            # 获取实际能量数量
                                            energy_amount = self.get_energy_amount(tx.get("hash"))
                                            
                                            if energy_amount is None:
                                                # 如果获取失败，使用合约数据计算
                                                staked_trx = float(contract_data.get("balance", 0)) / 1_000_000
                                                energy_amount = staked_trx * 11.3661
                                                energy_source = "计算值"
                                            else:
                                                energy_source = "API值"
                                            
                                            print(f"找到符合条件的交易对:")
                                            print(f"TRX转账: {prev_tx.get('hash')} - {amount} TRX")
                                            print(f"收款地址: {trx_receiver}")
                                            print(f"代理资源: {tx.get('hash')} - {energy_amount:,.2f} 能量 ({energy_source})")
                                            print(f"能量提供方: {energy_provider}")
                                            print(f"24小时内相同金额交易数: {max_count}")
                                            print(f"24小时内总交易数: {sum(amount_count.values())}")
                                            print(f"最多交易的金额: {max_amount} TRX")
                                            print(f"24小时内金额统计: {amount_count}")
                                            
                                            return {
                                                "address": trx_receiver,
                                                "energy_provider": energy_provider,
                                                "purchase_amount": max_amount,
                                                "energy_quantity": f"{energy_amount:,.2f} 能量",
                                                "energy_source": energy_source,
                                                "tx_hash": prev_tx.get("hash"),
                                                "proxy_tx_hash": tx.get("hash"),
                                                "recent_tx_count": sum(amount_count.values()),
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
        """查找低成本能量代理地址"""
        try:
            # 获取最新区块
            latest_block = self.get_latest_block()
            if not latest_block:
                print("❌ 获取最新区块失败")
                return
                
            print(f"最新区块号: {latest_block}")
            
            # 初始化结果列表和计数器
            found_addresses = []
            current_block = latest_block
            max_blocks_to_check = 3  # 最多检查10个区块
            blocks_checked = 0
            
            # 持续查找区块，直到找到符合条件的地址或达到最大检查区块数
            while blocks_checked < max_blocks_to_check:
                print(f"\n正在检查区块 {current_block}...")
                
                # 获取区块交易
                transactions = self.get_block_transactions(current_block)
                
                # 分析每个代理交易
                for tx in transactions:
                    contract_data = tx.get("contractData", {})
                    if (tx.get("contractType") == 57 and 
                        contract_data.get("resource") == "ENERGY"):
                        
                        # 获取代理能量数量
                        energy_amount = self.get_energy_amount(tx.get("hash"))
                        if energy_amount is None:
                            continue
                            
                        # 分析接收方地址
                        receiver_address = contract_data.get("receiver_address")
                        if receiver_address:
                            address_info = self.analyze_address(receiver_address)
                            if address_info:
                                found_addresses.append(address_info)
                                # 找到符合条件的地址后，立即保存并返回结果
                                self._save_results(found_addresses)
                                self._print_results(found_addresses)
                                return found_addresses
                
                # 如果当前区块没有找到，继续检查前一个区块
                current_block -= 1
                blocks_checked += 1
                
            if not found_addresses:
                print(f"\n⚠️ 检查了 {blocks_checked} 个区块后仍未找到符合条件的地址")
            
            return found_addresses
            
        except Exception as e:
            print(f"查找低成本能量代理地址时发生错误: {e}")
            return []

    def _print_results(self, addresses):
        """格式化输出结果"""
        if not addresses:
            print("\n❌ 未找到符合条件的低价能量地址")
            return
            
        print("\n🎉 找到以下低价能量地址：\n")
        for addr in addresses:
            # 如果是计算值，添加提示信息
            energy_display = addr['energy_quantity']
            if addr['energy_source'] == "计算值":
                energy_display = f"{energy_display} (计算值，仅供参考)"
                
            print(f"""🔹 【收款地址】: {addr['address']}
🔹 【能量提供方】: {addr['energy_provider']}
🔹 【购买记录】: https://tronscan.org/#/address/{addr['address']}
🔹 【收款金额】: {addr['purchase_amount']} TRX
🔹 【能量数量】: {energy_display}
🔹 【24h交易数】: {addr['recent_tx_count']} 笔
🔹 【转账哈希】: {addr['tx_hash']}
🔹 【代理哈希】: {addr['proxy_tx_hash']}

【地址信息】{addr['status']}
""")

def main():
    # 检查API Key
    if not os.getenv("TRON_API_KEY"):
        print("警告: 未设置TRON_API_KEY环境变量，TronScan API访问可能受限")
        print("请在.env文件中设置TRON_API_KEY=你的TronScan API密钥")
    
    finder = TronEnergyFinder()
    finder.find_low_cost_energy_addresses()

if __name__ == "__main__":
    main() 