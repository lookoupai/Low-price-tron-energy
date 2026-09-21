"""区块缓存必须有上限，并且不能保留 TronScan 交易原文。"""
import pytest

from tron_energy_finder import (
    BLOCK_CACHE_MAX_BLOCKS,
    BLOCK_CACHE_TTL_SECONDS,
    TronEnergyFinder,
)
from cachetools import TTLCache


def _bare_finder():
    finder = TronEnergyFinder.__new__(TronEnergyFinder)
    finder._block_cache = TTLCache(
        maxsize=BLOCK_CACHE_MAX_BLOCKS,
        ttl=BLOCK_CACHE_TTL_SECONDS,
    )
    finder.tronscan_api = "https://example.invalid"
    return finder


def _fat_payment(block_number):
    return {
        "contractType": 1,
        "hash": f"pay-{block_number}",
        "timestamp": 1_700_000_000_000 + block_number,
        "ownerAddress": "TBuyer",
        "toAddress": "TPayee",
        "amount": "100000",
        "tokenInfo": {"name": "trx", "extra": "x" * 200},
        "confirmed": True,
    }


def _fat_proxy(block_number):
    return {
        "contractType": 57,
        "hash": f"proxy-{block_number}",
        "timestamp": 1_700_000_000_000 + block_number,
        "cost": {"fee": 1},
        "contractData": {
            "resource": "ENERGY",
            "balance": 10_000_000,
            "owner_address": "TProvider",
            "receiver_address": "TBuyer",
            "extra": "drop-me",
        },
    }


class TestSlimTransactions:
    def test_payment_drops_unused_fields(self):
        slim = TronEnergyFinder._slim_payment(_fat_payment(1))
        assert slim == {
            "contractType": 1,
            "hash": "pay-1",
            "timestamp": 1_700_000_000_001,
            "ownerAddress": "TBuyer",
            "toAddress": "TPayee",
            "amount": "100000",
        }

    def test_proxy_drops_unused_fields(self):
        slim = TronEnergyFinder._slim_proxy(_fat_proxy(1))
        assert slim == {
            "contractType": 57,
            "hash": "proxy-1",
            "timestamp": 1_700_000_000_001,
            "contractData": {
                "resource": "ENERGY",
                "balance": 10_000_000,
                "owner_address": "TProvider",
                "receiver_address": "TBuyer",
            },
        }

    def test_slimmed_records_still_pair(self):
        pairs = TronEnergyFinder._pair_transactions_in_window(
            [TronEnergyFinder._slim_payment(_fat_payment(1))],
            [TronEnergyFinder._slim_proxy(_fat_proxy(1))],
            0.01,
            1.0,
        )
        assert len(pairs) == 1
        assert pairs[0]["payment_address"] == "TPayee"
        assert pairs[0]["provider"] == "TProvider"
        assert "extra" not in pairs[0]["proxy"]["contractData"]


class TestBlockCacheBound:
    @pytest.mark.asyncio
    async def test_caches_slim_records_and_drops_oldest_block(self):
        finder = _bare_finder()

        async def fake_energy():
            return 10.0

        async def fake_request(url, params=None, timeout=30, allow_retry=True):
            block_number = int(params["block"])
            if params.get("limit") == "1":
                return {"total": 2}
            return {
                "total": 2,
                "data": [_fat_payment(block_number), _fat_proxy(block_number)],
            }

        finder.get_energy_per_trx = fake_energy
        finder._make_request = fake_request

        for number in range(BLOCK_CACHE_MAX_BLOCKS + 1):
            result = await finder.get_block_transactions(number)
            assert "tokenInfo" not in result["payments"][0]
            assert "extra" not in result["proxies"][0]["contractData"]

        assert len(finder._block_cache) == BLOCK_CACHE_MAX_BLOCKS
        assert "block_0" not in finder._block_cache
        assert f"block_{BLOCK_CACHE_MAX_BLOCKS}" in finder._block_cache
        cached = finder._block_cache[f"block_{BLOCK_CACHE_MAX_BLOCKS}"]
        assert cached["payments"][0]["hash"] == f"pay-{BLOCK_CACHE_MAX_BLOCKS}"
