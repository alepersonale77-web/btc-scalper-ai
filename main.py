import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp


PRODUCT = "BTC-USD"
CHECK_INTERVAL_SECONDS = 300

EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200

COINBASE_URL = (
    f"https://api.exchange.coinbase.com/products/"
    f"{PRODUCT}/candles"
)


def floor_time(value: datetime, seconds: int) -> datetime:
    timestamp = int(value.timestamp())
    floored = timestamp - (timestamp % seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)


def to_coinbase_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


async def fetch_candles(
    session: aiohttp.ClientSession,
    granularity: int,
    required_count: int,
) -> list[list[float]]:
    """
    Scarica candele Coinbase completate.
    Coinbase restituisce:
    [time, low, high, open, close, volume]
    """

    candles_by_time: dict[int, list[float]] = {}

    now = datetime.now(timezone.utc)
    final_boundary = floor_time(now, granularity)
    end_time = final_boundary

    while len(candles_by_time) < required_count:
        missing = required_count - len(candles_by_time)
        chunk_size = min(300, missing + 5)

        start_time = end_time - timedelta(
            seconds=granularity * chunk_size
        )

        params = {
            "granularity": str(granularity),
            "start": to_coinbase_time(start_time),
            "end": to_coinbase_time(end_time),
        }

        async with session.get(
            COINBASE_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            data = await response.json()

            if response.status != 200:
                raise RuntimeError(
                    f"Coinbase HTTP {response.status}: {data}"
                )

        if not isinstance(data, list):
            raise RuntimeError(
                f"Risposta Coinbase non valida: {data}"
            )

        for candle in data:
            candle_time = int(candle[0])

            # Ignora la candela ancora aperta.
            if candle_time < int(final_boundary.timestamp()):
                candles_by_time[candle_time] = candle

        if not data:
            break

        end_time = start_time

    candles = sorted(
        candles_by_time.values(),
        key=lambda candle: int(candle[0]),
    )

    if len(candles) < required_count:
        raise RuntimeError(
            f"Candele insufficienti: richieste {required_count}, "
            f"ricevute {len(candles)}"
        )

    return candles[-required_count:]


def aggregate_h1_to_h4(
    h1_candles: list[list[float]],
) -> list[list[float]]:
    """
    Aggrega quattro candele H1 complete in una candela H4.
    """

    groups: dict[int, list[list[float]]] = {}

    for candle in h1_candles:
        timestamp = int(candle[0])
        h4_bucket = timestamp - (timestamp % 14400)

        groups.setdefault(h4_bucket, []).append(candle)

    h4_candles: list[list[float]] = []

    for bucket in sorted(groups):
        group = sorted(
            groups[bucket],
            key=lambda candle: int(candle[0]),
        )

        # Una candela H4 è valida solo se contiene 4 ore complete.
        if len(group) != 4:
            continue

        low_price = min(float(candle[1]) for candle in group)
        high_price = max(float(candle[2]) for candle in group)
        open_price = float(group[0][3])
        close_price = float(group[-1][4])
        volume = sum(float(candle[5]) for candle in group)

        h4_candles.append(
            [
                bucket,
                low_price,
                high_price,
                open_price,
                close_price,
                volume,
            ]
        )

    return h4_candles


def calculate_ema(
    values: list[float],
    period: int,
) -> float:
    if len(values) < period:
        raise ValueError(
            f"Servono almeno {period} valori per calcolare EMA{period}"
        )

    multiplier = 2 / (period + 1)
    ema_value = sum(values[:period]) / period

    for value in values[period:]:
        ema_value = (
            value * multiplier
            + ema_value * (1 - multiplier)
        )

    return ema_value


def analyze_timeframe(
    candles: list[list[float]],
    timeframe: str,
) -> dict[str, float | str]:
    closes = [float(candle[4]) for candle in candles]

    price = closes[-1]
    ema50 = calculate_ema(closes, EMA_FAST_PERIOD)
    ema200 = calculate_ema(closes, EMA_SLOW_PERIOD)

    if ema50 > ema200 and price > ema50:
        trend = "RIALZISTA"
    elif ema50 < ema200 and price < ema50:
        trend = "RIBASSISTA"
    else:
        trend = "NEUTRO / LATERALE"

    ema_distance_percent = (
        abs(ema50 - ema200) / ema200
    ) * 100

    return {
        "timeframe": timeframe,
        "price": price,
        "ema50": ema50,
        "ema200": ema200,
        "distance": ema_distance_percent,
        "trend": trend,
    }


def determine_state(
    h4: dict[str, float | str],
    h1: dict[str, float | str],
    m15: dict[str, float | str],
) -> str:
    h4_trend = str(h4["trend"])
    h1_trend = str(h1["trend"])
    m15_trend = str(m15["trend"])

    if h4_trend == "RIALZISTA" and h1_trend == "RIALZISTA":
        if m15_trend == "RIALZISTA":
            return "TREND RIALZISTA CONFERMATO"

        if m15_trend == "RIBASSISTA":
            return "PULLBACK IN TREND RIALZISTA"

        return "ATTESA INGRESSO BUY"

    if h4_trend == "RIBASSISTA" and h1_trend == "RIBASSISTA":
        if m15_trend == "RIBASSISTA":
            return "TREND RIBASSISTA CONFERMATO"

        if m15_trend == "RIALZISTA":
            return "PULLBACK IN TREND RIBASSISTA"

        return "ATTESA INGRESSO SELL"

    return "NESSUN SETUP: H4 E H1 NON ALLINEATI"


def format_analysis(
    analysis: dict[str, float | str],
) -> str:
    return (
        f"{analysis['timeframe']}: {analysis['trend']}\n"
        f"  Prezzo: {float(analysis['price']):.2f}\n"
        f"  EMA50: {float(analysis['ema50']):.2f}\n"
        f"  EMA200: {float(analysis['ema200']):.2f}\n"
        f"  Distanza EMA: "
        f"{float(analysis['distance']):.3f}%"
    )


async def run_analysis(
    session: aiohttp.ClientSession,
) -> None:
    # Circa 205 candele H4 richiedono almeno 820 ore H1.
    h1_history = await fetch_candles(
        session=session,
        granularity=3600,
        required_count=830,
    )

    m15_history = await fetch_candles(
        session=session,
        granularity=900,
        required_count=220,
    )

    h4_history = aggregate_h1_to_h4(h1_history)

    if len(h4_history) < EMA_SLOW_PERIOD:
        raise RuntimeError(
            f"Candele H4 insufficienti: {len(h4_history)}"
        )

    h4 = analyze_timeframe(
        h4_history[-220:],
        "H4",
    )

    h1 = analyze_timeframe(
        h1_history[-220:],
        "H1",
    )

    m15 = analyze_timeframe(
        m15_history[-220:],
        "M15",
    )

    state = determine_state(h4, h1, m15)

    now = datetime.now().strftime("%H:%M:%S")

    print(
        f"\n{now} | BTC Trend AI v0.4\n\n"
        f"{format_analysis(h4)}\n\n"
        f"{format_analysis(h1)}\n\n"
        f"{format_analysis(m15)}\n\n"
        f"STATO: {state}",
        flush=True,
    )


async def main() -> None:
    print(
        "🚀 BTC Trend AI v0.4 avviato",
        flush=True,
    )

    headers = {
        "User-Agent": "BTC-Trend-AI/0.4",
        "Accept": "application/json",
    }

    async with aiohttp.ClientSession(
        headers=headers
    ) as session:
        while True:
            try:
                await run_analysis(session)

            except Exception as error:
                print(
                    "Errore BTC Trend AI:",
                    repr(error),
                    flush=True,
                )

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
