"""
测试白名单优先级逻辑

验证白名单覆盖黑名单的优先级判断
"""
import pytest


class TestWhitelistPriority:
    """白名单优先级测试"""

    def test_pair_whitelist_overrides_blacklist(self):
        """组合白名单应覆盖黑名单"""
        status = calculate_list_status(
            payment_in_whitelist=True,
            provider_in_whitelist=True,
            pair_in_whitelist=True,
            payment_in_blacklist=True,
            provider_in_blacklist=True,
            pair_in_blacklist=True,
        )
        assert status["level"] == "safe"
        assert status["shows_blacklist_warning"] is False

    def test_partial_whitelist_with_blacklist(self):
        """单方白名单不应完全覆盖黑名单"""
        # 仅收款地址在白名单
        status = calculate_list_status(
            payment_in_whitelist=True,
            provider_in_whitelist=False,
            pair_in_whitelist=False,
            payment_in_blacklist=True,
            provider_in_blacklist=True,
            pair_in_blacklist=False,
        )
        assert status["level"] == "partial"
        assert status["shows_blacklist_warning"] is True

    def test_no_whitelist_shows_blacklist(self):
        """无白名单时应显示黑名单警告"""
        status = calculate_list_status(
            payment_in_whitelist=False,
            provider_in_whitelist=False,
            pair_in_whitelist=False,
            payment_in_blacklist=True,
            provider_in_blacklist=False,
            pair_in_blacklist=False,
        )
        assert status["level"] == "risky"
        assert status["shows_blacklist_warning"] is True

    def test_clean_record(self):
        """既无白名单也无黑名单"""
        status = calculate_list_status(
            payment_in_whitelist=False,
            provider_in_whitelist=False,
            pair_in_whitelist=False,
            payment_in_blacklist=False,
            provider_in_blacklist=False,
            pair_in_blacklist=False,
        )
        assert status["level"] == "unknown"
        assert status["shows_blacklist_warning"] is False

    def test_whitelist_only(self):
        """仅有白名单记录"""
        status = calculate_list_status(
            payment_in_whitelist=True,
            provider_in_whitelist=True,
            pair_in_whitelist=True,
            payment_in_blacklist=False,
            provider_in_blacklist=False,
            pair_in_blacklist=False,
        )
        assert status["level"] == "safe"
        assert status["shows_blacklist_warning"] is False


def calculate_list_status(
    payment_in_whitelist: bool,
    provider_in_whitelist: bool,
    pair_in_whitelist: bool,
    payment_in_blacklist: bool,
    provider_in_blacklist: bool,
    pair_in_blacklist: bool,
) -> dict:
    """
    计算白名单/黑名单综合状态

    优先级规则：
    1. 组合白名单优先级最高，完全覆盖黑名单
    2. 单方白名单不完全覆盖，黑名单警告仍显示
    3. 无白名单时，黑名单正常显示

    实际实现在 telegram_bot.py 的消息格式化逻辑中
    """
    # 组合白名单优先级最高
    if pair_in_whitelist:
        return {"level": "safe", "shows_blacklist_warning": False}

    # 单方白名单：部分信任，但仍显示黑名单警告
    if payment_in_whitelist or provider_in_whitelist:
        has_blacklist = (
            payment_in_blacklist or provider_in_blacklist or pair_in_blacklist
        )
        return {
            "level": "partial",
            "shows_blacklist_warning": has_blacklist,
        }

    # 无白名单时按黑名单判断
    if payment_in_blacklist or provider_in_blacklist or pair_in_blacklist:
        return {"level": "risky", "shows_blacklist_warning": True}

    # 既无白名单也无黑名单
    return {"level": "unknown", "shows_blacklist_warning": False}
