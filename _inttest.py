import asyncio, os
os.environ["TRON_API_KEY_1"]="test_key"
from settings_manager import SettingsManager
from whitelist_manager import WhitelistManager
from blacklist_manager import BlacklistManager
from tron_energy_finder import TronEnergyFinder

PAY1="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
PROV1="TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8"
PAY2="TVjsY4m2xT9m6Gk1QfL1qg3hFh8jKQ7q9A"
PROV2="TJ9yJ8r8TKcHecgq2tT5nC8vLwHnB8YvW7"

async def main():
    sm=SettingsManager(); await sm.init_database(); await sm.set_blacklist_association_enabled(True)
    wm=WhitelistManager(); await wm.init_database()
    bm=BlacklistManager(); await bm.init_database()
    # Test 1: pair whitelist priority
    await wm.add_pair(PAY1, PROV1, 1, is_provisional=True)
    finder=TronEnergyFinder()
    r1=await finder.check_and_handle_blacklist(PAY1, PROV1)
    print("T1_pair_whitelisted", r1.get("pair_whitelisted"), "bl_warn", bool(r1.get("blacklist_warning")))
    # Test 2: only payment whitelisted
    await wm.add_address(PAY2, "payment", "ok", 2, is_provisional=True)
    r2=await finder.check_and_handle_blacklist(PAY2, PROV1)
    print("T2_payment_wl_only", r2.get("payment_whitelisted"), "provider_wl", r2.get("provider_whitelisted"), "pair_wl", r2.get("pair_whitelisted"))
    print("T2_whitelist_notice", r2.get("whitelist_notice").strip())
    # Test 3: provider blacklisted causes association to payment when enabled
    await bm.add_to_blacklist(PROV2, "bad", 3, "manual", is_provisional=True)
    r3=await finder.check_and_handle_blacklist(PAY2, PROV2)
    print("T3_provider_blacklisted", r3.get("provider_blacklisted"), "auto_assoc", r3.get("auto_associated"))
    # Verify payment became blacklisted
    print("payment_black_after", bool(await bm.check_blacklist(PAY2)))
    # Test 4: disable association and verify no propagation
    await sm.set_blacklist_association_enabled(False)
    PAY3="TYuBy1n5rS7dExZb7dvLKkqj7k5x3n9F1J"
    PROV3="TKpEtzBQ6YJr3m4v8E1XnmFMm4sQ2Wc3nX"
    await bm.add_to_blacklist(PROV3, "bad2", 4, "manual", is_provisional=True)
    r4=await finder.check_and_handle_blacklist(PAY3, PROV3)
    print("T4_assoc_disabled", r4.get("provider_blacklisted"), r4.get("auto_associated"))
    print("payment_black_after_disabled", await bm.check_blacklist(PAY3))
    # Close
    await wm.close(); await bm.close(); await sm.close()

asyncio.run(main())
