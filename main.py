import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
from telegram import Bot


# ============================================================
# BTC TREND AI v0.9.10 - DUAL: TREND / SWING + SCALP
# CONFIGURAZIONE FUTURA:
# - FP Markets capitale predefinito: 475 EUR
# - rischio reale massimo per singolo setup: 6.25%
# - nuovi ingressi separati dalla gestione posizioni gia' aperte
# - canale TREND MOMENTUM attivo
# MODIFICA v0.9.10:
# - nuovo canale TREND MOMENTUM PULLBACK
# - H4/H1 decidono il trend, M15 conferma, M5 cerca il rientro
# - stop pullback su struttura M5/M15, senza usare sempre lo stop H1 largo
# - se il pullback non offre uno stop tecnico valido il bot aspetta
# - rischio massimo 6.25% invariato
# - TREND: canale anticipato anche con score 85-87 se qualita' >= 90
# - TREND: setup eccezionale 88+ resta prioritario
# - TREND/SCALP: blocco se il rischio reale della size minima e' eccessivo
# - TREND/SCALP: filtri anti-inseguimento e stop massimo restano attivi
# ============================================================

PRODUCT = "BTC-USD"
CHECK_INTERVAL_SECONDS = 300

EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14

ACCOUNT_CAPITAL_EUR = float(os.environ.get("ACCOUNT_CAPITAL_EUR", "475"))
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

# Setup TREND eccezionale:
# quando H4/H1 sono allineati, M15 ha trigger valido e score/qualita'
# sono molto alti, non aspettiamo obbligatoriamente 2 conferme M15.
EXCEPTIONAL_MIN_SCORE = int(os.environ.get("EXCEPTIONAL_MIN_SCORE", "88"))
EXCEPTIONAL_MIN_QUALITY = int(os.environ.get("EXCEPTIONAL_MIN_QUALITY", "85"))
EXCEPTIONAL_MAX_M15_EXTENSION_ATR = float(
    os.environ.get("EXCEPTIONAL_MAX_M15_EXTENSION_ATR", "1.05")
)

EARLY_ENTRY_MIN_SCORE = int(os.environ.get("EARLY_ENTRY_MIN_SCORE", "85"))
EARLY_ENTRY_MAX_SCORE = int(os.environ.get("EARLY_ENTRY_MAX_SCORE", "87"))
EARLY_ENTRY_MIN_QUALITY = int(os.environ.get("EARLY_ENTRY_MIN_QUALITY", "90"))
EARLY_ENTRY_MAX_M15_EXTENSION_ATR = float(
    os.environ.get("EARLY_ENTRY_MAX_M15_EXTENSION_ATR", "0.90")
)

# Canale TREND MOMENTUM v0.9.10
MOMENTUM_MIN_SCORE = int(os.environ.get("MOMENTUM_MIN_SCORE", "82"))
MOMENTUM_MIN_QUALITY = int(os.environ.get("MOMENTUM_MIN_QUALITY", "90"))
MOMENTUM_MIN_H1_ADX = float(os.environ.get("MOMENTUM_MIN_H1_ADX", "24"))
MOMENTUM_MIN_M15_ADX = float(os.environ.get("MOMENTUM_MIN_M15_ADX", "18"))
MOMENTUM_MAX_M15_EXTENSION_ATR = float(
    os.environ.get("MOMENTUM_MAX_M15_EXTENSION_ATR", "1.35")
)
MOMENTUM_PULLBACK_MAX_EXTENSION_ATR = float(
    os.environ.get("MOMENTUM_PULLBACK_MAX_EXTENSION_ATR", "0.75")
)

# =========================
# TREND MOMENTUM PULLBACK v0.9.10
# =========================
TREND_PULLBACK_ENABLED = os.environ.get("TREND_PULLBACK_ENABLED", "1") == "1"
TREND_PULLBACK_MIN_SCORE = int(
    os.environ.get("TREND_PULLBACK_MIN_SCORE", "82")
)
TREND_PULLBACK_MIN_QUALITY = int(
    os.environ.get("TREND_PULLBACK_MIN_QUALITY", "90")
)
TREND_PULLBACK_MAX_M15_EXTENSION_ATR = float(
    os.environ.get("TREND_PULLBACK_MAX_M15_EXTENSION_ATR", "1.20")
)
TREND_PULLBACK_M5_STOP_ATR_MULTIPLE = float(
    os.environ.get("TREND_PULLBACK_M5_STOP_ATR_MULTIPLE", "1.80")
)
TREND_PULLBACK_M15_STOP_ATR_MULTIPLE = float(
    os.environ.get("TREND_PULLBACK_M15_STOP_ATR_MULTIPLE", "0.65")
)
TREND_PULLBACK_STRUCTURE_BUFFER_ATR = float(
    os.environ.get("TREND_PULLBACK_STRUCTURE_BUFFER_ATR", "0.25")
)
TREND_PULLBACK_MIN_STOP_PERCENT = float(
    os.environ.get("TREND_PULLBACK_MIN_STOP_PERCENT", "0.25")
)
TREND_PULLBACK_MAX_STOP_USD = float(
    os.environ.get("TREND_PULLBACK_MAX_STOP_USD", "2000")
)
TREND_PULLBACK_MAX_STOP_PERCENT = float(
    os.environ.get("TREND_PULLBACK_MAX_STOP_PERCENT", "2.50")
)
TREND_PULLBACK_TP1_R = float(
    os.environ.get("TREND_PULLBACK_TP1_R", "1.50")
)
TREND_PULLBACK_TP2_R = float(
    os.environ.get("TREND_PULLBACK_TP2_R", "2.50")
)

MAX_REAL_RISK_PERCENT = float(
    os.environ.get("MAX_REAL_RISK_PERCENT", "6.25")
)

# =========================
# MODALITA' SCALP v0.9.10
# =========================
SCALP_ENABLED = os.environ.get("SCALP_ENABLED", "1") == "1"
SCALP_GREEN_MIN_SCORE = int(os.environ.get("SCALP_GREEN_MIN_SCORE", "78"))
SCALP_MIN_QUALITY = int(os.environ.get("SCALP_MIN_QUALITY", "55"))
SCALP_CONFIRM_BARS = int(os.environ.get("SCALP_CONFIRM_BARS", "2"))
SCALP_COOLDOWN_MINUTES = int(os.environ.get("SCALP_COOLDOWN_MINUTES", "60"))
SCALP_MAX_SIGNALS_PER_DAY = int(os.environ.get("SCALP_MAX_SIGNALS_PER_DAY", "5"))
SCALP_RISK_PERCENT = float(os.environ.get("SCALP_RISK_PERCENT", "0.50"))

# Stop dinamico scalp
SCALP_STOP_ATR_MULTIPLE = float(os.environ.get("SCALP_STOP_ATR_MULTIPLE", "1.80"))
SCALP_M15_STOP_ATR_MULTIPLE = float(
    os.environ.get("SCALP_M15_STOP_ATR_MULTIPLE", "0.90")
)
SCALP_STRUCTURE_BUFFER_ATR = float(
    os.environ.get("SCALP_STRUCTURE_BUFFER_ATR", "0.25")
)
SCALP_MIN_STOP_PERCENT = float(
    os.environ.get("SCALP_MIN_STOP_PERCENT", "0.25")
)

# Limite massimo per uno scalp: se la struttura richiede uno stop piu' largo,
# il setup viene rifiutato invece di trasformarsi in un quasi-swing.
SCALP_MAX_STOP_USD = float(
    os.environ.get("SCALP_MAX_STOP_USD", "700")
)
SCALP_MAX_STOP_PERCENT = float(
    os.environ.get("SCALP_MAX_STOP_PERCENT", "0.90")
)

# Anti-inseguimento: niente nuovo scalp se M15 e' gia' troppo esteso
# rispetto alla EMA50. Dopo un pullback il setup puo' tornare valido.
SCALP_MAX_M15_EXTENSION_ATR = float(
    os.environ.get("SCALP_MAX_M15_EXTENSION_ATR", "1.35")
)

# Target scalp
SCALP_TP1_R = float(os.environ.get("SCALP_TP1_R", "1.30"))
SCALP_TP2_R = float(os.environ.get("SCALP_TP2_R", "2.00"))

# Breakout M15
SCALP_BREAKOUT_BUFFER_ATR = float(
    os.environ.get("SCALP_BREAKOUT_BUFFER_ATR", "0.10")
)

# Spazio minimo verso supporto/resistenza H1:
# se una barriera H1 e' piu' vicina di questo multiplo del rischio,
# il trade viene bloccato.
SCALP_MIN_ROOM_R = float(os.environ.get("SCALP_MIN_ROOM_R", "1.20"))

SCALP_PROTECT_AT_R = float(os.environ.get("SCALP_PROTECT_AT_R", "0.80"))
SCALP_TRACK_MAX_HOURS = float(os.environ.get("SCALP_TRACK_MAX_HOURS", "4"))
SCALP_JOURNAL_FILE = os.environ.get(
    "SCALP_JOURNAL_FILE",
    "/tmp/btc_scalp_journal.jsonl",
)
TREND_RED_MIN_NOTIFY_MINUTES = int(
    os.environ.get("TREND_RED_MIN_NOTIFY_MINUTES", "240")
)

CONTRACT_SIZE = 1.0
MARGIN_PERCENT = 2.0

EUR_PER_USD_MOVE_PER_LOT = float(
    os.environ.get("EUR_PER_USD_MOVE_PER_LOT", "0.86")
)

# =========================
# BROKER WEEKDAY / WEEKEND
# =========================

FP_BROKER_NAME = "FP Markets / MT4"
FP_MIN_LOT = 0.01
FP_MAX_LOT = float(os.environ.get("FP_MAX_LOT", "0.02"))
FP_LOT_STEP = 0.01
FP_CONTRACT_SIZE = 1.0
FP_MARGIN_PERCENT = float(os.environ.get("FP_MARGIN_PERCENT", "2.0"))

PEPPER_BROKER_NAME = "Pepperstone / MT5"
PEPPER_MIN_LOT = 0.01
PEPPER_MAX_LOT = float(os.environ.get("PEPPER_MAX_LOT", "0.01"))
PEPPER_LOT_STEP = 0.01
PEPPER_CONTRACT_SIZE = 1.0
PEPPER_MARGIN_EUR_PER_LOT = float(
    os.environ.get("PEPPER_MARGIN_EUR_PER_LOT", "27232")
)

FP_ACCOUNT_CAPITAL_EUR = float(
    os.environ.get("FP_ACCOUNT_CAPITAL_EUR", "475")
)
PEPPER_ACCOUNT_CAPITAL_EUR = float(
    os.environ.get("PEPPER_ACCOUNT_CAPITAL_EUR", "500")
)
MIN_FREE_MARGIN_BUFFER_EUR = float(
    os.environ.get("MIN_FREE_MARGIN_BUFFER_EUR", "100")
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

scalp_candidate_direction: str | None = None
scalp_candidate_count = 0
scalp_candidate_last_m5_time: int | None = None
scalp_last_signal_time: datetime | None = None
scalp_signal_day: str | None = None
scalp_signals_today = 0
last_scalp_status_key: str | None = None

active_scalp_setup: dict | None = None
last_trend_red_sent_at: datetime | None = None


def active_broker_profile(now_utc: datetime | None = None) -> dict[str, float | str]:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    weekday = now_utc.weekday()
    if weekday <= 4:
        return {
            "name": FP_BROKER_NAME,
            "mode": "FP",
            "capital_eur": FP_ACCOUNT_CAPITAL_EUR,
            "min_lot": FP_MIN_LOT,
            "max_lot": FP_MAX_LOT,
            "lot_step": FP_LOT_STEP,
            "contract_size": FP_CONTRACT_SIZE,
            "margin_percent": FP_MARGIN_PERCENT,
        }
    return {
        "name": PEPPER_BROKER_NAME,
        "mode": "PEPPER",
        "capital_eur": PEPPER_ACCOUNT_CAPITAL_EUR,
        "min_lot": PEPPER_MIN_LOT,
        "max_lot": PEPPER_MAX_LOT,
        "lot_step": PEPPER_LOT_STEP,
        "contract_size": PEPPER_CONTRACT_SIZE,
        "margin_eur_per_lot": PEPPER_MARGIN_EUR_PER_LOT,
    }


def estimate_margin_eur(entry: float, lot_size: float, broker: dict) -> float:
    if lot_size <= 0:
        return 0.0
    if broker["mode"] == "PEPPER":
        return float(broker["margin_eur_per_lot"]) * lot_size
    return (
        entry
        * float(broker["contract_size"])
        * lot_size
        * (float(broker["margin_percent"]) / 100)
        * EUR_PER_USD_MOVE_PER_LOT
    )


def is_trade_executable(estimated_margin_eur: float, broker: dict) -> tuple[bool, float]:
    capital = float(broker["capital_eur"])
    remaining = capital - estimated_margin_eur
    executable = (
        estimated_margin_eur <= capital
        and remaining >= MIN_FREE_MARGIN_BUFFER_EUR
    )
    return executable, remaining


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

    # Livelli precedenti, ESCLUDENDO l'ultima candela chiusa.
    # Servono per sapere se l'ultima candela ha davvero rotto una struttura,
    # invece di confrontarla con un massimo/minimo che contiene se stessa.
    previous_recent = candles[-(lookback + 1):-1]
    previous_recent_low = min(float(candle[1]) for candle in previous_recent)
    previous_recent_high = max(float(candle[2]) for candle in previous_recent)

    # Micro-struttura per gli ingressi pullback M5:
    # 6 candele = circa 30 minuti su M5.
    micro_lookback = 6
    micro_recent = candles[-micro_lookback:]
    micro_recent_low = min(float(candle[1]) for candle in micro_recent)
    micro_recent_high = max(float(candle[2]) for candle in micro_recent)

    micro_previous = candles[-(micro_lookback + 1):-1]
    micro_previous_low = min(float(candle[1]) for candle in micro_previous)
    micro_previous_high = max(float(candle[2]) for candle in micro_previous)

    last_candle = candles[-1]
    previous_candle = candles[-2]

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
        "previous_recent_low": previous_recent_low,
        "previous_recent_high": previous_recent_high,
        "micro_recent_low": micro_recent_low,
        "micro_recent_high": micro_recent_high,
        "micro_previous_low": micro_previous_low,
        "micro_previous_high": micro_previous_high,
        "prev_low": float(previous_candle[1]),
        "prev_high": float(previous_candle[2]),
        "prev_open": float(previous_candle[3]),
        "prev_close": float(previous_candle[4]),
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
# TRIGGER E STATO TREND
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
    extension = float(m15["distance_from_ema50_atr"])
    h1_adx = float(h1["adx"])
    m15_adx = float(m15["adx"])

    exceptional_candidate = (
        score >= EXCEPTIONAL_MIN_SCORE
        and quality >= EXCEPTIONAL_MIN_QUALITY
        and aligned
        and trigger_ok
    )
    if exceptional_candidate:
        if extension <= EXCEPTIONAL_MAX_M15_EXTENSION_ATR:
            global green_candidate_direction, green_candidate_count, green_candidate_last_m15_time
            green_candidate_direction = None
            green_candidate_count = 0
            green_candidate_last_m15_time = int(m15["last_candle_time"])
            return "VERDE", "SETUP ECCEZIONALE - VALUTA INGRESSO ANTICIPATO"
        return "GIALLO", "SETUP ECCEZIONALE MA PREZZO ESTESO - ATTENDERE PULLBACK"

    momentum_candidate = (
        score >= MOMENTUM_MIN_SCORE
        and quality >= MOMENTUM_MIN_QUALITY
        and aligned
        and h1_adx >= MOMENTUM_MIN_H1_ADX
        and m15_adx >= MOMENTUM_MIN_M15_ADX
        and str(m15["trend"]) == ("RIALZISTA" if direction == "BUY" else "RIBASSISTA")
    )
    if momentum_candidate:
        if extension <= MOMENTUM_PULLBACK_MAX_EXTENSION_ATR:
            return "VERDE", "TREND MOMENTUM - PULLBACK FAVOREVOLE, VALUTA INGRESSO"
        if extension <= MOMENTUM_MAX_M15_EXTENSION_ATR:
            return "VERDE", "TREND MOMENTUM - VALUTA INGRESSO SENZA INSEGUIRE"
        return "GIALLO", "TREND MOMENTUM FORTE MA PREZZO TROPPO ESTESO - ATTENDERE PULLBACK"

    early_candidate = (
        EARLY_ENTRY_MIN_SCORE <= score <= EARLY_ENTRY_MAX_SCORE
        and quality >= EARLY_ENTRY_MIN_QUALITY
        and aligned
        and trigger_ok
    )
    if early_candidate:
        if extension <= EARLY_ENTRY_MAX_M15_EXTENSION_ATR:
            green_candidate_direction = None
            green_candidate_count = 0
            green_candidate_last_m15_time = int(m15["last_candle_time"])
            return "VERDE", "SETUP FORTE - INGRESSO ANTICIPATO CON RISCHIO CONTROLLATO"
        return "GIALLO", "SETUP FORTE MA PREZZO ESTESO - ATTENDERE PULLBACK"

    green_candidate = (
        score >= GREEN_MIN_SCORE
        and quality >= GREEN_MIN_QUALITY
        and aligned
        and trigger_ok
    )
    green_confirmed, confirm_count = apply_green_persistence(direction, green_candidate, m15)
    if green_confirmed:
        return "VERDE", "SETUP CONFERMATO - VALUTA L'INGRESSO"
    if green_candidate:
        return "GIALLO", f"CONFERMA {confirm_count}/{GREEN_CONFIRM_BARS} - ATTENDERE"

    if score >= YELLOW_MIN_SCORE and quality >= YELLOW_MIN_QUALITY:
        if extension > 1.20:
            return "GIALLO", "NON INSEGUIRE IL PREZZO"
        if not aligned:
            return "GIALLO", "PREALLERTA - H4/H1 NON ANCORA COMPLETI"
        if not trigger_ok:
            return "GIALLO", "PREALLERTA - ATTENDERE TRIGGER M15"
        return "GIALLO", "PREALLERTA - POSSIBILE INGRESSO"
    return "ROSSO", "NON ENTRARE"

def trend_label_from_higher_timeframes(
    h4: dict,
    h1: dict,
) -> str:
    h4_trend = str(h4["trend"])
    h1_trend = str(h1["trend"])

    if h4_trend == "RIALZISTA" and h1_trend == "RIALZISTA":
        return "BUY"
    if h4_trend == "RIBASSISTA" and h1_trend == "RIBASSISTA":
        return "SELL"
    if h4_trend == "NEUTRO / LATERALE" and h1_trend == "NEUTRO / LATERALE":
        return "NEUTRO"
    return "TRANSIZIONE"


def m15_momentum_label(m15: dict) -> str:
    trend = str(m15["trend"])
    rsi = float(m15["rsi"])

    if trend == "RIALZISTA":
        return "RIALZISTA FORTE" if rsi >= 58 else "RIALZISTA"
    if trend == "RIBASSISTA":
        return "RIBASSISTA FORTE" if rsi <= 42 else "RIBASSISTA"
    return "NEUTRO / LATERALE"


def market_phase_label(
    direction: str,
    h4: dict,
    h1: dict,
    m15: dict,
) -> str:
    trend_background = trend_label_from_higher_timeframes(h4, h1)
    m15_trend = str(m15["trend"])

    if trend_background == "TRANSIZIONE":
        return "TRANSIZIONE - ATTENDERE CHIAREZZA"
    if trend_background == "NEUTRO":
        return "LATERALE - NESSUN VANTAGGIO CHIARO"

    expected = "RIALZISTA" if trend_background == "BUY" else "RIBASSISTA"
    opposite = "RIBASSISTA" if expected == "RIALZISTA" else "RIALZISTA"

    if m15_trend == expected:
        return "MOMENTUM ALLINEATO AL TREND"

    if m15_trend == opposite:
        if trend_background == "BUY":
            return "PULLBACK RIBASSISTA CONTRO TREND BUY"
        return "RIMBALZO RIALZISTA CONTRO TREND SELL"

    if direction != trend_background:
        return "SEGNALE TECNICO IN CONFLITTO COL TREND DI FONDO"

    return "M15 IN ATTESA DI TRIGGER"



# =========================
# TREND MOMENTUM PULLBACK v0.9.10
# =========================

def trend_pullback_context_ok(
    direction: str,
    score: int,
    quality: int,
    h4: dict,
    h1: dict,
    m15: dict,
) -> tuple[bool, str]:
    if not TREND_PULLBACK_ENABLED:
        return False, "canale pullback disattivato"

    if score < TREND_PULLBACK_MIN_SCORE:
        return False, f"score {score} sotto {TREND_PULLBACK_MIN_SCORE}"

    if quality < TREND_PULLBACK_MIN_QUALITY:
        return False, f"qualita' {quality} sotto {TREND_PULLBACK_MIN_QUALITY}"

    if not higher_timeframes_aligned(direction, h4, h1):
        return False, "H4/H1 non allineati"

    extension = float(m15["distance_from_ema50_atr"])
    if extension > TREND_PULLBACK_MAX_M15_EXTENSION_ATR:
        return (
            False,
            f"M15 ancora troppo esteso: {extension:.2f} ATR "
            f"(max {TREND_PULLBACK_MAX_M15_EXTENSION_ATR:.2f})",
        )

    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"
    if str(m15["trend"]) != expected:
        return False, "M15 non ancora riallineato al trend"

    return True, "contesto pullback valido"


def trend_pullback_m5_trigger_ok(
    direction: str,
    m5: dict,
) -> tuple[bool, str]:
    """
    Cerca un nuovo impulso M5 DOPO il pullback.
    La candela usata e' gia' chiusa.
    Non richiede breakout M15: quello sarebbe spesso troppo tardivo.
    """
    close = float(m5["last_close"])
    open_ = float(m5["last_open"])
    ema50 = float(m5["ema50"])
    rsi = float(m5["rsi"])
    prev_high = float(m5["prev_high"])
    prev_low = float(m5["prev_low"])

    if direction == "BUY":
        candle_ok = close > open_
        ema_ok = close > ema50
        rsi_ok = 48 <= rsi <= 72
        impulse_ok = close > prev_high

        if candle_ok and ema_ok and rsi_ok and impulse_ok:
            return True, (
                f"M5 BUY confermato: close {close:.2f} > "
                f"massimo precedente {prev_high:.2f}"
            )

        missing = []
        if not candle_ok:
            missing.append("candela M5 non rialzista")
        if not ema_ok:
            missing.append("close M5 sotto EMA50")
        if not rsi_ok:
            missing.append(f"RSI M5 {rsi:.1f} fuori range")
        if not impulse_ok:
            missing.append(f"serve close M5 > {prev_high:.2f}")
        return False, ", ".join(missing)

    candle_ok = close < open_
    ema_ok = close < ema50
    rsi_ok = 28 <= rsi <= 52
    impulse_ok = close < prev_low

    if candle_ok and ema_ok and rsi_ok and impulse_ok:
        return True, (
            f"M5 SELL confermato: close {close:.2f} < "
            f"minimo precedente {prev_low:.2f}"
        )

    missing = []
    if not candle_ok:
        missing.append("candela M5 non ribassista")
    if not ema_ok:
        missing.append("close M5 sopra EMA50")
    if not rsi_ok:
        missing.append(f"RSI M5 {rsi:.1f} fuori range")
    if not impulse_ok:
        missing.append(f"serve close M5 < {prev_low:.2f}")
    return False, ", ".join(missing)


def calculate_trend_pullback_stop_distance(
    direction: str,
    entry: float,
    m15: dict,
    m5: dict,
) -> float:
    """
    Stop tecnico del pullback:
    - ATR M5
    - quota minima di ATR M15
    - micro-struttura delle ultime ~30 min
    - distanza percentuale minima

    NON usa la struttura H1, per evitare stop da 4.000-5.000 USD
    su un rientro che nasce da M5/M15.
    """
    m5_atr = float(m5["atr"])
    m15_atr = float(m15["atr"])

    atr_distance = max(
        m5_atr * TREND_PULLBACK_M5_STOP_ATR_MULTIPLE,
        m15_atr * TREND_PULLBACK_M15_STOP_ATR_MULTIPLE,
    )

    min_percent_distance = (
        entry * TREND_PULLBACK_MIN_STOP_PERCENT / 100
    )

    if direction == "BUY":
        structure_stop = (
            float(m5["micro_previous_low"])
            - m5_atr * TREND_PULLBACK_STRUCTURE_BUFFER_ATR
        )
        structure_distance = max(entry - structure_stop, 0.0)
    else:
        structure_stop = (
            float(m5["micro_previous_high"])
            + m5_atr * TREND_PULLBACK_STRUCTURE_BUFFER_ATR
        )
        structure_distance = max(structure_stop - entry, 0.0)

    return max(
        atr_distance,
        min_percent_distance,
        structure_distance,
    )


def trend_pullback_stop_ok(
    entry: float,
    stop_distance: float,
) -> tuple[bool, str]:
    percent_cap = entry * TREND_PULLBACK_MAX_STOP_PERCENT / 100
    max_allowed = min(
        TREND_PULLBACK_MAX_STOP_USD,
        percent_cap,
    )

    if stop_distance <= max_allowed:
        return (
            True,
            f"stop pullback valido: {stop_distance:.2f} USD "
            f"(max {max_allowed:.2f})",
        )

    return (
        False,
        f"stop pullback ancora troppo largo: {stop_distance:.2f} USD "
        f"(max {max_allowed:.2f}) - attendere nuova struttura",
    )


def build_trend_pullback_plan(
    direction: str,
    m15: dict,
    m5: dict,
) -> dict[str, float | str | bool]:
    broker = active_broker_profile()
    entry = float(m5["price"])

    stop_distance = calculate_trend_pullback_stop_distance(
        direction,
        entry,
        m15,
        m5,
    )
    stop_valid, stop_reason = trend_pullback_stop_ok(
        entry,
        stop_distance,
    )

    if direction == "BUY":
        stop_loss = entry - stop_distance
        tp1 = entry + stop_distance * TREND_PULLBACK_TP1_R
        tp2 = entry + stop_distance * TREND_PULLBACK_TP2_R
    else:
        stop_loss = entry + stop_distance
        tp1 = entry - stop_distance * TREND_PULLBACK_TP1_R
        tp2 = entry - stop_distance * TREND_PULLBACK_TP2_R

    broker_capital = float(broker["capital_eur"])
    target_risk_eur = broker_capital * FULL_RISK_PERCENT / 100

    raw_lot_size = 0.0
    if stop_distance > 0:
        raw_lot_size = target_risk_eur / (
            stop_distance * EUR_PER_USD_MOVE_PER_LOT
        )

    lot_size = floor_to_step(
        raw_lot_size,
        float(broker["lot_step"]),
    )
    lot_size = min(
        lot_size,
        float(broker["max_lot"]),
    )

    minimum_warning = False
    if 0 < raw_lot_size < float(broker["min_lot"]):
        lot_size = float(broker["min_lot"])
        minimum_warning = True

    actual_risk_eur = (
        stop_distance
        * lot_size
        * EUR_PER_USD_MOVE_PER_LOT
    )

    estimated_margin_eur = estimate_margin_eur(
        entry,
        lot_size,
        broker,
    )
    margin_executable, remaining_after_margin = is_trade_executable(
        estimated_margin_eur,
        broker,
    )

    max_real_risk_eur = (
        broker_capital * MAX_REAL_RISK_PERCENT / 100
    )
    risk_executable = (
        actual_risk_eur <= max_real_risk_eur
        if lot_size > 0
        else True
    )

    executable = (
        stop_valid
        and margin_executable
        and risk_executable
        and lot_size > 0
    )

    return {
        "broker_name": str(broker["name"]),
        "broker_mode": str(broker["mode"]),
        "broker_capital_eur": broker_capital,
        "entry": entry,
        "stop_loss": stop_loss,
        "stop_distance": stop_distance,
        "tp1": tp1,
        "tp2": tp2,
        "lot_size": lot_size,
        "size_label": "Pullback M5/M15 - rischio controllato",
        "target_risk_eur": target_risk_eur,
        "actual_risk_eur": actual_risk_eur,
        "tp1_profit_eur": actual_risk_eur * TREND_PULLBACK_TP1_R,
        "tp2_profit_eur": actual_risk_eur * TREND_PULLBACK_TP2_R,
        "estimated_margin_eur": estimated_margin_eur,
        "remaining_after_margin_eur": remaining_after_margin,
        "trade_executable": executable,
        "margin_executable": margin_executable,
        "risk_executable": risk_executable,
        "max_real_risk_eur": max_real_risk_eur,
        "raw_lot_size": raw_lot_size,
        "lot_cap": float(broker["max_lot"]),
        "minimum_warning": minimum_warning,
        "plan_mode": "PULLBACK",
        "pullback_stop_valid": stop_valid,
        "pullback_stop_reason": stop_reason,
    }


def evaluate_trend_pullback(
    direction: str,
    score: int,
    quality: int,
    h4: dict,
    h1: dict,
    m15: dict,
    m5: dict,
) -> tuple[bool, str, dict | None]:
    context_ok, context_reason = trend_pullback_context_ok(
        direction,
        score,
        quality,
        h4,
        h1,
        m15,
    )
    if not context_ok:
        return False, context_reason, None

    trigger_ok, trigger_reason = trend_pullback_m5_trigger_ok(
        direction,
        m5,
    )
    if not trigger_ok:
        return (
            False,
            f"contesto forte, attendere trigger pullback M5: {trigger_reason}",
            None,
        )

    plan = build_trend_pullback_plan(
        direction,
        m15,
        m5,
    )

    if not bool(plan["pullback_stop_valid"]):
        return (
            False,
            str(plan["pullback_stop_reason"]),
            plan,
        )

    if not bool(plan["risk_executable"]):
        return (
            False,
            f"pullback trovato ma rischio {float(plan['actual_risk_eur']):.2f} EUR "
            f"> max {float(plan['max_real_risk_eur']):.2f} EUR",
            plan,
        )

    if not bool(plan["margin_executable"]):
        return (
            False,
            "pullback valido ma margine insufficiente",
            plan,
        )

    return (
        True,
        f"TREND MOMENTUM PULLBACK {direction} CONFERMATO - {trigger_reason}",
        plan,
    )


# =========================
# SIZE, SL, TP E MARGINE TREND
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

    min_percent_distance = entry * MIN_STOP_PERCENT / 100

    if direction == "BUY":
        h1_structure_stop = float(h1["recent_low"]) - (m15_atr * STRUCTURE_BUFFER_ATR)
        m15_structure_stop = float(m15["recent_low"]) - (m15_atr * STRUCTURE_BUFFER_ATR)
        h1_structure_distance = max(entry - h1_structure_stop, 0.0)
        m15_structure_distance = max(entry - m15_structure_stop, 0.0)
    else:
        h1_structure_stop = float(h1["recent_high"]) + (m15_atr * STRUCTURE_BUFFER_ATR)
        m15_structure_stop = float(m15["recent_high"]) + (m15_atr * STRUCTURE_BUFFER_ATR)
        h1_structure_distance = max(h1_structure_stop - entry, 0.0)
        m15_structure_distance = max(m15_structure_stop - entry, 0.0)

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
    broker = active_broker_profile()
    stop_distance = calculate_structural_stop_distance(direction, entry, h1, m15)

    if direction == "BUY":
        stop_loss = entry - stop_distance
        tp1 = entry + stop_distance * TP1_R_MULTIPLE
        tp2 = entry + stop_distance * TP2_R_MULTIPLE
    else:
        stop_loss = entry + stop_distance
        tp1 = entry - stop_distance * TP1_R_MULTIPLE
        tp2 = entry - stop_distance * TP2_R_MULTIPLE

    broker_capital = float(broker["capital_eur"])
    full_risk_eur = broker_capital * FULL_RISK_PERCENT / 100

    if state == "VERDE":
        target_risk_eur = full_risk_eur
        size_label = "100% del rischio previsto"
        lot_cap = float(broker["max_lot"])
    elif state == "GIALLO":
        target_risk_eur = full_risk_eur * YELLOW_RISK_FRACTION
        size_label = "25% del rischio previsto"
        lot_cap = max(
            float(broker["min_lot"]),
            floor_to_step(
                float(broker["max_lot"]) * YELLOW_RISK_FRACTION,
                float(broker["lot_step"]),
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

    lot_size = floor_to_step(raw_lot_size, float(broker["lot_step"]))
    if lot_cap > 0:
        lot_size = min(lot_size, lot_cap)

    minimum_warning = False
    if 0 < raw_lot_size < float(broker["min_lot"]) and target_risk_eur > 0:
        lot_size = float(broker["min_lot"])
        minimum_warning = True

    actual_risk_eur = stop_distance * lot_size * EUR_PER_USD_MOVE_PER_LOT
    tp1_profit_eur = actual_risk_eur * TP1_R_MULTIPLE
    tp2_profit_eur = actual_risk_eur * TP2_R_MULTIPLE
    estimated_margin_eur = estimate_margin_eur(entry, lot_size, broker)
    margin_executable, remaining_after_margin = is_trade_executable(
        estimated_margin_eur, broker
    )

    max_real_risk_eur = broker_capital * MAX_REAL_RISK_PERCENT / 100
    risk_executable = (
        actual_risk_eur <= max_real_risk_eur
        if lot_size > 0
        else True
    )
    executable = margin_executable and risk_executable

    return {
        "broker_name": str(broker["name"]),
        "broker_mode": str(broker["mode"]),
        "broker_capital_eur": broker_capital,
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
        "remaining_after_margin_eur": remaining_after_margin,
        "trade_executable": executable,
        "margin_executable": margin_executable,
        "risk_executable": risk_executable,
        "max_real_risk_eur": max_real_risk_eur,
        "raw_lot_size": raw_lot_size,
        "lot_cap": lot_cap,
        "minimum_warning": minimum_warning,
        "plan_mode": "STANDARD",
    }


def setup_label(state: str, score: int, quality: int) -> str:
    if state == "VERDE" and score >= 92 and quality >= 80:
        return "SETUP ECCELLENTE - 5 STELLE"
    if state == "VERDE" and score >= 85 and quality >= 65:
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
# MESSAGGI TELEGRAM TREND
# =========================

def build_telegram_message(
    state: str,
    action: str,
    direction: str,
    score: int,
    quality: int,
    plan: dict,
    h4: dict,
    h1: dict,
    m15: dict,
) -> str:
    trend_background = trend_label_from_higher_timeframes(h4, h1)
    momentum = m15_momentum_label(m15)
    phase = market_phase_label(direction, h4, h1, m15)

    broker_name = str(plan["broker_name"])
    executable = bool(plan["trade_executable"])
    plan_mode = str(plan.get("plan_mode", "STANDARD"))
    margin_available_text = "SI" if bool(plan.get("margin_executable", executable)) else "NO"
    risk_available_text = "SI" if bool(plan.get("risk_executable", executable)) else "NO"

    if state == "ROSSO":
        return (
            "[NO TRADE - TREND] BTC Trend AI v0.9.10\n\n"
            "NESSUN SETUP OPERATIVO\n\n"
            f"Broker operativo: {broker_name}\n"
            f"Trend di fondo H4/H1: {trend_background}\n"
            f"Momentum M15: {momentum}\n"
            f"Fase mercato: {phase}\n\n"
            f"Direzione tecnica osservata: {direction} "
            "(solo direzione, NON e un ingresso)\n"
            f"Score tecnico: {score}/100\n"
            f"Qualita' mercato: {quality}/100\n\n"
            f"AZIONE: {action}\n\n"
            "Il bot non entra finche' trend, qualita' e trigger "
            "non sono sufficientemente allineati."
        )

    lot_size = float(plan["lot_size"])
    warning = ""

    if bool(plan["minimum_warning"]):
        warning += (
            "\nATTENZIONE: la size teorica e' inferiore "
            "al minimo negoziabile. Il rischio reale con "
            f"{lot_size:.2f} lotti puo' essere diverso.\n"
        )

    if not bool(plan.get("margin_executable", True)):
        warning += (
            "\nBLOCCO MARGINE: capitale/margine insufficiente "
            "per eseguire questa size sul broker operativo. "
            "Non aumentare il deposito solo per forzare il trade.\n"
        )

    if not bool(plan.get("risk_executable", True)):
        warning += (
            "\nBLOCCO RISCHIO: con la size minima il rischio reale stimato "
            f"e' {float(plan['actual_risk_eur']):.2f} EUR, oltre il massimo "
            f"consentito di {float(plan['max_real_risk_eur']):.2f} EUR "
            f"({MAX_REAL_RISK_PERCENT:.2f}% del capitale). NON ENTRARE.\n"
        )

    if state == "GIALLO":
        return (
            "[PREALLERTA - TREND] BTC Trend AI v0.9.10\n\n"
            f"{setup_label(state, score, quality)}\n\n"
            "STATO: PREALLERTA - NON ENTRARE\n\n"
            f"Broker operativo: {broker_name}\n"
            f"Capitale broker impostato: "
            f"{float(plan['broker_capital_eur']):.2f} EUR\n"
            f"Margine disponibile per {lot_size:.2f} lotti: "
            f"{margin_available_text}\n"
            f"Rischio entro limite {MAX_REAL_RISK_PERCENT:.2f}%: "
            f"{risk_available_text}\n"
            f"Modalita' piano: {plan_mode}\n\n"
            f"Trend di fondo H4/H1: {trend_background}\n"
            f"Momentum M15: {momentum}\n"
            f"Fase mercato: {phase}\n\n"
            f"Direzione osservata: {direction}\n"
            f"Entrata indicativa: {float(plan['entry']):.2f}\n"
            f"Stop indicativo: {float(plan['stop_loss']):.2f}\n"
            f"TP1 indicativo: {float(plan['tp1']):.2f}\n"
            f"TP2 indicativo: {float(plan['tp2']):.2f}\n\n"
            f"Volume minimo/previsto: {lot_size:.2f} lotti\n"
            f"Perdita massima stimata: "
            f"-{float(plan['actual_risk_eur']):.2f} EUR\n"
            f"Margine richiesto stimato: "
            f"{float(plan['estimated_margin_eur']):.2f} EUR\n\n"
            f"Affidabilita' tecnica: {score}/100\n"
            f"Qualita' mercato: {quality}/100\n\n"
            "AZIONE NUOVO INGRESSO: ATTENDERE IL VERDE CONFERMATO\n"
            f"{warning}"
            "Questa e' solo una preallerta: non aprire un nuovo trade. Se hai gia' una posizione, gestiscila separatamente."
        )

    authorization = (
        "INGRESSO AUTORIZZATO DAL SISTEMA"
        if executable
        else "SETUP CONFERMATO MA NON ESEGUIBILE"
    )

    return (
        "[SETUP CONFERMATO - TREND] BTC Trend AI v0.9.10\n\n"
        f"{setup_label(state, score, quality)}\n"
        f"{authorization}\n\n"
        f"Broker operativo: {broker_name}\n"
        f"Capitale broker impostato: "
        f"{float(plan['broker_capital_eur']):.2f} EUR\n"
        f"Operazione eseguibile: {'SI' if executable else 'NO'}\n"
        f"Margine sufficiente: {margin_available_text}\n"
        f"Rischio entro limite {MAX_REAL_RISK_PERCENT:.2f}%: "
        f"{risk_available_text}\n"
        f"Modalita' piano: {plan_mode}\n\n"
        f"Trend di fondo H4/H1: {trend_background}\n"
        f"Momentum M15: {momentum}\n"
        f"Fase mercato: {phase}\n\n"
        f"Direzione setup: {direction}\n\n"
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
        f"{float(plan['estimated_margin_eur']):.2f} EUR\n"
        f"Margine/capitale residuo stimato: "
        f"{float(plan['remaining_after_margin_eur']):.2f} EUR\n\n"
        f"Rischio/Rendimento TP1: 1:{TP1_R_MULTIPLE:.1f}\n"
        f"Rischio/Rendimento TP2: 1:{TP2_R_MULTIPLE:.1f}\n\n"
        f"Affidabilita' tecnica: {score}/100\n"
        f"Qualita' mercato: {quality}/100\n"
        f"Durata stimata: {duration_estimate(state)}\n\n"
        f"AZIONE NUOVO INGRESSO: {action}\n"
        f"{warning}"
        "Il VERDE e' il solo stato che autorizza la valutazione "
        "di un nuovo ingresso."
    )


def build_active_setup_message(
    title: str,
    setup: dict,
    score: int,
    quality: int,
    note: str,
    current_price: float | None = None,
) -> str:
    price_line = ""
    if current_price is not None:
        price_line = f"Prezzo attuale: {current_price:.2f}\n"
    return (
        f"{title} BTC Trend AI v0.9.10\n\n"
        f"{setup['direction']} - POSIZIONE IN MONITORAGGIO\n"
        f"Broker: {setup.get('broker_name', 'N/D')}\n\n"
        f"Entrata originale: {float(setup['entry']):.2f}\n"
        f"{price_line}"
        f"Stop Loss: {float(setup['stop_loss']):.2f}\n"
        f"Take Profit 1: {float(setup['tp1']):.2f}\n"
        f"Take Profit 2: {float(setup['tp2']):.2f}\n\n"
        f"Score attuale: {score}/100\n"
        f"Qualita' mercato: {quality}/100\n\n"
        f"AZIONE: {note}\n\n"
        "Questo NON e' un nuovo segnale d'ingresso."
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
    global last_trend_red_sent_at

    now = datetime.now(timezone.utc)

    if state_key.startswith("ROSSO|"):
        if (
            last_trend_red_sent_at is not None
            and (now - last_trend_red_sent_at).total_seconds()
            < TREND_RED_MIN_NOTIFY_MINUTES * 60
        ):
            last_notified_state = state_key
            return False

    if state_key == last_notified_state:
        return False

    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

    if state_key.startswith("ROSSO|"):
        last_trend_red_sent_at = now

    last_notified_state = state_key
    return True


# =========================
# GESTIONE SETUP ATTIVO TREND
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
        "broker_name": str(plan["broker_name"]),
        "initial_score": score,
        "initial_quality": quality,
        "tp1_hit": False,
        "protection_notified": False,
        "warning_notified": False,
        "status": "ATTIVO",
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
    current_price = float(m15["price"])
    last_low = float(m15["last_low"])
    last_high = float(m15["last_high"])
    stop_loss = float(setup["stop_loss"])
    tp1 = float(setup["tp1"])
    tp2 = float(setup["tp2"])
    entry = float(setup["entry"])

    if direction == "BUY":
        stop_hit = last_low <= stop_loss
        tp1_hit_now = last_high >= tp1
        tp2_hit_now = last_high >= tp2
        favorable_move = current_price - entry
        total_to_tp1 = tp1 - entry
    else:
        stop_hit = last_high >= stop_loss
        tp1_hit_now = last_low <= tp1
        tp2_hit_now = last_low <= tp2
        favorable_move = entry - current_price
        total_to_tp1 = entry - tp1

    if stop_hit and tp1_hit_now:
        return "ACTIVE|AMBIGUO", build_active_setup_message(
            "[ATTENZIONE]",
            setup,
            score,
            quality,
            "Nella stessa candela M15 risultano toccati sia area TP sia area SL. "
            "Ordine temporale non determinabile dai dati candela.",
            current_price,
        ), True

    if stop_hit:
        return "ACTIVE|STOP", build_active_setup_message(
            "[ROSSO]",
            setup,
            score,
            quality,
            "SETUP CHIUSO / INVALIDATO DALLO STOP. Non mediare la perdita.",
            current_price,
        ), True

    if tp2_hit_now:
        return "ACTIVE|TP2", build_active_setup_message(
            "[VERDE]",
            setup,
            score,
            quality,
            "TP2 RAGGIUNTO. Setup completato.",
            current_price,
        ), True

    if tp1_hit_now and not bool(setup["tp1_hit"]):
        setup["tp1_hit"] = True
        setup["status"] = "TP1"
        return "ACTIVE|TP1", build_active_setup_message(
            "[VERDE]",
            setup,
            score,
            quality,
            "TP1 RAGGIUNTO. Valuta protezione del trade e lascia lavorare "
            "solo l'eventuale parte residua.",
            current_price,
        ), False

    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"
    opposite = "RIBASSISTA" if direction == "BUY" else "RIALZISTA"

    hard_invalidated = (
        h4["trend"] == opposite
        or (h1["trend"] == opposite and score < 55)
    )
    if hard_invalidated:
        setup["status"] = "INVALIDATO"
        return "ACTIVE|INVALIDATO", build_active_setup_message(
            "[ROSSO]",
            setup,
            score,
            quality,
            "STRUTTURA MULTIORARIA INVALIDATA. Valuta uscita: "
            "non e' un semplice pullback M15.",
            current_price,
        ), True

    progress_to_tp1 = (
        favorable_move / total_to_tp1 if total_to_tp1 > 0 else 0.0
    )
    if progress_to_tp1 >= 0.60 and not bool(setup["protection_notified"]):
        setup["protection_notified"] = True
        return "ACTIVE|PROTEGGI", build_active_setup_message(
            "[VERDE]",
            setup,
            score,
            quality,
            "PROTEGGI IL TRADE. Il movimento ha percorso almeno il 60% verso TP1. "
            "Valuta stop a pareggio o protezione parziale, senza aumentare size.",
            current_price,
        ), False

    deteriorated = (
        score < 60
        or quality < 50
        or (direction_now != direction and h1["trend"] != expected)
    )
    if deteriorated and not bool(setup["warning_notified"]):
        setup["warning_notified"] = True
        setup["status"] = "ATTENZIONE"
        return "ACTIVE|ATTENZIONE", build_active_setup_message(
            "[GIALLO]",
            setup,
            score,
            quality,
            "ATTENZIONE - FORZA RIDOTTA. NON AGGIUNGERE SIZE. "
            "Il setup non e' ancora invalidato su H4/H1.",
            current_price,
        ), False

    return None, None, False


# =========================
# JOURNAL E GESTIONE SCALP ATTIVO
# =========================

def append_scalp_journal(event: dict) -> None:
    try:
        payload = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            **event,
        }
        Path(SCALP_JOURNAL_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(SCALP_JOURNAL_FILE, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception as error:
        print("Errore journal scalp:", repr(error), flush=True)


def create_active_scalp_setup(
    direction: str,
    score: int,
    quality: int,
    plan: dict,
) -> dict:
    return {
        "direction": direction,
        "entry": float(plan["entry"]),
        "stop": float(plan["stop"]),
        "tp1": float(plan["tp1"]),
        "tp2": float(plan["tp2"]),
        "lot": float(plan["lot"]),
        "broker_name": str(plan["broker_name"]),
        "score": score,
        "quality": quality,
        "tp1_hit": False,
        "protect_notified": False,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }


def build_scalp_management_message(
    title: str,
    setup: dict,
    current_price: float,
    action: str,
) -> str:
    return (
        f"{title} BTC Trend AI v0.9.10\n\n"
        f"SCALP {setup['direction']} - POSIZIONE IN MONITORAGGIO\n"
        f"Broker: {setup['broker_name']}\n\n"
        f"Entrata: {float(setup['entry']):.2f}\n"
        f"Prezzo attuale: {current_price:.2f}\n"
        f"Stop Loss: {float(setup['stop']):.2f}\n"
        f"Take Profit 1: {float(setup['tp1']):.2f}\n"
        f"Take Profit 2: {float(setup['tp2']):.2f}\n\n"
        f"AZIONE: {action}\n\n"
        "Questo NON e' un nuovo segnale d'ingresso."
    )


def manage_active_scalp_setup(
    setup: dict,
    m5: dict,
) -> tuple[str | None, str | None, bool]:
    direction = str(setup["direction"])
    current_price = float(m5["price"])
    last_low = float(m5["last_low"])
    last_high = float(m5["last_high"])

    entry = float(setup["entry"])
    stop = float(setup["stop"])
    tp1 = float(setup["tp1"])
    tp2 = float(setup["tp2"])

    if direction == "BUY":
        stop_hit = last_low <= stop
        tp1_hit = last_high >= tp1
        tp2_hit = last_high >= tp2
        favorable_move = current_price - entry
        risk_distance = entry - stop
    else:
        stop_hit = last_high >= stop
        tp1_hit = last_low <= tp1
        tp2_hit = last_low <= tp2
        favorable_move = entry - current_price
        risk_distance = stop - entry

    if stop_hit and (tp1_hit or tp2_hit):
        message = build_scalp_management_message(
            "[SCALP ATTENZIONE]",
            setup,
            current_price,
            "Nella stessa candela M5 risultano toccati stop e target. "
            "Sequenza non determinabile.",
        )
        append_scalp_journal({
            "event": "AMBIGUO",
            "direction": direction,
            "entry": entry,
            "price": current_price,
        })
        return "SCALP|AMBIGUO", message, True

    if stop_hit:
        message = build_scalp_management_message(
            "[SCALP STOP]",
            setup,
            current_price,
            "STOP LOSS RAGGIUNTO - SCALP CHIUSO.",
        )
        append_scalp_journal({
            "event": "STOP",
            "direction": direction,
            "entry": entry,
            "price": current_price,
            "stop": stop,
        })
        return "SCALP|STOP", message, True

    if tp2_hit:
        message = build_scalp_management_message(
            "[SCALP TARGET]",
            setup,
            current_price,
            "TP2 RAGGIUNTO - OBIETTIVO SCALP COMPLETATO.",
        )
        append_scalp_journal({
            "event": "TP2",
            "direction": direction,
            "entry": entry,
            "price": current_price,
            "tp2": tp2,
        })
        return "SCALP|TP2", message, True

    if tp1_hit and not bool(setup["tp1_hit"]):
        setup["tp1_hit"] = True
        message = build_scalp_management_message(
            "[SCALP TARGET]",
            setup,
            current_price,
            "TP1 RAGGIUNTO - PROTEGGI IL TRADE. Valuta stop a pareggio "
            "e lascia lavorare l'eventuale parte residua verso TP2.",
        )
        append_scalp_journal({
            "event": "TP1",
            "direction": direction,
            "entry": entry,
            "price": current_price,
            "tp1": tp1,
        })
        return "SCALP|TP1", message, False

    progress_r = favorable_move / risk_distance if risk_distance > 0 else 0.0

    if (
        progress_r >= SCALP_PROTECT_AT_R
        and not bool(setup["protect_notified"])
        and not bool(setup["tp1_hit"])
    ):
        setup["protect_notified"] = True
        message = build_scalp_management_message(
            "[SCALP PROTEZIONE]",
            setup,
            current_price,
            f"TRADE IN PROFITTO DI CIRCA {progress_r:.1f}R. "
            "Valuta protezione o stop a pareggio. Non aumentare la size.",
        )
        append_scalp_journal({
            "event": "PROTEGGI",
            "direction": direction,
            "entry": entry,
            "price": current_price,
            "progress_r": progress_r,
        })
        return "SCALP|PROTEGGI", message, False

    try:
        opened_at = datetime.fromisoformat(str(setup["opened_at"]))
        age_hours = (
            datetime.now(timezone.utc) - opened_at
        ).total_seconds() / 3600
    except Exception:
        age_hours = 0.0

    if age_hours >= SCALP_TRACK_MAX_HOURS:
        message = build_scalp_management_message(
            "[SCALP PROTEZIONE]",
            setup,
            current_price,
            "SCALP FUORI TEMPO: oltre la finestra prevista. "
            "Valuta chiusura manuale o nuova analisi.",
        )
        append_scalp_journal({
            "event": "TIMEOUT",
            "direction": direction,
            "entry": entry,
            "price": current_price,
            "age_hours": age_hours,
        })
        return "SCALP|TIMEOUT", message, True

    return None, None, False


# =========================
# MOTORE SCALP v0.9.10
# =========================

def scalp_direction_score(
    direction: str,
    h1: dict,
    m15: dict,
    m5: dict,
) -> tuple[int, list[str]]:
    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"
    opposite = "RIBASSISTA" if direction == "BUY" else "RIALZISTA"
    score = 0
    reasons: list[str] = []

    if h1["trend"] == expected:
        score += 20
        reasons.append("H1 favorevole")
    elif h1["trend"] == opposite:
        score -= 12
        reasons.append("H1 contrario")
    else:
        score += 8
        reasons.append("H1 neutro")

    if m15["trend"] == expected:
        score += 30
        reasons.append("M15 allineato")
    elif m15["trend"] == opposite:
        score -= 25
        reasons.append("M15 contrario")
    else:
        if direction == "BUY" and float(m15["price"]) > float(m15["ema50"]):
            score += 14
            reasons.append("M15 neutro ma sopra EMA50")
        elif direction == "SELL" and float(m15["price"]) < float(m15["ema50"]):
            score += 14
            reasons.append("M15 neutro ma sotto EMA50")

    rsi15 = float(m15["rsi"])
    if direction == "BUY" and 50 <= rsi15 <= 70:
        score += 14
        reasons.append("RSI M15 favorevole")
    elif direction == "SELL" and 30 <= rsi15 <= 50:
        score += 14
        reasons.append("RSI M15 favorevole")

    candle_up = float(m5["last_close"]) > float(m5["last_open"])
    candle_down = float(m5["last_close"]) < float(m5["last_open"])

    if (
        direction == "BUY"
        and float(m5["price"]) > float(m5["ema50"])
        and candle_up
    ):
        score += 24
        reasons.append("trigger M5 BUY")
    elif (
        direction == "SELL"
        and float(m5["price"]) < float(m5["ema50"])
        and candle_down
    ):
        score += 24
        reasons.append("trigger M5 SELL")

    rsi5 = float(m5["rsi"])
    if direction == "BUY" and 48 <= rsi5 <= 72:
        score += 8
    elif direction == "SELL" and 28 <= rsi5 <= 52:
        score += 8

    if float(m15["adx"]) >= 18:
        score += 4
    if float(m5["adx"]) >= 18:
        score += 4

    return max(0, min(score, 100)), reasons


def choose_scalp_direction(
    h1: dict,
    m15: dict,
    m5: dict,
) -> tuple[str, int, list[str]]:
    buy_score, buy_reasons = scalp_direction_score("BUY", h1, m15, m5)
    sell_score, sell_reasons = scalp_direction_score("SELL", h1, m15, m5)

    if buy_score >= sell_score:
        return "BUY", buy_score, buy_reasons
    return "SELL", sell_score, sell_reasons


def scalp_quality(m15: dict, m5: dict) -> int:
    quality = 45

    if float(m15["atr_percent"]) >= 0.20:
        quality += 15
    if float(m5["atr_percent"]) >= 0.08:
        quality += 10
    if float(m15["adx"]) >= 18:
        quality += 15
    if float(m5["adx"]) >= 18:
        quality += 10
    if float(m15["distance_from_ema50_atr"]) > 1.80:
        quality -= 20

    return max(0, min(quality, 100))


def scalp_trigger_ok(direction: str, m15: dict, m5: dict) -> bool:
    candle_up = float(m5["last_close"]) > float(m5["last_open"])
    candle_down = float(m5["last_close"]) < float(m5["last_open"])

    if direction == "BUY":
        return (
            float(m5["price"]) > float(m5["ema50"])
            and candle_up
            and float(m5["rsi"]) >= 48
            and float(m15["rsi"]) >= 48
        )

    return (
        float(m5["price"]) < float(m5["ema50"])
        and candle_down
        and float(m5["rsi"]) <= 52
        and float(m15["rsi"]) <= 52
    )


def scalp_h1_blocked(direction: str, h1: dict) -> bool:
    opposite = "RIBASSISTA" if direction == "BUY" else "RIALZISTA"
    return h1["trend"] == opposite and float(h1["adx"]) >= 28


def scalp_breakout_ok(
    direction: str,
    m15: dict,
) -> tuple[bool, str]:
    """
    Richiede che L'ULTIMA CANDELA M15 CHIUSA abbia superato il massimo/minimo
    delle precedenti candele del lookback, con un piccolo buffer ATR.
    """
    close = float(m15["last_close"])
    atr = float(m15["atr"])
    buffer_value = atr * SCALP_BREAKOUT_BUFFER_ATR

    previous_high = float(m15["previous_recent_high"])
    previous_low = float(m15["previous_recent_low"])

    if direction == "BUY":
        level = previous_high + buffer_value
        ok = close > level
        if ok:
            return True, f"breakout M15 BUY confermato sopra {level:.2f}"
        return False, f"manca breakout M15 BUY: serve chiusura > {level:.2f}"

    level = previous_low - buffer_value
    ok = close < level
    if ok:
        return True, f"breakout M15 SELL confermato sotto {level:.2f}"
    return False, f"manca breakout M15 SELL: serve chiusura < {level:.2f}"


def calculate_scalp_stop_distance(
    direction: str,
    entry: float,
    m15: dict,
    m5: dict,
) -> float:
    """
    Stop non piu' basato solo su ATR M5.
    Usa il valore PIU' LARGO tra:
    - ATR M5
    - ATR M15
    - struttura recente M5
    - distanza minima percentuale
    """
    m5_atr = float(m5["atr"])
    m15_atr = float(m15["atr"])

    atr_distance = max(
        m5_atr * SCALP_STOP_ATR_MULTIPLE,
        m15_atr * SCALP_M15_STOP_ATR_MULTIPLE,
    )

    min_percent_distance = entry * SCALP_MIN_STOP_PERCENT / 100

    if direction == "BUY":
        structure_stop = (
            float(m5["previous_recent_low"])
            - m5_atr * SCALP_STRUCTURE_BUFFER_ATR
        )
        structure_distance = max(entry - structure_stop, 0.0)
    else:
        structure_stop = (
            float(m5["previous_recent_high"])
            + m5_atr * SCALP_STRUCTURE_BUFFER_ATR
        )
        structure_distance = max(structure_stop - entry, 0.0)

    return max(
        atr_distance,
        min_percent_distance,
        structure_distance,
    )


def scalp_stop_ok(
    entry: float,
    stop_distance: float,
) -> tuple[bool, str]:
    """
    Uno scalp 15-60 min non deve avere uno stop da swing.
    Il limite effettivo e' il PIU' BASSO tra:
    - SCALP_MAX_STOP_USD
    - percentuale massima del prezzo
    """
    percent_cap = entry * SCALP_MAX_STOP_PERCENT / 100
    max_allowed = min(SCALP_MAX_STOP_USD, percent_cap)

    if stop_distance <= max_allowed:
        return (
            True,
            f"stop scalp valido: {stop_distance:.2f} USD "
            f"(max {max_allowed:.2f})",
        )

    return (
        False,
        f"stop troppo largo per scalp: {stop_distance:.2f} USD "
        f"(max {max_allowed:.2f})",
    )


def scalp_not_chasing(
    direction: str,
    m15: dict,
    m5: dict,
) -> tuple[bool, str]:
    """
    Blocca il nuovo ingresso quando l'impulso e' gia' troppo lontano
    dalla EMA50 M15. Non e' un blocco permanente: dopo il pullback,
    la distanza ATR scende e il setup torna valutabile.
    """
    extension = float(m15["distance_from_ema50_atr"])

    if extension > SCALP_MAX_M15_EXTENSION_ATR:
        return (
            False,
            f"prezzo troppo esteso: {extension:.2f} ATR da EMA50 M15 "
            f"(max {SCALP_MAX_M15_EXTENSION_ATR:.2f}) - attendere pullback",
        )

    # Evita anche la candela M5 eccessivamente verticale:
    # un corpo > 1.8 ATR M5 indica spesso ingresso tardivo.
    m5_atr = float(m5["atr"])
    body = abs(float(m5["last_close"]) - float(m5["last_open"]))
    body_atr = body / m5_atr if m5_atr > 0 else 0.0

    if body_atr > 1.80:
        return (
            False,
            f"candela M5 troppo estesa: corpo {body_atr:.2f} ATR - "
            "attendere pullback/nuovo trigger",
        )

    return True, f"estensione accettabile: {extension:.2f} ATR"


def build_scalp_plan(direction: str, m15: dict, m5: dict) -> dict:
    broker = active_broker_profile()
    entry = float(m5["price"])

    stop_distance = calculate_scalp_stop_distance(
        direction,
        entry,
        m15,
        m5,
    )

    if direction == "BUY":
        stop = entry - stop_distance
        tp1 = entry + stop_distance * SCALP_TP1_R
        tp2 = entry + stop_distance * SCALP_TP2_R
    else:
        stop = entry + stop_distance
        tp1 = entry - stop_distance * SCALP_TP1_R
        tp2 = entry - stop_distance * SCALP_TP2_R

    capital = float(broker["capital_eur"])
    target_risk = capital * SCALP_RISK_PERCENT / 100
    eur_loss_per_lot = stop_distance * EUR_PER_USD_MOVE_PER_LOT
    raw_lot = (
        target_risk / eur_loss_per_lot
        if eur_loss_per_lot > 0
        else 0.0
    )

    lot = floor_to_step(raw_lot, float(broker["lot_step"]))
    if lot < float(broker["min_lot"]):
        lot = float(broker["min_lot"])
    lot = min(lot, float(broker["max_lot"]))

    estimated_loss = stop_distance * lot * EUR_PER_USD_MOVE_PER_LOT
    margin = estimate_margin_eur(entry, lot, broker)
    margin_executable, remaining = is_trade_executable(margin, broker)

    max_real_risk_eur = capital * MAX_REAL_RISK_PERCENT / 100
    risk_executable = estimated_loss <= max_real_risk_eur
    executable = margin_executable and risk_executable

    return {
        "broker_name": str(broker["name"]),
        "capital": capital,
        "entry": entry,
        "stop": stop,
        "stop_distance": stop_distance,
        "tp1": tp1,
        "tp2": tp2,
        "lot": lot,
        "loss_eur": estimated_loss,
        "margin_eur": margin,
        "remaining_eur": remaining,
        "executable": executable,
        "margin_executable": margin_executable,
        "risk_executable": risk_executable,
        "max_real_risk_eur": max_real_risk_eur,
        "raw_lot": raw_lot,
    }


def scalp_room_ok(
    direction: str,
    plan: dict,
    h1: dict,
) -> tuple[bool, str]:
    """
    Evita BUY subito sotto resistenza H1 e SELL subito sopra supporto H1.
    La barriera deve lasciare almeno SCALP_MIN_ROOM_R volte il rischio.
    """
    entry = float(plan["entry"])
    risk_distance = float(plan["stop_distance"])
    minimum_room = risk_distance * SCALP_MIN_ROOM_R

    if direction == "BUY":
        resistance = float(h1["previous_recent_high"])

        # Se la resistenza e' gia' sotto il prezzo, e' stata superata.
        if resistance <= entry:
            return True, "resistenza H1 gia' superata"

        room = resistance - entry
        if room >= minimum_room:
            return True, f"spazio libero fino a resistenza H1: {room:.2f} USD"

        return (
            False,
            f"BUY bloccato: resistenza H1 troppo vicina "
            f"({room:.2f} USD, minimo {minimum_room:.2f})",
        )

    support = float(h1["previous_recent_low"])

    if support >= entry:
        return True, "supporto H1 gia' superato"

    room = entry - support
    if room >= minimum_room:
        return True, f"spazio libero fino a supporto H1: {room:.2f} USD"

    return (
        False,
        f"SELL bloccato: supporto H1 troppo vicino "
        f"({room:.2f} USD, minimo {minimum_room:.2f})",
    )


def update_scalp_confirmation(
    direction: str,
    eligible: bool,
    m5: dict,
) -> bool:
    global scalp_candidate_direction
    global scalp_candidate_count
    global scalp_candidate_last_m5_time

    bar_time = int(m5["last_candle_time"])

    if not eligible:
        scalp_candidate_direction = None
        scalp_candidate_count = 0
        scalp_candidate_last_m5_time = None
        return False

    if scalp_candidate_direction != direction:
        scalp_candidate_direction = direction
        scalp_candidate_count = 1
        scalp_candidate_last_m5_time = bar_time
        return SCALP_CONFIRM_BARS <= 1

    if scalp_candidate_last_m5_time != bar_time:
        scalp_candidate_count += 1
        scalp_candidate_last_m5_time = bar_time

    return scalp_candidate_count >= SCALP_CONFIRM_BARS


def scalp_signal_allowed_now() -> tuple[bool, str]:
    global scalp_last_signal_time
    global scalp_signal_day
    global scalp_signals_today

    now = datetime.now(timezone.utc)
    day_key = now.strftime("%Y-%m-%d")

    if scalp_signal_day != day_key:
        scalp_signal_day = day_key
        scalp_signals_today = 0

    if scalp_signals_today >= SCALP_MAX_SIGNALS_PER_DAY:
        return False, "limite giornaliero raggiunto"

    if scalp_last_signal_time is not None:
        elapsed = (now - scalp_last_signal_time).total_seconds() / 60
        if elapsed < SCALP_COOLDOWN_MINUTES:
            return False, f"cooldown {SCALP_COOLDOWN_MINUTES} min"

    return True, "ok"


def build_scalp_green_message(
    direction: str,
    score: int,
    quality: int,
    plan: dict,
    reasons: list[str],
    breakout_reason: str,
    room_reason: str,
    stop_reason: str,
    chase_reason: str,
) -> str:
    margin_text = "SI" if bool(plan.get("margin_executable", plan["executable"])) else "NO"
    risk_text = "SI" if bool(plan.get("risk_executable", plan["executable"])) else "NO"

    all_reasons = reasons + [breakout_reason, room_reason, stop_reason, chase_reason]
    reasons_block = "\n".join(
        f"- {reason}"
        for reason in all_reasons
    )

    return (
        f"[{direction} SCALP - INGRESSO] BTC Trend AI v0.9.10\n\n"
        f"SCALP {direction} - INGRESSO CONFERMATO\n"
        "Durata obiettivo: 15-60 minuti\n\n"
        f"Broker operativo: {plan['broker_name']}\n"
        f"Margine disponibile per "
        f"{float(plan['lot']):.2f} lotti: {margin_text}\n"
        f"Rischio entro limite {MAX_REAL_RISK_PERCENT:.2f}%: {risk_text}\n\n"
        f"Entrata: {float(plan['entry']):.2f}\n"
        f"Stop Loss: {float(plan['stop']):.2f}\n"
        f"Distanza Stop: {float(plan['stop_distance']):.2f} USD\n"
        f"Take Profit 1: {float(plan['tp1']):.2f}\n"
        f"Take Profit 2: {float(plan['tp2']):.2f}\n\n"
        f"Volume: {float(plan['lot']):.2f} lotti\n"
        f"Perdita massima stimata: "
        f"-{float(plan['loss_eur']):.2f} EUR\n"
        f"Rischio massimo ammesso: "
        f"{float(plan['max_real_risk_eur']):.2f} EUR\n"
        f"Margine richiesto stimato: "
        f"{float(plan['margin_eur']):.2f} EUR\n\n"
        f"R/R TP1: 1:{SCALP_TP1_R:.2f}\n"
        f"R/R TP2: 1:{SCALP_TP2_R:.2f}\n\n"
        f"Affidabilita' scalp: {score}/100\n"
        f"Qualita' mercato: {quality}/100\n\n"
        "AZIONE:\n"
        "VALUTARE INGRESSO SCALP ORA\n\n"
        "Conferme:\n"
        f"{reasons_block}\n\n"
        "Il segnale scalp normale richiede breakout M15 chiuso, "
        "due conferme M5, stop entro il limite e prezzo non esteso.\n"
        "Non aggiungere size e non inseguire il prezzo.\n"
        "Segnale tecnico indicativo: non garantisce profitto."
    )


async def evaluate_and_notify_scalp(
    bot: Bot,
    h1: dict,
    m15: dict,
    m5: dict,
) -> None:
    global scalp_last_signal_time
    global scalp_signals_today
    global last_scalp_status_key
    global active_scalp_setup

    if not SCALP_ENABLED:
        return

    direction, score, reasons = choose_scalp_direction(h1, m15, m5)
    quality = scalp_quality(m15, m5)
    trigger = scalp_trigger_ok(direction, m15, m5)
    blocked = scalp_h1_blocked(direction, h1)

    breakout_ok, breakout_reason = scalp_breakout_ok(
        direction,
        m15,
    )

    # Il piano viene costruito PRIMA della conferma, per poter verificare
    # stop dinamico e spazio reale verso supporto/resistenza.
    plan = build_scalp_plan(direction, m15, m5)

    room_ok, room_reason = scalp_room_ok(
        direction,
        plan,
        h1,
    )

    stop_ok, stop_reason = scalp_stop_ok(
        float(plan["entry"]),
        float(plan["stop_distance"]),
    )

    chase_ok, chase_reason = scalp_not_chasing(
        direction,
        m15,
        m5,
    )

    eligible = (
        score >= SCALP_GREEN_MIN_SCORE
        and quality >= SCALP_MIN_QUALITY
        and trigger
        and not blocked
        and breakout_ok
        and room_ok
        and stop_ok
        and chase_ok
        and bool(plan["risk_executable"])
    )

    confirmed = update_scalp_confirmation(
        direction,
        eligible,
        m5,
    )

    print(
        "\nSCALP v0.9.10 | "
        f"{direction} score={score}/100 quality={quality}/100 "
        f"trigger={'SI' if trigger else 'NO'} "
        f"H1_block={'SI' if blocked else 'NO'} "
        f"breakout={'SI' if breakout_ok else 'NO'} "
        f"room={'SI' if room_ok else 'NO'} "
        f"stop_ok={'SI' if stop_ok else 'NO'} "
        f"chase_ok={'SI' if chase_ok else 'NO'} "
        f"risk_ok={'SI' if bool(plan['risk_executable']) else 'NO'} "
        f"SLdist={float(plan['stop_distance']):.2f} "
        f"conferme={scalp_candidate_count}/{SCALP_CONFIRM_BARS}\n"
        f"Breakout: {breakout_reason}\n"
        f"Spazio: {room_reason}\n"
        f"Stop: {stop_reason}\n"
        f"Anti-chase: {chase_reason}",
        flush=True,
    )

    if not confirmed:
        return

    allowed, reason = scalp_signal_allowed_now()
    if not allowed:
        print(f"SCALP non inviato: {reason}", flush=True)
        return

    if not bool(plan["executable"]):
        if not bool(plan.get("risk_executable", True)):
            print(
                "SCALP bloccato: rischio reale eccessivo con size minima. "
                f"Rischio={float(plan['loss_eur']):.2f} EUR, "
                f"max={float(plan['max_real_risk_eur']):.2f} EUR.",
                flush=True,
            )
        else:
            print(
                "SCALP confermato tecnicamente ma non eseguibile per margine.",
                flush=True,
            )
        return

    status_key = f"{direction}|{int(m5['last_candle_time'])}"
    if status_key == last_scalp_status_key:
        return

    if active_scalp_setup is not None:
        print(
            "SCALP non inviato: esiste gia' uno scalp attivo.",
            flush=True,
        )
        return

    message = build_scalp_green_message(
        direction,
        score,
        quality,
        plan,
        reasons,
        breakout_reason,
        room_reason,
        stop_reason,
        chase_reason,
    )

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
    )

    active_scalp_setup = create_active_scalp_setup(
        direction,
        score,
        quality,
        plan,
    )

    append_scalp_journal({
        "event": "ENTRY",
        "direction": direction,
        "score": score,
        "quality": quality,
        "entry": float(plan["entry"]),
        "stop": float(plan["stop"]),
        "stop_distance": float(plan["stop_distance"]),
        "tp1": float(plan["tp1"]),
        "tp2": float(plan["tp2"]),
        "lot": float(plan["lot"]),
        "broker": str(plan["broker_name"]),
        "breakout": breakout_reason,
        "room": room_reason,
    })

    last_scalp_status_key = status_key
    scalp_last_signal_time = datetime.now(timezone.utc)
    scalp_signals_today += 1

    print(
        f"SCALP VERDE inviato: {direction} | "
        f"{scalp_signals_today}/{SCALP_MAX_SIGNALS_PER_DAY} oggi",
        flush=True,
    )


# =========================
# CICLO PRINCIPALE
# =========================

async def run_analysis(
    session: aiohttp.ClientSession,
    bot: Bot,
) -> None:
    global active_setup
    global last_notified_state
    global active_scalp_setup

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

    m5_history = await fetch_candles(
        session=session,
        granularity=300,
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
    m5 = analyze_timeframe(m5_history[-220:], "M5")

    if active_scalp_setup is not None:
        _event_key, scalp_message, close_scalp = manage_active_scalp_setup(
            active_scalp_setup,
            m5,
        )

        if scalp_message is not None:
            await bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=scalp_message,
            )

        if close_scalp:
            active_scalp_setup = None

    if active_scalp_setup is None:
        await evaluate_and_notify_scalp(
            bot,
            h1,
            m15,
            m5,
        )

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
        f"{now} DEBUG v0.9.10 TREND\n"
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
        f"Trend fondo={trend_label_from_higher_timeframes(h4, h1)} | "
        f"Momentum M15={m15_momentum_label(m15)} | "
        f"Fase={market_phase_label(direction, h4, h1, m15)}\n"
        f"Motivi: {', '.join(reasons)}\n"
        f"Pullback v0.9.10: "
        f"{'VERDE' if pullback_green else 'ATTESA'} | "
        f"{pullback_reason}",
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

        if management_key is not None and management_message is not None:
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

    # ---------------------------------------------------------
    # v0.9.10 - PRIORITA' TREND MOMENTUM PULLBACK
    # ---------------------------------------------------------
    pullback_green, pullback_reason, pullback_plan = evaluate_trend_pullback(
        direction,
        score,
        quality,
        h4,
        h1,
        m15,
        m5,
    )

    if pullback_green and pullback_plan is not None:
        state = "VERDE"
        action = pullback_reason
        plan = pullback_plan
    else:
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

        # Se il trend e' forte ma il piano standard ha rischio troppo alto,
        # il messaggio deve spiegare che si sta aspettando un rientro M5/M15.
        strong_context = (
            score >= TREND_PULLBACK_MIN_SCORE
            and quality >= TREND_PULLBACK_MIN_QUALITY
            and higher_timeframes_aligned(direction, h4, h1)
        )

        if (
            strong_context
            and not bool(plan["risk_executable"])
            and not pullback_green
        ):
            state = "GIALLO"
            action = (
                "TREND FORTE - PIANO STANDARD TROPPO LARGO. "
                f"ATTENDERE PULLBACK M5/M15: {pullback_reason}"
            )

    message = build_telegram_message(
        state,
        action,
        direction,
        score,
        quality,
        plan,
        h4,
        h1,
        m15,
    )

    print(
        f"\n{now}\n{message}",
        flush=True,
    )

    state_key = (
        f"{state}|{direction}|"
        f"{trend_label_from_higher_timeframes(h4, h1)}|"
        f"{m15_momentum_label(m15)}|"
        f"{market_phase_label(direction, h4, h1, m15)}|"
        f"{score_band(score)}|"
        f"{quality_band(quality)}|"
        f"{plan.get('plan_mode', 'STANDARD')}"
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

    if state == "VERDE" and bool(plan["trade_executable"]):
        active_setup = create_active_setup(
            direction,
            score,
            quality,
            plan,
        )

        if sent:
            print(
                "SETUP ATTIVO creato: "
                f"{direction} @ {float(plan['entry']):.2f} "
                f"su {plan['broker_name']}",
                flush=True,
            )


async def main() -> None:
    print(
        "BTC Trend AI v0.9.10 DUAL Trend+Scalp Multi-Broker avviato",
        flush=True,
    )

    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN mancante")

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID mancante")

    headers = {
        "User-Agent": "BTC-Trend-AI/0.9.10",
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
