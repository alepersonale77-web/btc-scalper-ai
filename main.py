import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from telegram import Bot


PRODUCT = "BTC-USD"
CHECK_INTERVAL_SECONDS = 300

EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200
RSI_PERIOD = 14
ATR_PERIOD = 14

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

COINBASE_URL = (
    f"https://api.exchange.coinbase.com/products/"
    f"{PRODUCT}/candles"
)

last_alert_key: str | None = None


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
    candles_by_time: dict[int, list[float]] = {}

    now = datetime.now(timezone.utc)
    final_boundary = floor_time(now, granularity)
    end_time = final_boundary

    while len(candles_by_time) < required_count:
        missing = required_count - len(candles_by_time)
        chunk_size = min(300, missing + 5)
        start_time = end_time - timedelta(seconds=granularity * chunk_size)

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
            raise RuntimeError(f"Risposta Coinbase non valida: {data}")

        for candle in data:
            candle_time = int(candle[0])
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

        if len(group) != 4:
            continue

        low_price = min(float(candle[1]) for candle in group)
        high_price = max(float(candle[2]) for candle in group)
        open_price = float(group[0][3])
        close_price = float(group[-1][4])
        volume = sum(float(candle[5]) for candle in group)

        h4_candles.append(
            [bucket, low_price, high_price, open_price, close_price, volume]
        )

    return h4_candles


def calculate_ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Servono almeno {period} valori per EMA{period}")

    multiplier = 2 / (period + 1)
    ema_value = sum(values[:period]) / period

    for value in values[period:]:
        ema_value = value * multiplier + ema_value * (1 - multiplier)

    return ema_value


def calculate_rsi(values: list[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError(
            f"Servono almeno {period + 1} valori per RSI{period}"
        )

    gains: list[float] = []
    losses: list[float] = []

    for previous, current in zip(values[:-1], values[1:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for gain, loss in zip(gains[period:], losses[period:]):
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def calculate_atr(
    candles: list[list[float]],
    period: int = 14,
) -> float:
    if len(candles) < period + 1:
        raise ValueError(
            f"Servono almeno {period + 1} candele per ATR{period}"
        )

    true_ranges: list[float] = []

    for previous, current in zip(candles[:-1], candles[1:]):
        high = float(current[2])
        low = float(current[1])
        previous_close = float(previous[4])

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        true_ranges.append(true_range)

    atr_value = sum(true_ranges[:period]) / period

    for true_range in true_ranges[period:]:
        atr_value = ((atr_value * (period - 1)) + true_range) / period

    return atr_value


def analyze_timeframe(
    candles: list[list[float]],
    timeframe: str,
) -> dict[str, float | str]:
    closes = [float(candle[4]) for candle in candles]

    price = closes[-1]
    ema50 = calculate_ema(closes, EMA_FAST_PERIOD)
    ema200 = calculate_ema(closes, EMA_SLOW_PERIOD)
    rsi = calculate_rsi(closes, RSI_PERIOD)
    atr = calculate_atr(candles, ATR_PERIOD)
    atr_percent = (atr / price) * 100

    if ema50 > ema200 and price > ema50:
        trend = "RIALZISTA"
    elif ema50 < ema200 and price < ema50:
        trend = "RIBASSISTA"
    else:
        trend = "NEUTRO / LATERALE"

    ema_distance_percent = abs(ema50 - ema200) / ema200 * 100

    return {
        "timeframe": timeframe,
        "price": price,
        "ema50": ema50,
        "ema200": ema200,
        "ema_distance": ema_distance_percent,
        "rsi": rsi,
        "atr": atr,
        "atr_percent": atr_percent,
        "trend": trend,
    }


def determine_direction(
    h4: dict[str, float | str],
    h1: dict[str, float | str],
) -> str:
    if h4["trend"] == "RIALZISTA" and h1["trend"] == "RIALZISTA":
        return "BUY"
    if h4["trend"] == "RIBASSISTA" and h1["trend"] == "RIBASSISTA":
        return "SELL"
    return "NONE"


def calculate_setup_score(
    direction: str,
    h4: dict[str, float | str],
    h1: dict[str, float | str],
    m15: dict[str, float | str],
) -> tuple[int, list[str]]:
    if direction == "NONE":
        return 0, ["H4 e H1 non sono allineati"]

    score = 0
    reasons: list[str] = []
    expected_trend = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"

    if h4["trend"] == expected_trend:
        score += 22
        reasons.append(f"H4 {expected_trend.lower()}")
        if float(h4["ema_distance"]) >= 0.30:
            score += 8
            reasons.append("H4 con buona separazione EMA")

    if h1["trend"] == expected_trend:
        score += 18
        reasons.append(f"H1 {expected_trend.lower()}")
        if float(h1["ema_distance"]) >= 0.20:
            score += 7
            reasons.append("H1 con buona separazione EMA")

    if m15["trend"] == expected_trend:
        score += 20
        reasons.append("M15 allineato al trend")
    elif m15["trend"] == "NEUTRO / LATERALE":
        score += 8
        reasons.append("M15 in attesa di conferma")
    else:
        reasons.append("M15 ancora in pullback")

    h1_rsi = float(h1["rsi"])
    m15_rsi = float(m15["rsi"])

    if direction == "BUY":
        if 50 <= h1_rsi <= 68:
            score += 8
            reasons.append("RSI H1 favorevole")
        if 50 <= m15_rsi <= 70:
            score += 7
            reasons.append("RSI M15 favorevole")
    else:
        if 32 <= h1_rsi <= 50:
            score += 8
            reasons.append("RSI H1 favorevole")
        if 30 <= m15_rsi <= 50:
            score += 7
            reasons.append("RSI M15 favorevole")

    atr_percent = float(h1["atr_percent"])

    if atr_percent >= 0.80:
        score += 10
        reasons.append("VolatilitÃ  H1 elevata")
    elif atr_percent >= 0.45:
        score += 7
        reasons.append("VolatilitÃ  H1 sufficiente")
    elif atr_percent >= 0.25:
        score += 3
        reasons.append("VolatilitÃ  H1 modesta")
    else:
        reasons.append("VolatilitÃ  H1 troppo bassa")

    return min(score, 100), reasons


def traffic_light(score: int, direction: str) -> tuple[str, str]:
    if direction == "NONE":
        return "ð´", "NON FARE NULLA"
    if score >= 85:
        return "ð¢", f"SETUP {direction} INTERESSANTE"
    if score >= 70:
        return "ð¡", f"PREPARATI: POSSIBILE {direction}"
    return "ð´", "NON FARE NULLA"


def format_timeframe(
    analysis: dict[str, float | str],
) -> str:
    return (
        f"{analysis['timeframe']}: {analysis['trend']}\n"
        f"Prezzo: {float(analysis['price']):.2f}\n"
        f"EMA50: {float(analysis['ema50']):.2f} | "
        f"EMA200: {float(analysis['ema200']):.2f}\n"
        f"RSI14: {float(analysis['rsi']):.1f} | "
        f"ATR14: {float(analysis['atr']):.2f} "
        f"({float(analysis['atr_percent']):.2f}%)"
    )


def build_message(
    h4: dict[str, float | str],
    h1: dict[str, float | str],
    m15: dict[str, float | str],
    direction: str,
    score: int,
    reasons: list[str],
) -> str:
    icon, action = traffic_light(score, direction)
    explanation = "\n".join(f"â¢ {reason}" for reason in reasons)

    return (
        f"{icon} BTC Trend AI v0.5\n\n"
        f"{format_timeframe(h4)}\n\n"
        f"{format_timeframe(h1)}\n\n"
        f"{format_timeframe(m15)}\n\n"
        f"SCORE SETUP: {score}/100\n"
        f"AZIONE: {action}\n\n"
        f"Motivi:\n{explanation}\n\n"
        f"Nota: lo score Ã¨ un filtro tecnico, "
        f"non una probabilitÃ  garantita di successo."
    )


async def send_telegram_alert(
    bot: Bot,
    message: str,
    alert_key: str,
) -> None:
    global last_alert_key

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    if alert_key == last_alert_key:
        return

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
    )
    last_alert_key = alert_key


async def run_analysis(
    session: aiohttp.ClientSession,
    bot: Bot,
) -> None:
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

    h4 = analyze_timeframe(h4_history[-220:], "H4")
    h1 = analyze_timeframe(h1_history[-220:], "H1")
    m15 = analyze_timeframe(m15_history[-220:], "M15")

    direction = determine_direction(h4, h1)
    score, reasons = calculate_setup_score(
        direction,
        h4,
        h1,
        m15,
    )

    message = build_message(
        h4,
        h1,
        m15,
        direction,
        score,
        reasons,
    )

    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n{now}\n{message}", flush=True)

    if score >= 70 and direction != "NONE":
        alert_key = (
            f"{direction}-"
            f"{score // 5}-"
            f"{h4['trend']}-"
            f"{h1['trend']}-"
            f"{m15['trend']}"
        )
        await send_telegram_alert(
            bot,
            message,
            alert_key,
        )


async def main() -> None:
    print("ð BTC Trend AI v0.5 avviato", flush=True)

    headers = {
        "User-Agent": "BTC-Trend-AI/0.5",
        "Accept": "application/json",
    }

    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN mancante")
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID mancante")

    bot = Bot(token=TELEGRAM_TOKEN)

    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                await run_analysis(session, bot)
            except Exception as error:
                print(
                    "Errore BTC Trend AI:",
                    repr(error),
                    flush=True,
                )

            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
