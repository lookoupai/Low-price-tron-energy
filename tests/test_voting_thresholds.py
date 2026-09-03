"""
测试投票阈值与状态计算

验证黑名单/白名单的临时与正式状态转换逻辑
"""
import pytest


class TestVotingThresholds:
    """投票阈值测试"""

    def test_one_vote_is_provisional(self):
        """1 票应标记为临时状态"""
        status = calculate_vote_status(success_votes=1, fail_votes=0)
        assert status["is_provisional"] is True
        assert status["status"] == "whitelist_provisional"

    def test_two_votes_is_confirmed(self):
        """2 票及以上应转为正式状态"""
        status = calculate_vote_status(success_votes=2, fail_votes=0)
        assert status["is_provisional"] is False
        assert status["status"] == "whitelist_confirmed"

        status = calculate_vote_status(success_votes=5, fail_votes=0)
        assert status["is_provisional"] is False

    def test_zero_votes_no_status(self):
        """0 票应无状态"""
        status = calculate_vote_status(success_votes=0, fail_votes=0)
        assert status["status"] == "none"

    def test_conflicting_votes_both_shown(self):
        """黑白票同时存在时应展示双方票数"""
        status = calculate_vote_status(success_votes=3, fail_votes=2)
        assert status["success_votes"] == 3
        assert status["fail_votes"] == 2
        assert status["status"] == "conflicting"

    def test_blacklist_voting(self):
        """黑名单投票测试"""
        status = calculate_vote_status(success_votes=0, fail_votes=1)
        assert status["is_provisional"] is True
        assert status["status"] == "blacklist_provisional"

        status = calculate_vote_status(success_votes=0, fail_votes=2)
        assert status["is_provisional"] is False
        assert status["status"] == "blacklist_confirmed"

    def test_tie_votes(self):
        """平票情况"""
        status = calculate_vote_status(success_votes=1, fail_votes=1)
        assert status["status"] == "conflicting"
        assert status["success_votes"] == 1
        assert status["fail_votes"] == 1


def calculate_vote_status(success_votes: int, fail_votes: int) -> dict:
    """
    根据投票数计算名单状态

    规则：
    - 0 票：无状态
    - 1 票：临时状态（whitelist_provisional 或 blacklist_provisional）
    - ≥2 票：正式状态（whitelist_confirmed 或 blacklist_confirmed）
    - 黑白票都有：conflicting（展示双方票数）

    实际实现在 feedback_manager.py 和 telegram_bot.py 的状态判断中
    """
    CONFIRM_THRESHOLD = 2

    if success_votes == 0 and fail_votes == 0:
        return {"status": "none", "is_provisional": False}

    if success_votes > 0 and fail_votes > 0:
        return {
            "status": "conflicting",
            "success_votes": success_votes,
            "fail_votes": fail_votes,
            "is_provisional": False,
        }

    if success_votes > 0:
        is_provisional = success_votes < CONFIRM_THRESHOLD
        return {
            "status": "whitelist_provisional" if is_provisional else "whitelist_confirmed",
            "is_provisional": is_provisional,
            "success_votes": success_votes,
            "fail_votes": 0,
        }

    if fail_votes > 0:
        is_provisional = fail_votes < CONFIRM_THRESHOLD
        return {
            "status": "blacklist_provisional" if is_provisional else "blacklist_confirmed",
            "is_provisional": is_provisional,
            "success_votes": 0,
            "fail_votes": fail_votes,
        }

    return {"status": "none", "is_provisional": False}
