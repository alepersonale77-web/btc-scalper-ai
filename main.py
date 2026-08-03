import asyncio
import aiohttp
from datetime import datetime


PRODUCT = "BTC-USD"


async def get_candle(granularity):

    url = (
        f"https://api.exchange.coinbase.com/products/"
        f"{PRODUCT}/candles?granularity={granularity}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    if not isinstance(data, list):
        raise Exception(f"Risposta Coinbase non valida: {data}")

    candle = data[0]

    open_price = float(candle[3])
    close_price = float(candle[4])

    return open_price, close_price


def direction(open_price, close_price):

    if close_price > open_price:
        return "RIALZO"
    else:
        return "RIBASSO"


async def main():

    print("🚀 BTC Trend AI avviato", flush=True)

    while True:

        try:
            # H4
            h4_open, h4_close = await get_candle(14400)

            # H1
            h1_open, h1_close = await get_candle(3600)

            # M15
            m15_open, m15_close = await get_candle(900)


            h4 = direction(h4_open, h4_close)
            h1 = direction(h1_open, h1_close)
            m15 = direction(m15_open, m15_close)


            now = datetime.now().strftime("%H:%M:%S")


            stato = (
                "TREND ALLINEATO"
                if h4 == h1 == m15
                else "IN ATTESA"
            )


            print(
                f"\n{now} | BTC Trend AI\n"
                f"H4: {h4}\n"
                f"H1: {h1}\n"
                f"M15: {m15}\n"
                f"Stato: {stato}",
                flush=True
            )


        except Exception as e:
            print("Errore:", repr(e), flush=True)


        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
