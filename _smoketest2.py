import asyncio
from blacklist_manager import BlacklistManager

async def main():
    bm=BlacklistManager()
    await bm.init_database()
    pa="TLa2f6VPqDgRE67v1736s7E2dQxC3vMT7"
    ok=await bm.add_to_blacklist(pa,"assoc-test",999,"auto_associated",is_provisional=True)
    print("add_payment_ok", ok)
    print("payment_black", await bm.check_blacklist(pa))
    await bm.close()

asyncio.run(main())
