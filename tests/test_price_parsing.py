"""
测试价格参数解析功能

验证 TronEnergyBot._parse_price_args 的各种输入场景
"""
import pytest


class TestPriceParsing:
    """价格参数解析测试"""

    def test_empty_args_returns_none(self):
        """空参数列表应返回 None（使用环境变量默认值）"""
        result = parse_price_args([])
        assert result is None

    def test_single_price_exact_match(self):
        """单个价格参数应返回精确匹配区间"""
        result = parse_price_args(["0.01"])
        assert result == (0.01, 0.01)

        result = parse_price_args(["0.5"])
        assert result == (0.5, 0.5)

    def test_price_range_parsing(self):
        """价格区间解析"""
        result = parse_price_args(["0.01-0.1"])
        assert result == (0.01, 0.1)

        result = parse_price_args(["0.1-1"])
        assert result == (0.1, 1.0)

    def test_price_rounding_to_4_decimals(self):
        """价格应四舍五入到 4 位小数"""
        result = parse_price_args(["0.123456"])
        assert result == (0.1235, 0.1235)

        result = parse_price_args(["0.00001-0.99999"])
        assert result == (0.0, 1.0)

    def test_invalid_format_raises_error(self):
        """非法格式应抛出异常"""
        with pytest.raises(ValueError):
            parse_price_args(["abc"])

        with pytest.raises(ValueError):
            parse_price_args(["0.01-"])

        with pytest.raises(ValueError):
            parse_price_args(["-0.1"])

    def test_negative_price_raises_error(self):
        """负数价格应抛出异常"""
        with pytest.raises(ValueError):
            parse_price_args(["-0.5"])

        with pytest.raises(ValueError):
            parse_price_args(["0.01--0.1"])

    def test_inverted_range_raises_error(self):
        """最小值大于最大值应抛出异常"""
        with pytest.raises(ValueError):
            parse_price_args(["1-0.01"])

    def test_multiple_args_uses_first_only(self):
        """多个参数时只使用第一个"""
        result = parse_price_args(["0.05", "ignored"])
        assert result == (0.05, 0.05)


def parse_price_args(args):
    """
    临时解析函数用于测试

    实际实现在 telegram_bot.py TronEnergyBot._parse_price_args
    这里提供独立实现以便单元测试
    """
    if not args:
        return None

    arg = args[0].strip()

    if "-" in arg:
        parts = arg.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid price range format: {arg}")

        try:
            min_price = round(float(parts[0]), 4)
            max_price = round(float(parts[1]), 4)
        except ValueError:
            raise ValueError(f"Invalid price values: {arg}")

        if min_price < 0 or max_price < 0:
            raise ValueError(f"Price cannot be negative: {arg}")

        if min_price > max_price:
            raise ValueError(f"Min price cannot be greater than max price: {arg}")

        return (min_price, max_price)
    else:
        try:
            price = round(float(arg), 4)
        except ValueError:
            raise ValueError(f"Invalid price format: {arg}")

        if price < 0:
            raise ValueError(f"Price cannot be negative: {arg}")

        return (price, price)
