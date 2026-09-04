"""
测试 10 秒到账率统计与收款频率描述

背景：
1. 用户认定「转 TRX 后 10 秒内收到能量代理」才算真到账，超时到账是买家换了
   别的出租方重试，不能算原收款地址的功劳。
2. 【24h交易数】被 TronScan 单页 50 条上限截断，忙碌地址与冷清地址都显示
   "50 笔"，必须换成基于时间跨度的频率描述。
"""
import pytest

from tron_energy_finder import TronEnergyFinder

stats = TronEnergyFinder._delivery_stats
frequency = TronEnergyFinder._format_tx_frequency

BLOCK_MS = 3000
BASE_TS = 1_700_000_000_000
WINDOW_END = BASE_TS + 5 * BLOCK_MS  # 6 块窗口的末尾时间

BUYER = "TBuyerAddress0000000000000000000001"
PAYEE = "TPayeeAddress0000000000000000000002"
PROVIDER = "TProviderAddress00000000000000000003"


def make_payment(owner=BUYER, to=PAYEE, trx=0.1, block=0):
    return {
        "contractType": 1,
        "ownerAddress": owner,
        "toAddress": to,
        "amount": int(trx * 1_000_000),
        "timestamp": BASE_TS + block * BLOCK_MS,
    }


def make_proxy(receiver=BUYER, block=1):
    return {
        "contractType": 57,
        "contractData": {
            "resource": "ENERGY",
            "owner_address": PROVIDER,
            "receiver_address": receiver,
            "balance": 10_000_000,
        },
        "timestamp": BASE_TS + block * BLOCK_MS,
    }


class TestDeliveryStats:
    """付款到账率统计测试"""

    def test_counts_delivery_within_window(self):
        """10 秒内收到能量代理算到账"""
        result = stats([make_payment(block=0)], [make_proxy(block=1)], 0.01, 1.0, WINDOW_END)
        assert result[PAYEE] == {"total": 1, "delivered": 1}

    def test_counts_missing_delivery_as_failure(self):
        """完全没有能量代理时算未到账"""
        result = stats([make_payment(block=0)], [], 0.01, 1.0, WINDOW_END)
        assert result[PAYEE] == {"total": 1, "delivered": 0}

    def test_late_delivery_is_not_counted(self):
        """超过 10 秒才到账的算未到账（买家已换供应方重试）"""
        late_end = BASE_TS + 20 * BLOCK_MS
        result = stats([make_payment(block=0)], [make_proxy(block=5)], 0.01, 1.0, late_end)
        assert result[PAYEE] == {"total": 1, "delivered": 0}

    def test_skips_payments_without_enough_observation(self):
        """窗口末尾的付款还没到观察期，不能判定为失败"""
        # 付款在窗口最后一块，其能量代理会落在窗口外
        result = stats([make_payment(block=5)], [], 0.01, 1.0, WINDOW_END)
        assert result == {}

    def test_ignores_out_of_range_amounts(self):
        """区间外的转账不参与统计"""
        result = stats([make_payment(trx=5.0)], [], 0.01, 1.0, WINDOW_END)
        assert result == {}

    def test_aggregates_multiple_payments_per_payee(self):
        """同一收款地址的多笔付款应累加"""
        buyer2 = "TBuyerAddress0000000000000000000004"
        payments = [make_payment(block=0), make_payment(owner=buyer2, block=1)]
        result = stats(payments, [make_proxy(block=1)], 0.01, 1.0, WINDOW_END)
        assert result[PAYEE] == {"total": 2, "delivered": 1}

    def test_energy_to_other_buyer_does_not_count(self):
        """能量发给别的买家不能算这笔付款到账"""
        other = "TBuyerAddress0000000000000000000004"
        result = stats(
            [make_payment(block=0)], [make_proxy(receiver=other, block=1)], 0.01, 1.0, WINDOW_END
        )
        assert result[PAYEE] == {"total": 1, "delivered": 0}

    def test_separates_payees_of_same_buyer(self):
        """同一买家付给不同收款地址时应分别归属"""
        payee2 = "TPayeeAddress0000000000000000000005"
        payments = [make_payment(block=0), make_payment(to=payee2, block=1)]
        result = stats(payments, [], 0.01, 1.0, WINDOW_END)
        assert result[PAYEE]["total"] == 1
        assert result[payee2]["total"] == 1

    def test_payees_filter_excludes_unrelated_addresses(self):
        """限定收款地址后，恰好落在金额区间的无关地址不进入统计

        实测有地址 18 秒内收到 89 笔区间内转账却与能量租凭无关，
        不过滤会把整体到账率压到毫无意义的低位。
        """
        unrelated = "TExchangeAddress00000000000000000006"
        payments = [make_payment(block=0), make_payment(to=unrelated, block=1)]
        result = stats(payments, [], 0.01, 1.0, WINDOW_END, payees={PAYEE})
        assert set(result) == {PAYEE}

    def test_empty_payees_filter_yields_nothing(self):
        """没有配对出任何收款地址时不应统计任何付款"""
        assert stats([make_payment(block=0)], [], 0.01, 1.0, WINDOW_END, payees=set()) == {}


class TestFrequencyFormat:
    """收款频率描述测试"""

    def test_high_frequency_uses_per_minute(self):
        """50 笔集中在几分钟内应按分钟计"""
        text = frequency(50, 378_000)  # 6.3 分钟
        assert "50 笔" in text
        assert "6.3 分钟" in text
        assert "8 笔/分钟" in text

    def test_medium_frequency_uses_per_hour(self):
        """50 笔跨 5 小时应按小时计"""
        text = frequency(50, 18_000_000)
        assert "5.0 小时" in text
        assert "10 笔/小时" in text

    def test_low_frequency_uses_per_day(self):
        """20 笔跨 40 小时应按天计"""
        text = frequency(20, 144_000_000)
        assert "1.7 天" in text
        assert "12.0 笔/天" in text

    def test_zero_span_reports_insufficient_data(self):
        """跨度为 0 无法估算频率"""
        assert "跨度不足" in frequency(5, 0)

    def test_single_transaction_reports_insufficient_data(self):
        """只有 1 笔无法估算频率"""
        assert "跨度不足" in frequency(1, 60_000)
