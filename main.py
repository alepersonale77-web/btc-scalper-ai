import asyncio
import os
from datetime import datetime, timedelta, timezone

import aiohttp
from telegram import Bot


# ============================================================
# BTC TREND AI v0.8.0 - TREND / SWING
# ============================================================

PRODUCT = "BTC-USD"
CHECK_INTERVAL_SECONDS = 300

EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14

ACCOUNT_CAPITAL_EUR = float(os.environ.get("ACCOUNT_CAPITAL_EUR", "115"))
FULL_RISK_PERCENT = float(os.environ.get("FULL_RISK_PERCENT", "1.0"))
YELLOW_RISK_FRACTION = float(os.environ.get("YELLOW_RISK_FRACTION", "0.25"))

TP1_R_MULTIPLE = 1.5
TP2_R_MULTIPLE = 2.5

H1_STOP_ATR_MULTIPLE = 1.50
M15_STOP_ATR_MULTIPLE = 2.80
STRUCTURE_BUFFER_ATR = 0.35
MIN_STOP_PERCENT = 0.45

MAX_LOT = float(os.environ.get("MAX_LOT", "0.02"))
GREEN_CONFIRM_BARS = int(os.environ.get("GREEN_CONFIRM_BARS", "2"))

GREEN_MIN_SCORE = 85
GREEN_MIN_QUALITY = 65
YELLOW_MIN_SCORE = 72
YELLOW_MIN_QUALITY = 55

CONTRACT_SIZE = 1.0
MARGIN_PERCENT = 2.0

EUR_PER_USD_MOVE_PER_LOT = float(
    os.environ.get("EUR_PER_USD_MOVE_PER_LOT", "0.86")
)

MIN_LOT = 0.01
LOT_STEP = 0.01

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

COINBASE_URL = (
    f"https://api.exchange.coinbase.com/products/{PRODUCT}/candles"
)

last_notified_state: str | None = None

green_candidate_direction: str | None = None
green_candidate_count = 0
green_candidate_last_m15_time: int | None = None

active_setup: dict | None = None


# =========================
# DATI DI MERCATO
# =========================

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


# =========================
# INDICATORI
# =========================

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
        atr_value = (
            (atr_value * (period - 1)) + true_range
        ) / period

    return atr_value


def calculate_adx(
    candles: list[list[float]],
    period: int = 14,
) -> float:
    if len(candles) < (period * 2) + 1:
        raise ValueError(
            f"Servono almeno {(period * 2) + 1} candele per ADX{period}"
        )

    trs: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []

    for previous, current in zip(candles[:-1], candles[1:]):
        prev_high = float(previous[2])
        prev_low = float(previous[1])
        prev_close = float(previous[4])

        high = float(current[2])
        low = float(current[1])

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm.append(
            up_move
            if up_move > down_move and up_move > 0
            else 0.0
        )
        minus_dm.append(
            down_move
            if down_move > up_move and down_move > 0
            else 0.0
        )

        trs.append(
            max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
        )

    smoothed_tr = sum(trs[:period])
    smoothed_plus = sum(plus_dm[:period])
    smoothed_minus = sum(minus_dm[:period])

    dx_values: list[float] = []

    for index in range(period, len(trs)):
        if index > period:
            smoothed_tr = (
                smoothed_tr
                - (smoothed_tr / period)
                + trs[index]
            )
            smoothed_plus = (
                smoothed_plus
                - (smoothed_plus / period)
                + plus_dm[index]
            )
            smoothed_minus = (
                smoothed_minus
                - (smoothed_minus / period)
                + minus_dm[index]
            )

        if smoothed_tr <= 0:
            continue

        plus_di = 100 * (smoothed_plus / smoothed_tr)
        minus_di = 100 * (smoothed_minus / smoothed_tr)
        denominator = plus_di + minus_di

        if denominator <= 0:
            dx = 0.0
        else:
            dx = (
                100
                * abs(plus_di - minus_di)
                / denominator
            )

        dx_values.append(dx)

    if len(dx_values) < period:
        return 0.0

    adx = sum(dx_values[:period]) / period

    for dx in dx_values[period:]:
        adx = ((adx * (period - 1)) + dx) / period

    return adx


def analyze_timeframe(
    candles: list[list[float]],
    timeframe: str,
) -> dict[str, float | int | str]:
    closes = [float(candle[4]) for candle in candles]

    price = closes[-1]
    ema50 = calculate_ema(closes, EMA_FAST_PERIOD)
    ema200 = calculate_ema(closes, EMA_SLOW_PERIOD)
    rsi = calculate_rsi(closes, RSI_PERIOD)
    atr = calculate_atr(candles, ATR_PERIOD)
    adx = calculate_adx(candles, ADX_PERIOD)

    ema_distance_percent = abs(ema50 - ema200) / ema200 * 100
    atr_percent = atr / price * 100

    distance_from_ema50_atr = (
        abs(price - ema50) / atr
        if atr > 0
        else 0.0
    )

    if ema50 > ema200 and price > ema50:
        trend = "RIALZISTA"
    elif ema50 < ema200 and price < ema50:
        trend = "RIBASSISTA"
    else:
        trend = "NEUTRO / LATERALE"

    lookback = 8 if timeframe == "H1" else 12
    recent = candles[-lookback:]

    recent_low = min(float(candle[1]) for candle in recent)
    recent_high = max(float(candle[2]) for candle in recent)

    last_candle = candles[-1]

    return {
        "timeframe": timeframe,
        "price": price,
        "ema50": ema50,
        "ema200": ema200,
        "ema_distance": ema_distance_percent,
        "rsi": rsi,
        "atr": atr,
        "atr_percent": atr_percent,
        "adx": adx,
        "distance_from_ema50_atr": distance_from_ema50_atr,
        "trend": trend,
        "recent_low": recent_low,
        "recent_high": recent_high,
        "last_candle_time": int(last_candle[0]),
        "last_low": float(last_candle[1]),
        "last_high": float(last_candle[2]),
        "last_open": float(last_candle[3]),
        "last_close": float(last_candle[4]),
    }


# =========================
# SCORE E QUALITA'
# =========================

def score_direction(
    direction: str,
    h4: dict,
    h1: dict,
    m15: dict,
) -> tuple[int, list[str], dict[str, int]]:
    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"

    score = 0
    reasons: list[str] = []
    parts: dict[str, int] = {}

    h4_points = 0

    if h4["trend"] == expected:
        h4_points += 30
        reasons.append(f"H4 {expected.lower()}")
    elif h4["trend"] == "NEUTRO / LATERALE":
        h4_points += 8
        reasons.append("H4 neutro")
    else:
        reasons.append("H4 contrario")

    h4_distance = float(h4["ema_distance"])

    if h4["trend"] == expected:
        if h4_distance >= 0.40:
            h4_points += 5
        elif h4_distance >= 0.20:
            h4_points += 3

    score += h4_points
    parts["H4"] = h4_points

    h1_points = 0

    if h1["trend"] == expected:
        h1_points += 30
        reasons.append(f"H1 {expected.lower()}")
    elif h1["trend"] == "NEUTRO / LATERALE":
        h1_points += 8
        reasons.append("H1 in attesa")
    else:
        reasons.append("H1 contrario")

    h1_distance = float(h1["ema_distance"])

    if h1["trend"] == expected:
        if h1_distance >= 0.30:
            h1_points += 5
        elif h1_distance >= 0.15:
            h1_points += 3

    h1_rsi = float(h1["rsi"])

    if direction == "BUY":
        if 50 <= h1_rsi <= 68:
            h1_points += 10
            reasons.append("RSI H1 favorevole")
    else:
        if 32 <= h1_rsi <= 50:
            h1_points += 10
            reasons.append("RSI H1 favorevole")

    score += h1_points
    parts["H1"] = h1_points

    m15_points = 0

    if m15["trend"] == expected:
        m15_points += 8
        reasons.append("M15 allineato")
    elif m15["trend"] == "NEUTRO / LATERALE":
        m15_points += 4
        reasons.append("M15 neutro")
    else:
        reasons.append("M15 in pullback/contrario")

    m15_rsi = float(m15["rsi"])

    if direction == "BUY":
        if 48 <= m15_rsi <= 68:
            m15_points += 5
    else:
        if 32 <= m15_rsi <= 52:
            m15_points += 5

    score += m15_points
    parts["M15"] = m15_points

    adx_points = 0
    h1_adx = float(h1["adx"])

    if h1["trend"] == expected:
        if h1_adx >= 28:
            adx_points = 7
        elif h1_adx >= 23:
            adx_points = 4

    score += adx_points
    parts["ADX"] = adx_points

    return min(score, 100), reasons, parts


def choose_best_direction(
    h4: dict,
    h1: dict,
    m15: dict,
) -> tuple[str, int, list[str], dict[str, int]]:
    buy_score, buy_reasons, buy_parts = score_direction(
        "BUY", h4, h1, m15
    )
    sell_score, sell_reasons, sell_parts = score_direction(
        "SELL", h4, h1, m15
    )

    if buy_score >= sell_score:
        return "BUY", buy_score, buy_reasons, buy_parts

    return "SELL", sell_score, sell_reasons, sell_parts


def market_quality(
    h4: dict,
    h1: dict,
) -> int:
    quality = 0

    h4_distance = float(h4["ema_distance"])

    if h4_distance >= 0.50:
        quality += 25
    elif h4_distance >= 0.25:
        quality += 18
    elif h4_distance >= 0.10:
        quality += 10

    h1_distance = float(h1["ema_distance"])

    if h1_distance >= 0.35:
        quality += 20
    elif h1_distance >= 0.18:
        quality += 14
    elif h1_distance >= 0.08:
        quality += 7

    atr_percent = float(h1["atr_percent"])

    if atr_percent >= 0.80:
        quality += 15
    elif atr_percent >= 0.45:
        quality += 12
    elif atr_percent >= 0.25:
        quality += 7

    if (
        h4["trend"] == h1["trend"]
        and h4["trend"] != "NEUTRO / LATERALE"
    ):
        quality += 20
    elif (
        h4["trend"] == "NEUTRO / LATERALE"
        or h1["trend"] == "NEUTRO / LATERALE"
    ):
        quality += 5

    h1_adx = float(h1["adx"])

    if h1_adx >= 30:
        quality += 15
    elif h1_adx >= 25:
        quality += 11
    elif h1_adx >= 20:
        quality += 6

    h4_adx = float(h4["adx"])

    if h4_adx >= 25:
        quality += 5
    elif h4_adx >= 20:
        quality += 3

    return min(quality, 100)


# =========================
# TRIGGER E STATO
# =========================

def m15_trigger_ok(
    direction: str,
    m15: dict,
) -> bool:
    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"

    if m15["trend"] != expected:
        return False

    rsi = float(m15["rsi"])
    extension = float(m15["distance_from_ema50_atr"])

    if extension > 1.20:
        return False

    if direction == "BUY":
        return 48 <= rsi <= 68

    return 32 <= rsi <= 52


def higher_timeframes_aligned(
    direction: str,
    h4: dict,
    h1: dict,
) -> bool:
    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"

    return (
        h4["trend"] == expected
        and h1["trend"] == expected
    )


def apply_green_persistence(
    direction: str,
    is_green_candidate: bool,
    m15: dict,
) -> tuple[bool, int]:
    global green_candidate_direction
    global green_candidate_count
    global green_candidate_last_m15_time

    candle_time = int(m15["last_candle_time"])

    if not is_green_candidate:
        green_candidate_direction = None
        green_candidate_count = 0
        green_candidate_last_m15_time = candle_time
        return False, 0

    if green_candidate_last_m15_time == candle_time:
        confirmed = (
            green_candidate_direction == direction
            and green_candidate_count >= GREEN_CONFIRM_BARS
        )
        return confirmed, green_candidate_count

    green_candidate_last_m15_time = candle_time

    if green_candidate_direction == direction:
        green_candidate_count += 1
    else:
        green_candidate_direction = direction
        green_candidate_count = 1

    confirmed = green_candidate_count >= GREEN_CONFIRM_BARS
    return confirmed, green_candidate_count


def determine_state(
    direction: str,
    score: int,
    quality: int,
    h4: dict,
    h1: dict,
    m15: dict,
) -> tuple[str, str]:
    aligned = higher_timeframes_aligned(direction, h4, h1)
    trigger_ok = m15_trigger_ok(direction, m15)

    green_candidate = (
        score >= GREEN_MIN_SCORE
        and quality >= GREEN_MIN_QUALITY
        and aligned
        and trigger_ok
    )

    green_confirmed, confirm_count = apply_green_persistence(
        direction,
        green_candidate,
        m15,
    )

    if green_confirmed:
        return "VERDE", "SETUP CONFERMATO - VALUTA L'INGRESSO"

    if green_candidate:
        return (
            "GIALLO",
            f"CONFERMA {confirm_count}/{GREEN_CONFIRM_BARS} - ATTENDERE",
        )

    too_extended = (
        float(m15["distance_from_ema50_atr"]) > 1.20
    )

    if (
        score >= YELLOW_MIN_SCORE
        and quality >= YELLOW_MIN_QUALITY
    ):
        if too_extended:
            return "GIALLO", "NON INSEGUIRE IL PREZZO"

        if not aligned:
            return (
                "GIALLO",
                "PREALLERTA - H4/H1 NON ANCORA COMPLETI",
            )

        if not trigger_ok:
            return (
                "GIALLO",
                "PREALLERTA - ATTENDERE TRIGGER M15",
            )

        return "GIALLO", "PREALLERTA - POSSIBILE INGRESSO"

    return "ROSSO", "NON ENTRARE"


# =========================
# SIZE, SL, TP E MARGINE
# =========================

def floor_to_step(value: float, step: float) -> float:
    if value <= 0:
        return 0.0

    steps = int(value / step)
    return steps * step


def calculate_structural_stop_distance(
    direction: str,
    entry: float,
    h1: dict,
    m15: dict,
) -> float:
    h1_atr = float(h1["atr"])
    m15_atr = float(m15["atr"])

    atr_distance = max(
        h1_atr * H1_STOP_ATR_MULTIPLE,
        m15_atr * M15_STOP_ATR_MULTIPLE,
    )

    min_percent_distance = (
        entry * MIN_STOP_PERCENT / 100
    )

    if direction == "BUY":
        h1_structure_stop = (
            float(h1["recent_low"])
            - (m15_atr * STRUCTURE_BUFFER_ATR)
        )
        m15_structure_stop = (
            float(m15["recent_low"])
            - (m15_atr * STRUCTURE_BUFFER_ATR)
        )

        h1_structure_distance = max(
            entry - h1_structure_stop,
            0.0,
        )
        m15_structure_distance = max(
            entry - m15_structure_stop,
            0.0,
        )

    else:
        h1_structure_stop = (
            float(h1["recent_high"])
            + (m15_atr * STRUCTURE_BUFFER_ATR)
        )
        m15_structure_stop = (
            float(m15["recent_high"])
            + (m15_atr * STRUCTURE_BUFFER_ATR)
        )

        h1_structure_distance = max(
            h1_structure_stop - entry,
            0.0,
        )
        m15_structure_distance = max(
            m15_structure_stop - entry,
            0.0,
        )

    return max(
        atr_distance,
        min_percent_distance,
        h1_structure_distance,
        m15_structure_distance,
    )


def build_trade_plan(
    direction: str,
    state: str,
    m15: dict,
    h1: dict,
) -> dict[str, float | str | bool]:
    entry = float(m15["price"])

    stop_distance = calculate_structural_stop_distance(
        direction,
        entry,
        h1,
        m15,
    )

    if direction == "BUY":
        stop_loss = entry - stop_distance
        tp1 = entry + stop_distance * TP1_R_MULTIPLE
        tp2 = entry + stop_distance * TP2_R_MULTIPLE
    else:
        stop_loss = entry + stop_distance
        tp1 = entry - stop_distance * TP1_R_MULTIPLE
        tp2 = entry - stop_distance * TP2_R_MULTIPLE

    full_risk_eur = (
        ACCOUNT_CAPITAL_EUR * FULL_RISK_PERCENT / 100
    )

    if state == "VERDE":
        target_risk_eur = full_risk_eur
        size_label = "100% del rischio previsto"
        lot_cap = MAX_LOT
    elif state == "GIALLO":
        target_risk_eur = (
            full_risk_eur * YELLOW_RISK_FRACTION
        )
        size_label = "25% del rischio previsto"
        lot_cap = max(
            MIN_LOT,
            floor_to_step(
                MAX_LOT * YELLOW_RISK_FRACTION,
                LOT_STEP,
            ),
        )
    else:
        target_risk_eur = 0.0
        size_label = "Nessuna posizione"
        lot_cap = 0.0

    raw_lot_size = 0.0

    if target_risk_eur > 0 and stop_distance > 0:
        raw_lot_size = target_risk_eur / (
            stop_distance * EUR_PER_USD_MOVE_PER_LOT
        )

    lot_size = floor_to_step(raw_lot_size, LOT_STEP)

    if lot_cap > 0:
        lot_size = min(lot_size, lot_cap)

    minimum_warning = False

    if (
        0 < raw_lot_size < MIN_LOT
        and target_risk_eur > 0
    ):
        lot_size = MIN_LOT
        minimum_warning = True

    actual_risk_eur = (
        stop_distance
        * lot_size
        * EUR_PER_USD_MOVE_PER_LOT
    )

    tp1_profit_eur = (
        actual_risk_eur * TP1_R_MULTIPLE
    )
    tp2_profit_eur = (
        actual_risk_eur * TP2_R_MULTIPLE
    )

    estimated_margin_eur = (
        entry
        * CONTRACT_SIZE
        * lot_size
        * (MARGIN_PERCENT / 100)
        * EUR_PER_USD_MOVE_PER_LOT
    )

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "stop_distance": stop_distance,
        "tp1": tp1,
        "tp2": tp2,
        "lot_size": lot_size,
        "size_label": size_label,
        "target_risk_eur": target_risk_eur,
        "actual_risk_eur": actual_risk_eur,
        "tp1_profit_eur": tp1_profit_eur,
        "tp2_profit_eur": tp2_profit_eur,
        "estimated_margin_eur": estimated_margin_eur,
        "raw_lot_size": raw_lot_size,
        "lot_cap": lot_cap,
        "minimum_warning": minimum_warning,
    }


def setup_label(
    state: str,
    score: int,
    quality: int,
) -> str:
    if (
        state == "VERDE"
        and score >= 92
        and quality >= 80
    ):
        return "SETUP ECCELLENTE - 5 STELLE"

    if (
        state == "VERDE"
        and score >= 85
        and quality >= 65
    ):
        return "SETUP OTTIMO - 4 STELLE"

    if score >= 78 and quality >= 60:
        return "SETUP BUONO - 3 STELLE"

    if score >= 72:
        return "SETUP IN PREPARAZIONE - 2 STELLE"

    return "NESSUN SETUP"


def duration_estimate(state: str) -> str:
    if state == "VERDE":
        return "Trend/Swing: indicativamente 6-48 ore"

    if state == "GIALLO":
        return "Preallerta: attendere conferma"

    return "Nessuna operazione"


# =========================
# MESSAGGI TELEGRAM
# =========================

def build_telegram_message(
    state: str,
    action: str,
    direction: str,
    score: int,
    quality: int,
    plan: dict,
) -> str:
    if state == "ROSSO":
        return (
            "[ROSSO] BTC Trend AI v0.8.0\n\n"
            "NESSUN SETUP\n\n"
            f"Direzione osservata: {direction}\n"
            f"Score tecnico: {score}/100\n"
            f"Qualita' mercato: {quality}/100\n\n"
            f"AZIONE: {action}\n\n"
            "Il bot continua a controllare il mercato."
        )

    lot_size = float(plan["lot_size"])

    warning = ""

    if bool(plan["minimum_warning"]):
        warning = (
            "\nATTENZIONE: la size teorica e' inferiore "
            "al minimo negoziabile. Il rischio reale con "
            "0.01 lotti puo' essere diverso.\n"
        )

    state_warning = (
        "Ingresso anticipato: conferme non complete."
        if state == "GIALLO"
        else (
            "Setup Trend/Swing confermato. "
            "Lo stop e' strutturale, non da scalping."
        )
    )

    return (
        f"[{state}] BTC Trend AI v0.8.0\n\n"
        f"{setup_label(state, score, quality)}\n\n"
        f"{direction}\n\n"
        f"Entrata: {float(plan['entry']):.2f}\n"
        f"Stop Loss: {float(plan['stop_loss']):.2f}\n"
        f"Distanza SL: {float(plan['stop_distance']):.2f} USD\n"
        f"Take Profit 1: {float(plan['tp1']):.2f}\n"
        f"Profitto TP1: +{float(plan['tp1_profit_eur']):.2f} EUR\n"
        f"Take Profit 2: {float(plan['tp2']):.2f}\n"
        f"Profitto TP2: +{float(plan['tp2_profit_eur']):.2f} EUR\n\n"
        f"Volume consigliato: {lot_size:.2f} lotti\n"
        f"Uso size: {plan['size_label']}\n"
        f"Perdita massima stimata: "
        f"-{float(plan['actual_risk_eur']):.2f} EUR\n"
        f"Margine richiesto stimato: "
        f"{float(plan['estimated_margin_eur']):.2f} EUR\n\n"
        f"Rischio/Rendimento TP1: 1:{TP1_R_MULTIPLE:.1f}\n"
        f"Rischio/Rendimento TP2: 1:{TP2_R_MULTIPLE:.1f}\n\n"
        f"Affidabilita' tecnica: {score}/100\n"
        f"Qualita' mercato: {quality}/100\n"
        f"Durata stimata: {duration_estimate(state)}\n\n"
        f"AZIONE CONSIGLIATA: {action}\n"
        f"{warning}"
        f"ATTENZIONE: {state_warning}\n"
        "I livelli sono indicativi e non garantiscono profitto."
    )


def build_active_setup_message(
    title: str,
    setup: dict,
    score: int,
    quality: int,
    note: str,
) -> str:
    return (
        f"{title} BTC Trend AI v0.8.0\n\n"
        f"{setup['direction']} - SETUP ATTIVO\n\n"
        f"Entrata originale: {float(setup['entry']):.2f}\n"
        f"Stop Loss: {float(setup['stop_loss']):.2f}\n"
        f"Take Profit 1: {float(setup['tp1']):.2f}\n"
        f"Take Profit 2: {float(setup['tp2']):.2f}\n\n"
        f"Score attuale: {score}/100\n"
        f"Qualita' mercato: {quality}/100\n\n"
        f"AZIONE: {note}\n\n"
        "Se non hai eseguito il VERDE iniziale, "
        "considera questo solo come monitoraggio del setup."
    )


def score_band(score: int) -> str:
    if score >= 85:
        return "85+"
    if score >= 72:
        return "72-84"
    if score >= 60:
        return "60-71"
    return "<60"


def quality_band(quality: int) -> str:
    if quality >= 80:
        return "80+"
    if quality >= 65:
        return "65-79"
    if quality >= 55:
        return "55-64"
    return "<55"


async def notify_state_change(
    bot: Bot,
    state_key: str,
    message: str,
) -> bool:
    global last_notified_state

    if state_key == last_notified_state:
        return False

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
    )

    last_notified_state = state_key
    return True


# =========================
# GESTIONE SETUP ATTIVO
# =========================

def create_active_setup(
    direction: str,
    score: int,
    quality: int,
    plan: dict,
) -> dict:
    return {
        "direction": direction,
        "entry": float(plan["entry"]),
        "stop_loss": float(plan["stop_loss"]),
        "tp1": float(plan["tp1"]),
        "tp2": float(plan["tp2"]),
        "lot_size": float(plan["lot_size"]),
        "initial_score": score,
        "initial_quality": quality,
        "tp1_hit": False,
        "status": "MANTIENI",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def manage_active_setup(
    setup: dict,
    direction_now: str,
    score: int,
    quality: int,
    h4: dict,
    h1: dict,
    m15: dict,
) -> tuple[str | None, str | None, bool]:
    direction = str(setup["direction"])

    last_low = float(m15["last_low"])
    last_high = float(m15["last_high"])

    stop_loss = float(setup["stop_loss"])
    tp1 = float(setup["tp1"])
    tp2 = float(setup["tp2"])

    if direction == "BUY":
        stop_hit = last_low <= stop_loss
        tp1_hit_now = last_high >= tp1
        tp2_hit_now = last_high >= tp2
    else:
        stop_hit = last_high >= stop_loss
        tp1_hit_now = last_low <= tp1
        tp2_hit_now = last_low <= tp2

    if stop_hit and tp1_hit_now:
        message = build_active_setup_message(
            "[ATTENZIONE]",
            setup,
            score,
            quality,
            (
                "Nella stessa candela M15 risultano toccati "
                "sia area TP sia area SL. Ordine temporale "
                "non determinabile dai dati candela."
            ),
        )
        return "ACTIVE|AMBIGUO", message, True

    if stop_hit:
        message = build_active_setup_message(
            "[ROSSO]",
            setup,
            score,
            quality,
            (
                "SETUP CHIUSO / INVALIDATO DALLO STOP. "
                "Non mediare la perdita."
            ),
        )
        return "ACTIVE|STOP", message, True

    if tp2_hit_now:
        message = build_active_setup_message(
            "[VERDE]",
            setup,
            score,
            quality,
            "TP2 RAGGIUNTO. Setup completato.",
        )
        return "ACTIVE|TP2", message, True

    if tp1_hit_now and not bool(setup["tp1_hit"]):
        setup["tp1_hit"] = True
        setup["status"] = "TP1"

        message = build_active_setup_message(
            "[VERDE]",
            setup,
            score,
            quality,
            (
                "TP1 RAGGIUNTO. Valuta protezione del trade "
                "e lascia lavorare l'eventuale parte residua."
            ),
        )

        return "ACTIVE|TP1", message, False

    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"
    opposite = "RIBASSISTA" if direction == "BUY" else "RIALZISTA"

    hard_invalidated = (
        h4["trend"] == opposite
        or (
            h1["trend"] == opposite
            and score < 55
        )
    )

    if hard_invalidated:
        setup["status"] = "INVALIDATO"

        message = build_active_setup_message(
            "[ROSSO]",
            setup,
            score,
            quality,
            (
                "STRUTTURA MULTIORARIA INVALIDATA. "
                "Valuta uscita: non e' un semplice pullback M15."
            ),
        )

        return "ACTIVE|INVALIDATO", message, True

    deteriorated = (
        score < 70
        or quality < 55
        or direction_now != direction
        or m15["trend"] == opposite
    )

    if deteriorated:
        if setup["status"] != "ATTENZIONE":
            setup["status"] = "ATTENZIONE"

            message = build_active_setup_message(
                "[GIALLO]",
                setup,
                score,
                quality,
                (
                    "ATTENZIONE - FORZA RIDOTTA. "
                    "NON AGGIUNGERE SIZE. "
                    "Il setup non e' ancora invalidato "
                    "su H4/H1."
                ),
            )

            return "ACTIVE|ATTENZIONE", message, False

        return None, None, False

    if (
        setup["status"] == "ATTENZIONE"
        and h4["trend"] == expected
        and h1["trend"] == expected
        and score >= 80
        and quality >= 65
    ):
        setup["status"] = "MANTIENI"

        message = build_active_setup_message(
            "[VERDE]",
            setup,
            score,
            quality,
            (
                "CONFERME RECUPERATE - MANTIENI. "
                "NON AGGIUNGERE una nuova posizione "
                "solo perche' il verde e' tornato."
            ),
        )

        return "ACTIVE|RECUPERO", message, False

    return None, None, False


# =========================
# CICLO PRINCIPALE
# =========================

async def run_analysis(
    session: aiohttp.ClientSession,
    bot: Bot,
) -> None:
    global active_setup
    global last_notified_state

    h1_history = await fetch_candles(
        session=session,
        granularity=3600,
        required_count=830,
    )

    m15_history = await fetch_candles(
        session=session,
        granularity=900,
        required_count=240,
    )

    h4_history = aggregate_h1_to_h4(h1_history)

    if len(h4_history) < EMA_SLOW_PERIOD:
        raise RuntimeError(
            f"Candele H4 insufficienti: {len(h4_history)}"
        )

    h4 = analyze_timeframe(h4_history[-220:], "H4")
    h1 = analyze_timeframe(h1_history[-220:], "H1")
    m15 = analyze_timeframe(m15_history[-220:], "M15")

    (
        direction,
        score,
        reasons,
        score_parts,
    ) = choose_best_direction(
        h4,
        h1,
        m15,
    )

    quality = market_quality(h4, h1)

    now = datetime.now().strftime("%H:%M:%S")

    print(
        "\n"
        f"{now} DEBUG v0.8.0\n"
        f"Direzione: {direction}\n"
        f"Score: {score}/100 "
        f"(H4={score_parts.get('H4', 0)}, "
        f"H1={score_parts.get('H1', 0)}, "
        f"M15={score_parts.get('M15', 0)}, "
        f"ADX={score_parts.get('ADX', 0)})\n"
        f"Qualita': {quality}/100\n"
        f"H4 trend={h4['trend']} "
        f"ADX={float(h4['adx']):.1f}\n"
        f"H1 trend={h1['trend']} "
        f"RSI={float(h1['rsi']):.1f} "
        f"ADX={float(h1['adx']):.1f}\n"
        f"M15 trend={m15['trend']} "
        f"RSI={float(m15['rsi']):.1f} "
        f"distEMA50={float(m15['distance_from_ema50_atr']):.2f} ATR\n"
        f"Motivi: {', '.join(reasons)}",
        flush=True,
    )

    if active_setup is not None:
        (
            management_key,
            management_message,
            close_setup,
        ) = manage_active_setup(
            active_setup,
            direction,
            score,
            quality,
            h4,
            h1,
            m15,
        )

        if (
            management_key is not None
            and management_message is not None
        ):
            print(
                f"\n{management_message}",
                flush=True,
            )

            await notify_state_change(
                bot,
                management_key,
                management_message,
            )

        if close_setup:
            active_setup = None
            last_notified_state = None

        return

    state, action = determine_state(
        direction,
        score,
        quality,
        h4,
        h1,
        m15,
    )

    plan = build_trade_plan(
        direction,
        state,
        m15,
        h1,
    )

    message = build_telegram_message(
        state,
        action,
        direction,
        score,
        quality,
        plan,
    )

    print(
        f"\n{now}\n{message}",
        flush=True,
    )

    state_key = (
        f"{state}|{direction}|"
        f"{score_band(score)}|"
        f"{quality_band(quality)}"
    )

    if (
        state == "GIALLO"
        and action.startswith("CONFERMA")
    ):
        state_key += f"|{green_candidate_count}"

    sent = await notify_state_change(
        bot,
        state_key,
        message,
    )

    if state == "VERDE":
        active_setup = create_active_setup(
            direction,
            score,
            quality,
            plan,
        )

        if sent:
            print(
                "SETUP ATTIVO creato: "
                f"{direction} @ "
                f"{float(plan['entry']):.2f}",
                flush=True,
            )


async def main() -> None:
    print(
        "BTC Trend AI v0.8.0 Trend/Swing avviato",
        flush=True,
    )

    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN mancante")

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID mancante")

    headers = {
        "User-Agent": "BTC-Trend-AI/0.8.0",
        "Accept": "application/json",
    }

    bot = Bot(token=TELEGRAM_TOKEN)

    async with aiohttp.ClientSession(
        headers=headers
    ) as session:
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
