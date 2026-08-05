import asyncio
import aiohttp
from datetime import datetime


PRODUCT = "BTC-USD"


async def get_candles(granularity, limit=5):

    url = (
        f"https://api.exchange.coinbase.com/products/"
        f"{PRODUCT}/candles?granularity={granularity}"
    )

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            data = await response.json()

    if not isinstance(data, list):
        raise Exception(f"Risposta Coinbase non valida: {data}")

    # Coinbase manda le candele dalla più recente alla più vecchia
    data = data[:limit]

    # Ordiniamo dalla più vecchia alla più recente
    data.reverse()

    return data


def analyze_trend(candles):

    bullish = 0
    bearish = 0

    for candle in candles:
        open_price = float(candle[3])
        close_price = float(candle[4])

        if close_price > open_price:
            bullish += 1
        else:
            bearish += 1

    total = len(candles)

    if bullish >= total - 1:
        return f"RIALZO FORTE ({bullish}/{total})"

    if bearish >= total - 1:
        return f"RIBASSO FORTE ({bearish}/{total})"

    if bullish > bearish:
        return f"RIALZO ({bullish}/{total})"

    if bearish > bullish:
        return f"RIBASSO ({bearish}/{total})"

    return f"INCERTO ({bullish}/{total})"


def get_state(h4, h1, m15):

    if "RIALZO" in h4 and "RIALZO" in h1:
        if "RIBASSO" in m15:
            return "PULLBACK RIALZISTA"

        return "TREND RIALZISTA"

    if "RIBASSO" in h4 and "RIBASSO" in h1:
        if "RIALZO" in m15:
            return "PULLBACK RIBASSISTA"

        return "TREND RIBASSISTA"

    return "ATTESA"


async def main():

    print("🚀 BTC Trend AI v0.3.1 avviato", flush=True)

    while True:

        try:

            # H4 costruito da 5 candele H1
            h1_candles = await get_candles(3600, 5)

            h4 = analyze_trend(h1_candles)

            # H1
            h1_candles = await get_candles(3600, 5)

            h1 = analyze_trend(h1_candles)

            # M15
            m15_candles = await get_candles(900, 5)

            m15 = analyze_trend(m15_candles)


            stato = get_state(h4, h1, m15)

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
