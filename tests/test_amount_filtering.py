"""
测试金额筛选逻辑

回归重点：本文件曾自带一份 is_rental_amount 副本，既没有导入真实实现，
断言的四舍五入行为（round(0.01005, 4) == 0.0101）也与 Python 浮点实际行为不符，
属于测了假逻辑。现在直接调用 TronEnergyFinder._is_rental_amount。
"""
from types import SimpleNamespace

import pytest

from tron_energy_finder import TronEnergyFinder

# 真实实现只做区间比较，四舍五入由调用方在 sun 换算成 TRX 时完成
_check = TronEnergyFinder._is_rental_amount
_STUB = SimpleNamespace(min_trx_amount=0.01, max_trx_amount=1.0)


def is_rental_amount(amount: float, min_trx: float, max_trx: float) -> bool:
    """按生产链路调用：先把金额四舍五入到 4 位，再交给真实的区间判断"""
    return _check(_STUB, round(amount, 4), min_trx, max_trx)


def sun_to_trx(sun: int) -> float:
    """复刻生产代码里 sun 转 TRX 的换算"""
    return round(float(sun) / 1_000_000, 4)


class TestAmountFiltering:
    """金额筛选测试"""

    def test_exact_match(self):
        """精确匹配测试"""
        assert is_rental_amount(0.01, 0.01, 0.01) is True
        assert is_rental_amount(0.5, 0.5, 0.5) is True
        assert is_rental_amount(0.0099, 0.01, 0.01) is False
        assert is_rental_amount(0.0101, 0.01, 0.01) is False

    def test_range_match(self):
        """区间匹配测试"""
        assert is_rental_amount(0.05, 0.01, 0.1) is True
        assert is_rental_amount(0.01, 0.01, 0.1) is True
        assert is_rental_amount(0.1, 0.01, 0.1) is True
        assert is_rental_amount(0.009, 0.01, 0.1) is False
        assert is_rental_amount(0.11, 0.01, 0.1) is False

    def test_edge_cases(self):
        """边界情况测试"""
        assert is_rental_amount(0, 0, 0) is True
        assert is_rental_amount(0.0001, 0, 0.001) is True
        assert is_rental_amount(1.0, 0.01, 1.0) is True
        assert is_rental_amount(1.0001, 0.01, 1.0) is False

    def test_falls_back_to_configured_range(self):
        """不传区间时应回退到实例配置的区间"""
        assert _check(_STUB, 0.5) is True
        assert _check(_STUB, 2.0) is False


class TestSunConversion:
    """sun 换算精度测试"""

    def test_common_rental_amounts_are_exact(self):
        """常见租能量金额换算后应精确落在区间端点上"""
        assert sun_to_trx(10_000) == 0.01
        assert sun_to_trx(500_000) == 0.5
        assert sun_to_trx(1_000_000) == 1.0

    def test_sub_precision_amounts_collapse_to_four_digits(self):
        """低于 4 位精度的零头会被并入相邻档位，这是可接受的行为"""
        # 10050 sun = 0.01005 TRX，四舍五入到 4 位后与 0.01 无法区分
        assert sun_to_trx(10_050) == 0.01
        assert is_rental_amount(sun_to_trx(10_050), 0.01, 0.01) is True
