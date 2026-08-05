import asyncio
import aiohttp
from datetime import datetime


PRODUCT = "BTC-USD"


async def get_candles(granularity, limit=5):

    url = (
        f"https://api.exchange.coinbase.com/products/"
        f"{PRODUCT}/candles?granularity={granularity}&limit={limit}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    if not isinstance(data, list):
        raise Exception(f"Risposta Coinbase non valida: {data}")

    return data


def candle_direction(open_price, close_price):

    if close_price > open_price:
        return "RIALZO"
    else:
        return "RIBASSO"


async def main():

    print("🚀 BTC Trend AI v0.3 avviato", flush=True)  

    while True:

        try:
            # H1 - usiamo 4 candele per costruire H4
            h1_candles = await get_candles(3600, 5)

            h4_open = float(h1_candles[3][3])
            h4_close = float(h1_candles[0][4])

            # H1
            h1_open = float(h1_candles[1][3])
            h1_close = float(h1_candles[1][4])

            # M15
            m15_candles = await get_candles(900, 2)

            m15_open = float(m15_candles[0][3])
            m15_close = float(m15_candles[0][4])


            h4 = candle_direction(h4_open, h4_close)
            h1 = candle_direction(h1_open, h1_close)
            m15 = candle_direction(m15_open, m15_close)


            stato = (
                "TREND ALLINEATO"
                if h4 == h1 == m15
                else "IN ATTESA"
            )


            now = datetime.now().strftime("%H:%M:%S")


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
