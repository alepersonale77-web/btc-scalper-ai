import asyncio
import aiohttp
from datetime import datetime


SYMBOL = "BTCUSDT"
INTERVAL = "15m"


async def get_btc_price():
    url = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={SYMBOL}&interval={INTERVAL}&limit=3"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    last_candle = data[-1]

    open_price = float(last_candle[1])
    close_price = float(last_candle[4])

    return open_price, close_price


async def main():
    printprint("🚀 BTC Scalper AI avviato", flush=True)

    while True:
        try:
            open_price, close_price = await get_btc_price()

            now = datetime.now().strftime("%H:%M:%S")

            direction = "RIALZO" if close_price > open_price else "RIBASSO"

            print(
                f"{now} | BTCUSDT M15 | "
                f"Open: {open_price} | "
                f"Close: {close_price} | "
                f"Movimento: {direction}",
                flush=True
            )

        except Exception as e:
            print("Errore:", e)

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
