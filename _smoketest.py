import asyncio
from settings_manager import SettingsManager
from whitelist_manager import WhitelistManager
from blacklist_manager import BlacklistManager

async def main():
    sm=SettingsManager()
    await sm.init_database()
    print("assoc_before", await sm.is_blacklist_association_enabled())
    await sm.set_blacklist_association_enabled(True)
    print("assoc_after", await sm.is_blacklist_association_enabled())
    wm=WhitelistManager()
    await wm.init_database()
    pa="TLa2f6VPqDgRE67v1736s7E2dQxC3vMT7"
    pr="TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8"
    await wm.add_address(pa,"payment","test",123,is_provisional=True)
    await wm.add_address(pr,"provider","test",123,is_provisional=True)
    await wm.add_pair(pa,pr,123,is_provisional=True)
    print("pair_info", await wm.check_pair(pa,pr))
    bm=BlacklistManager()
    await bm.init_database()
    await bm.add_to_blacklist(pr,"temp",123,"manual",is_provisional=True)
    print("provider_black", await bm.check_blacklist(pr))
    print("payment_black_before", await bm.check_blacklist(pa))
    await bm.auto_associate_addresses(pa, pr)
    print("payment_black_after", await bm.check_blacklist(pa))
    await wm.close(); await bm.close(); await sm.close()

asyncio.run(main())
