import asyncio
from settings_manager import SettingsManager
from whitelist_manager import WhitelistManager
from blacklist_manager import BlacklistManager

PAY="TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
PROV="TEkxiTehnzSmSe2XqrBj4w32RUN966rdz8"

async def main():
    sm=SettingsManager()
    await sm.init_database()
    await sm.set_blacklist_association_enabled(True)
    wm=WhitelistManager()
    await wm.init_database()
    await wm.add_address(PAY,"payment","test",123,is_provisional=True)
    await wm.add_address(PROV,"provider","test",123,is_provisional=True)
    await wm.add_pair(PAY,PROV,123,is_provisional=True)
    pair=await wm.check_pair(PAY,PROV)
    print("pair_provisional", pair and pair.get("is_provisional"))
    bm=BlacklistManager()
    await bm.init_database()
    await bm.add_to_blacklist(PROV,"temp",123,"manual",is_provisional=True)
    print("provider_black_is_prov", (await bm.check_blacklist(PROV)).get("is_provisional"))
    print("payment_black_before", await bm.check_blacklist(PAY))
    await bm.auto_associate_addresses(PAY, PROV)
    print("payment_black_after_is_prov", (await bm.check_blacklist(PAY) or {}).get("is_provisional"))
    await wm.close(); await bm.close(); await sm.close()

asyncio.run(main())
