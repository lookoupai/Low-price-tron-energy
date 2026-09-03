"""
测试金额筛选逻辑

验证 TronEnergyFinder 中金额是否在指定区间的判断
"""
import pytest


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

    def test_rounding_precision(self):
        """四舍五入精度测试"""
        # 0.01005 四舍五入到 4 位为 0.0101
        assert is_rental_amount(0.01005, 0.01, 0.01) is False
        # 0.01004 四舍五入到 4 位为 0.01
        assert is_rental_amount(0.01004, 0.01, 0.01) is True

    def test_edge_cases(self):
        """边界情况测试"""
        assert is_rental_amount(0, 0, 0) is True
        assert is_rental_amount(0.0001, 0, 0.001) is True
        assert is_rental_amount(1.0, 0.01, 1.0) is True
        assert is_rental_amount(1.0001, 0.01, 1.0) is False


def is_rental_amount(amount: float, min_trx: float, max_trx: float) -> bool:
    """
    判断金额是否在指定区间（四舍五入到 4 位后比较）

    实际实现在 tron_energy_finder.py TronEnergyFinder._is_rental_amount
    """
    rounded = round(amount, 4)
    return min_trx <= rounded <= max_trx
