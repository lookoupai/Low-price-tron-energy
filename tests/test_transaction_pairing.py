"""
测试连续区块窗口内 TRX 转账与能量代理的配对逻辑

回归重点：
1. _pair_transactions_in_block 曾用 payment["toAddress"] 与代理的 receiver_address
   比对，方向是反的（买家是付款的发起方，不是收款方）。
2. 旧实现只在单个区块内配对，且要求 payment_time < proxy_time。TronScan 返回的
   timestamp 是区块时间戳，同块内所有交易时间戳相同，而付款与代理通常相隔 1-2 块，
   两个条件叠加导致配对恒为 0 个。
3. 付款与代理相隔过久不构成因果关系（买家换供应方重试），必须有 10 秒上限。
"""
import pytest

from tron_energy_finder import TronEnergyFinder

pair = TronEnergyFinder._pair_transactions_in_window

BLOCK_MS = 3000
BASE_TS = 1_700_000_000_000

BUYER = "TBuyerAddress0000000000000000000001"
PAYEE = "TPayeeAddress0000000000000000000002"
PROVIDER = "TProviderAddress00000000000000000003"


def make_payment(owner=BUYER, to=PAYEE, trx=0.1, block=0, tx_hash="payment_hash"):
    return {
        "contractType": 1,
        "hash": tx_hash,
        "ownerAddress": owner,
        "toAddress": to,
        "amount": int(trx * 1_000_000),
        "timestamp": BASE_TS + block * BLOCK_MS,
    }


def make_proxy(receiver=BUYER, owner=PROVIDER, block=1, tx_hash="proxy_hash"):
    return {
        "contractType": 57,
        "hash": tx_hash,
        "contractData": {
            "resource": "ENERGY",
            "owner_address": owner,
            "receiver_address": receiver,
            "balance": 10_000_000,
        },
        "timestamp": BASE_TS + block * BLOCK_MS,
    }


class TestWindowPairing:
    """跨块配对测试"""

    def test_pairs_across_blocks(self):
        """买家在前一块付款、下一块收到能量代理时应配对成功"""
        pairs = pair([make_payment(block=0)], [make_proxy(block=1)], 0.01, 1.0)
        assert len(pairs) == 1
        assert pairs[0]["buyer"] == BUYER
        # 推送给用户的收款地址取自付款的 toAddress，不是买家自己
        assert pairs[0]["payment_address"] == PAYEE
        assert pairs[0]["provider"] == PROVIDER
        assert pairs[0]["amount"] == 0.1
        assert pairs[0]["delay_ms"] == BLOCK_MS

    def test_pairs_within_same_block(self):
        """同块内付款与代理时间戳相同，仍应配对（旧实现要求严格早于，永远配不上）"""
        pairs = pair([make_payment(block=0)], [make_proxy(block=0)], 0.01, 1.0)
        assert len(pairs) == 1
        assert pairs[0]["delay_ms"] == 0

    def test_does_not_pair_on_reversed_direction(self):
        """付款只是转给买家（买家不是发起方）时不应配对"""
        payment = make_payment(owner=PAYEE, to=BUYER)
        assert pair([payment], [make_proxy()], 0.01, 1.0) == []

    def test_does_not_pair_when_payment_after_delegation(self):
        """付款时间晚于代理时不应配对"""
        assert pair([make_payment(block=2)], [make_proxy(block=1)], 0.01, 1.0) == []

    def test_does_not_pair_when_delay_exceeds_limit(self):
        """付款与代理相隔超过 10 秒视为换供应方重试，不算因果配对"""
        assert pair([make_payment(block=0)], [make_proxy(block=5)], 0.01, 1.0) == []

    def test_pairs_at_delay_limit_boundary(self):
        """恰好等于 10 秒上限时应配对"""
        pairs = pair(
            [make_payment(block=0)],
            [make_proxy(block=0)],
            0.01,
            1.0,
            max_delay_ms=BLOCK_MS,
        )
        assert len(pairs) == 1

    def test_does_not_pair_when_amount_out_of_range(self):
        """付款金额超出筛选区间时不应配对"""
        assert pair([make_payment(trx=5.0)], [make_proxy()], 0.01, 1.0) == []

    def test_keeps_payment_closest_to_delegation(self):
        """同一买家多笔付款时保留时间最接近代理的那笔"""
        early = make_payment(trx=0.1, block=0)
        late = make_payment(trx=0.5, block=1)
        pairs = pair([early, late], [make_proxy(block=2)], 0.01, 1.0)
        assert len(pairs) == 1
        assert pairs[0]["amount"] == 0.5

    def test_ignores_proxy_without_receiver(self):
        """代理交易缺少 receiver_address 时应跳过"""
        proxy = make_proxy()
        del proxy["contractData"]["receiver_address"]
        assert pair([make_payment()], [proxy], 0.01, 1.0) == []

    def test_ignores_payment_without_payee(self):
        """付款缺少 toAddress 时无法得出收款地址，应跳过"""
        payment = make_payment()
        del payment["toAddress"]
        assert pair([payment], [make_proxy()], 0.01, 1.0) == []

    def test_pairs_multiple_buyers_independently(self):
        """多个买家应各自独立配对"""
        buyer2 = "TBuyerAddress0000000000000000000004"
        payments = [make_payment(), make_payment(owner=buyer2, trx=0.2)]
        proxies = [make_proxy(), make_proxy(receiver=buyer2, tx_hash="proxy_hash_2")]
        pairs = pair(payments, proxies, 0.01, 1.0)
        assert {p["buyer"] for p in pairs} == {BUYER, buyer2}

    def test_sorts_pairs_newest_first(self):
        """配对结果应按代理时间倒序，优先分析最新报价"""
        buyer2 = "TBuyerAddress0000000000000000000004"
        payments = [make_payment(block=0), make_payment(owner=buyer2, block=3)]
        proxies = [
            make_proxy(block=1, tx_hash="old"),
            make_proxy(receiver=buyer2, block=4, tx_hash="new"),
        ]
        pairs = pair(payments, proxies, 0.01, 1.0)
        assert [p["proxy"]["hash"] for p in pairs] == ["new", "old"]

    def test_one_payee_can_serve_multiple_buyers(self):
        """同一收款地址服务多个买家时应产生多条配对，供上层聚合"""
        buyer2 = "TBuyerAddress0000000000000000000004"
        payments = [make_payment(), make_payment(owner=buyer2)]
        proxies = [make_proxy(), make_proxy(receiver=buyer2, tx_hash="proxy_hash_2")]
        pairs = pair(payments, proxies, 0.01, 1.0)
        assert len(pairs) == 2
        assert {p["payment_address"] for p in pairs} == {PAYEE}
