import asyncio
import aiohttp
from datetime import datetime


SYMBOL = "BTCUSD"
INTERVAL = "15m"


async def get_btc_price():
    url = (
        "https://api.exchange.coinbase.com/products/"
        "BTC-USD/candles?granularity=900"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    if not isinstance(data, list):
        raise Exception(f"Risposta Coinbase non valida: {data}")

    last_candle = data[0]

    open_price = float(last_candle[3])
    close_price = float(last_candle[4])

    return open_price, close_price


async def main():

    print("🚀 BTC Trend AI avviato", flush=True)

    while True:
        try:
            open_price, close_price = await get_btc_price()

            now = datetime.now().strftime("%H:%M:%S")

            direction = (
                "RIALZO"
                if close_price > open_price
                else "RIBASSO"
            )

            print(
                f"{now} | BTCUSD M15 | "
                f"Open: {open_price} | "
                f"Close: {close_price} | "
                f"Movimento: {direction}",
                flush=True
            )

        except Exception as e:
            print("Errore Coinbase:", repr(e), flush=True)

        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
