"""
测试 TRX 转账与能量代理配对逻辑

验证区块内 Type-1 转账与能量代理记录的配对判断
"""
import pytest


class TestTransactionPairing:
    """转账配对测试"""

    def test_matching_pair(self):
        """匹配的转账与代理应返回 True"""
        transfer = {
            "from": "TFromAddress123",
            "to": "TToAddress456",
            "amount": 10000000,  # 10 TRX (单位为 sun)
        }
        proxy = {
            "owner_address": "TFromAddress123",
            "receiver_address": "TToAddress456",
            "frozen_balance": 10000000,
        }
        assert is_matching_pair(transfer, proxy) is True

    def test_non_matching_from_address(self):
        """发送方地址不匹配应返回 False"""
        transfer = {
            "from": "TDifferentAddress",
            "to": "TToAddress456",
            "amount": 10000000,
        }
        proxy = {
            "owner_address": "TFromAddress123",
            "receiver_address": "TToAddress456",
            "frozen_balance": 10000000,
        }
        assert is_matching_pair(transfer, proxy) is False

    def test_non_matching_to_address(self):
        """接收方地址不匹配应返回 False"""
        transfer = {
            "from": "TFromAddress123",
            "to": "TDifferentAddress",
            "amount": 10000000,
        }
        proxy = {
            "owner_address": "TFromAddress123",
            "receiver_address": "TToAddress456",
            "frozen_balance": 10000000,
        }
        assert is_matching_pair(transfer, proxy) is False

    def test_non_matching_amount(self):
        """金额不匹配应返回 False"""
        transfer = {
            "from": "TFromAddress123",
            "to": "TToAddress456",
            "amount": 15000000,  # 15 TRX
        }
        proxy = {
            "owner_address": "TFromAddress123",
            "receiver_address": "TToAddress456",
            "frozen_balance": 10000000,  # 10 TRX
        }
        assert is_matching_pair(transfer, proxy) is False

    def test_case_sensitivity(self):
        """地址大小写敏感测试"""
        transfer = {
            "from": "tfromaddress123",
            "to": "TToAddress456",
            "amount": 10000000,
        }
        proxy = {
            "owner_address": "TFromAddress123",
            "receiver_address": "TToAddress456",
            "frozen_balance": 10000000,
        }
        assert is_matching_pair(transfer, proxy) is False


def is_matching_pair(transfer: dict, proxy: dict) -> bool:
    """
    判断 Type-1 转账与能量代理是否匹配

    匹配条件：
    1. transfer["from"] == proxy["owner_address"]
    2. transfer["to"] == proxy["receiver_address"]
    3. transfer["amount"] == proxy["frozen_balance"]

    实际实现将在 tron_energy_finder.py 的块扫描优化中使用
    """
    return (
        transfer.get("from") == proxy.get("owner_address")
        and transfer.get("to") == proxy.get("receiver_address")
        and transfer.get("amount") == proxy.get("frozen_balance")
    )
