"""
测试投票后的名单重算逻辑

背景：投票允许改主意——record_vote 的 ON CONFLICT 会覆盖 vote_type，旧票随之消失。
早先只写入新票对应的那张表，旧票留下的另一张表记录不会被清掉，地址会同时躺在
白名单和黑名单里；而查询是白名单优先，于是更新的那次反馈被旧记录屏蔽。
现在每次投票/撤票都按当前票数重算两张表。
"""
from types import SimpleNamespace

import pytest

from feedback_manager import CONFIRM_THRESHOLD, VOTE_FAIL, VOTE_SUCCESS
from telegram_bot import TronEnergyBot

reconcile = TronEnergyBot._reconcile_address_lists
suffix = TronEnergyBot._vote_status_suffix

PAYMENT = "TPayeeAddress0000000000000000000002"
PROVIDER = "TProviderAddress00000000000000000003"


class FakeFeedback:
    """只按 (地址, 角色) 返回预设票数"""

    def __init__(self, counts):
        self._counts = counts

    async def count_address_votes(self, address, address_type):
        return dict(
            self._counts.get((address, address_type), {VOTE_SUCCESS: 0, VOTE_FAIL: 0})
        )


class FakeWhitelist:
    def __init__(self):
        self.removed = []
        self.provisional = []

    async def remove_address(self, address, address_type, only_feedback=False):
        self.removed.append((address, address_type, only_feedback))

    async def set_provisional(self, address, address_type, is_provisional):
        self.provisional.append((address, address_type, is_provisional))


class FakeBlacklist:
    def __init__(self):
        self.removed = []
        self.provisional = []

    async def remove_from_blacklist(self, address, only_feedback=False):
        self.removed.append((address, only_feedback))

    async def set_provisional(self, address, is_provisional):
        self.provisional.append((address, is_provisional))


def make_bot(counts=None):
    return SimpleNamespace(
        feedback_manager=FakeFeedback(counts or {}),
        whitelist_manager=FakeWhitelist(),
        blacklist_manager=FakeBlacklist(),
    )


def votes(success=0, fail=0):
    return {VOTE_SUCCESS: success, VOTE_FAIL: fail}


class TestVoteFlipReconciliation:
    """改票后两张名单都要重算"""

    async def test_flip_success_to_fail_drops_whitelist_row(self):
        """成功改判未成功：旧的白名单记录必须被移除，否则白名单优先会屏蔽新票"""
        bot = make_bot({(PAYMENT, 'payment'): votes(success=0, fail=1)})
        await reconcile(bot, PAYMENT, 'payment')

        assert bot.whitelist_manager.removed == [(PAYMENT, 'payment', True)]
        assert bot.whitelist_manager.provisional == []
        assert bot.blacklist_manager.provisional == [(PAYMENT, True)]
        assert bot.blacklist_manager.removed == []

    async def test_flip_fail_to_success_drops_blacklist_row(self):
        """未成功改判成功：旧的黑名单记录必须被移除"""
        bot = make_bot({(PROVIDER, 'provider'): votes(success=1, fail=0)})
        await reconcile(bot, PROVIDER, 'provider')

        assert bot.blacklist_manager.removed == [(PROVIDER, True)]
        assert bot.blacklist_manager.provisional == []
        assert bot.whitelist_manager.provisional == [(PROVIDER, 'provider', True)]

    async def test_cleanup_never_touches_non_feedback_rows(self):
        """清理必须带 only_feedback，管理员手工条目不能被投票冲掉"""
        bot = make_bot()
        await reconcile(bot, PAYMENT, 'payment')

        assert all(call[-1] is True for call in bot.whitelist_manager.removed)
        assert all(call[-1] is True for call in bot.blacklist_manager.removed)

    async def test_no_votes_clears_both_lists(self):
        """票数归零（撤回）时两张表都要清掉"""
        bot = make_bot()
        await reconcile(bot, PAYMENT, 'payment')

        assert bot.whitelist_manager.removed == [(PAYMENT, 'payment', True)]
        assert bot.blacklist_manager.removed == [(PAYMENT, True)]

    async def test_conflicting_votes_keep_both_rows(self):
        """不同用户投出相反票时两条记录都保留，交由白名单优先规则裁决"""
        bot = make_bot({(PAYMENT, 'payment'): votes(success=1, fail=1)})
        await reconcile(bot, PAYMENT, 'payment')

        assert bot.whitelist_manager.removed == []
        assert bot.blacklist_manager.removed == []
        assert bot.whitelist_manager.provisional == [(PAYMENT, 'payment', True)]
        assert bot.blacklist_manager.provisional == [(PAYMENT, True)]

    async def test_reconcile_returns_current_counts(self):
        """返回值用于生成"临时/已确认"后缀"""
        bot = make_bot({(PAYMENT, 'payment'): votes(success=3, fail=1)})
        counts = await reconcile(bot, PAYMENT, 'payment')

        assert counts == {VOTE_SUCCESS: 3, VOTE_FAIL: 1}

    async def test_payment_and_provider_counted_separately(self):
        """同一次调用只处理传入的那个角色，另一个地址不受影响"""
        bot = make_bot({
            (PAYMENT, 'payment'): votes(success=2, fail=0),
            (PROVIDER, 'provider'): votes(success=0, fail=0),
        })
        await reconcile(bot, PAYMENT, 'payment')

        assert bot.whitelist_manager.provisional == [(PAYMENT, 'payment', False)]
        assert bot.whitelist_manager.removed == []


class TestThresholdPromotion:
    """票数达到阈值后从临时转正式"""

    async def test_single_vote_stays_provisional(self):
        bot = make_bot({(PAYMENT, 'payment'): votes(success=1)})
        await reconcile(bot, PAYMENT, 'payment')
        assert bot.whitelist_manager.provisional == [(PAYMENT, 'payment', True)]

    async def test_threshold_votes_become_confirmed(self):
        bot = make_bot({(PAYMENT, 'payment'): votes(success=CONFIRM_THRESHOLD)})
        await reconcile(bot, PAYMENT, 'payment')
        assert bot.whitelist_manager.provisional == [(PAYMENT, 'payment', False)]

    async def test_blacklist_threshold_becomes_confirmed(self):
        bot = make_bot({(PROVIDER, 'provider'): votes(fail=CONFIRM_THRESHOLD)})
        await reconcile(bot, PROVIDER, 'provider')
        assert bot.blacklist_manager.provisional == [(PROVIDER, False)]


class TestStatusSuffix:
    """展示后缀"""

    def test_below_threshold_is_provisional(self):
        assert suffix(1) == "（临时，1 票）"

    def test_at_threshold_is_confirmed(self):
        assert suffix(CONFIRM_THRESHOLD).startswith("（已确认")

    def test_zero_votes_is_provisional(self):
        assert suffix(0) == "（临时，0 票）"

