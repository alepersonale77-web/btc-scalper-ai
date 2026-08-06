import asyncio
import os
from datetime import datetime, timedelta, timezone

import aiohttp
from telegram import Bot


# =========================
# CONFIGURAZIONE PRINCIPALE
# =========================

PRODUCT = "BTC-USD"
CHECK_INTERVAL_SECONDS = 300

EMA_FAST_PERIOD = 50
EMA_SLOW_PERIOD = 200
RSI_PERIOD = 14
ATR_PERIOD = 14

ACCOUNT_CAPITAL_EUR = 1000.0
FULL_RISK_PERCENT = 2.0
YELLOW_RISK_FRACTION = 0.25

TP1_R_MULTIPLE = 1.5
TP2_R_MULTIPLE = 2.5
STOP_ATR_MULTIPLE = 1.20

# Dati ricavati dalle specifiche FP Trading / FP Markets:
# - Dimensione contratto BTCUSD = 1
# - Margine = 2%
# - 1 lotto = 1 BTC
CONTRACT_SIZE = 1.0
MARGIN_PERCENT = 2.0

# Dal test reale sul tuo conto:
# 0.01 lotti, movimento di circa 29.20 USD, profitto circa 0.25 EUR.
# Il valore oscilla leggermente per la conversione USD/EUR.
# Usiamo 0.86 EUR per ogni 1 USD di movimento con 1 lotto.
EUR_PER_USD_MOVE_PER_LOT = float(
    os.environ.get("EUR_PER_USD_MOVE_PER_LOT", "0.86")
)

MIN_LOT = 0.01
LOT_STEP = 0.01

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

COINBASE_URL = (
    f"https://api.exchange.coinbase.com/products/"
    f"{PRODUCT}/candles"
)

last_notified_state: str | None = None


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
            raise RuntimeError(
                f"Risposta Coinbase non valida: {data}"
            )

        for candle in data:
            candle_time = int(candle[0])

            # Usa soltanto candele gia' chiuse.
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
        raise ValueError(
            f"Servono almeno {period} valori per EMA{period}"
        )

    multiplier = 2 / (period + 1)
    ema_value = sum(values[:period]) / period

    for value in values[period:]:
        ema_value = (
            value * multiplier
            + ema_value * (1 - multiplier)
        )

    return ema_value


def calculate_rsi(
    values: list[float],
    period: int = 14,
) -> float:
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
        average_gain = (
            (average_gain * (period - 1)) + gain
        ) / period
        average_loss = (
            (average_loss * (period - 1)) + loss
        ) / period

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

    ema_distance_percent = (
        abs(ema50 - ema200) / ema200
    ) * 100
    atr_percent = (atr / price) * 100
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

    return {
        "timeframe": timeframe,
        "price": price,
        "ema50": ema50,
        "ema200": ema200,
        "ema_distance": ema_distance_percent,
        "rsi": rsi,
        "atr": atr,
        "atr_percent": atr_percent,
        "distance_from_ema50_atr": distance_from_ema50_atr,
        "trend": trend,
    }


# =========================
# SCORE E STATO
# =========================

def score_direction(
    direction: str,
    h4: dict[str, float | str],
    h1: dict[str, float | str],
    m15: dict[str, float | str],
) -> tuple[int, list[str]]:
    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"
    opposite = "RIBASSISTA" if direction == "BUY" else "RIALZISTA"

    score = 0
    reasons: list[str] = []

    if h4["trend"] == expected:
        score += 22
        reasons.append(f"H4 {expected.lower()}")
    elif h4["trend"] == "NEUTRO / LATERALE":
        score += 8
        reasons.append("H4 neutro")
    else:
        reasons.append(f"H4 {opposite.lower()}")

    if h4["trend"] == expected:
        distance = float(h4["ema_distance"])
        if distance >= 0.40:
            score += 8
        elif distance >= 0.20:
            score += 4

    if h1["trend"] == expected:
        score += 18
        reasons.append(f"H1 {expected.lower()}")
    elif h1["trend"] == "NEUTRO / LATERALE":
        score += 8
        reasons.append("H1 in attesa")
    else:
        reasons.append(f"H1 {opposite.lower()}")

    if h1["trend"] == expected:
        distance = float(h1["ema_distance"])
        if distance >= 0.30:
            score += 7
        elif distance >= 0.15:
            score += 3

    if m15["trend"] == expected:
        score += 20
        reasons.append("M15 conferma")
    elif m15["trend"] == "NEUTRO / LATERALE":
        score += 10
        reasons.append("M15 attende conferma")
    else:
        score += 4
        reasons.append("M15 in pullback")

    h1_rsi = float(h1["rsi"])
    m15_rsi = float(m15["rsi"])

    if direction == "BUY":
        if 48 <= h1_rsi <= 68:
            score += 8
        if 48 <= m15_rsi <= 70:
            score += 7
    else:
        if 32 <= h1_rsi <= 52:
            score += 8
        if 30 <= m15_rsi <= 52:
            score += 7

    atr_percent = float(h1["atr_percent"])

    if atr_percent >= 0.80:
        score += 10
    elif atr_percent >= 0.45:
        score += 7
    elif atr_percent >= 0.25:
        score += 3

    return min(score, 100), reasons


def choose_best_direction(
    h4: dict[str, float | str],
    h1: dict[str, float | str],
    m15: dict[str, float | str],
) -> tuple[str, int, list[str]]:
    buy_score, buy_reasons = score_direction(
        "BUY",
        h4,
        h1,
        m15,
    )
    sell_score, sell_reasons = score_direction(
        "SELL",
        h4,
        h1,
        m15,
    )

    if buy_score >= sell_score:
        return "BUY", buy_score, buy_reasons

    return "SELL", sell_score, sell_reasons


def market_quality(
    h4: dict[str, float | str],
    h1: dict[str, float | str],
) -> int:
    quality = 0

    h4_distance = float(h4["ema_distance"])
    if h4_distance >= 0.50:
        quality += 35
    elif h4_distance >= 0.25:
        quality += 25
    elif h4_distance >= 0.10:
        quality += 12

    h1_distance = float(h1["ema_distance"])
    if h1_distance >= 0.35:
        quality += 30
    elif h1_distance >= 0.18:
        quality += 20
    elif h1_distance >= 0.08:
        quality += 10

    atr_percent = float(h1["atr_percent"])
    if atr_percent >= 0.80:
        quality += 25
    elif atr_percent >= 0.45:
        quality += 18
    elif atr_percent >= 0.25:
        quality += 10

    if h4["trend"] == h1["trend"]:
        if h4["trend"] != "NEUTRO / LATERALE":
            quality += 10
        else:
            quality += 3

    return min(quality, 100)


def determine_state(
    direction: str,
    score: int,
    m15: dict[str, float | str],
) -> tuple[str, str]:
    expected = "RIALZISTA" if direction == "BUY" else "RIBASSISTA"
    too_extended = (
        float(m15["distance_from_ema50_atr"]) > 1.25
    )

    if (
        score >= 85
        and m15["trend"] == expected
        and not too_extended
    ):
        return "VERDE", "APRI IL GRAFICO E VALUTA L'INGRESSO"

    if score >= 70:
        if too_extended:
            return "GIALLO", "NON INSEGUIRE IL PREZZO"
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


def build_trade_plan(
    direction: str,
    state: str,
    m15: dict[str, float | str],
    h1: dict[str, float | str],
) -> dict[str, float | str]:
    entry = float(m15["price"])

    stop_distance = max(
        float(h1["atr"]) * STOP_ATR_MULTIPLE,
        float(m15["atr"]) * 2.0,
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
        size_label = "100% della size prevista"
    elif state == "GIALLO":
        target_risk_eur = (
            full_risk_eur * YELLOW_RISK_FRACTION
        )
        size_label = "25% della size prevista"
    else:
        target_risk_eur = 0.0
        size_label = "Nessuna posizione"

    raw_lot_size = 0.0

    if target_risk_eur > 0 and stop_distance > 0:
        raw_lot_size = target_risk_eur / (
            stop_distance * EUR_PER_USD_MOVE_PER_LOT
        )

    lot_size = floor_to_step(raw_lot_size, LOT_STEP)

    # Se la size calcolata e' inferiore al minimo, il bot non deve
    # fingere che il rischio sia esattamente quello desiderato.
    if 0 < raw_lot_size < MIN_LOT:
        lot_size = MIN_LOT

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

    # Margine approssimativo:
    # prezzo * contract size * lotti * 2%, convertito in EUR.
    estimated_margin_eur = (
        entry
        * CONTRACT_SIZE
        * lot_size
        * (MARGIN_PERCENT / 100)
        * EUR_PER_USD_MOVE_PER_LOT
    )

    rr_tp1 = (
        tp1_profit_eur / actual_risk_eur
        if actual_risk_eur > 0
        else 0.0
    )
    rr_tp2 = (
        tp2_profit_eur / actual_risk_eur
        if actual_risk_eur > 0
        else 0.0
    )

    return {
        "entry": entry,
        "stop_loss": stop_loss,
        "tp1": tp1,
        "tp2": tp2,
        "lot_size": lot_size,
        "size_label": size_label,
        "target_risk_eur": target_risk_eur,
        "actual_risk_eur": actual_risk_eur,
        "tp1_profit_eur": tp1_profit_eur,
        "tp2_profit_eur": tp2_profit_eur,
        "estimated_margin_eur": estimated_margin_eur,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2,
        "raw_lot_size": raw_lot_size,
    }


def setup_label(score: int) -> str:
    if score >= 91:
        return "SETUP ECCELLENTE - 5 STELLE"
    if score >= 85:
        return "SETUP OTTIMO - 4 STELLE"
    if score >= 76:
        return "SETUP BUONO - 3 STELLE"
    if score >= 70:
        return "SETUP IN PREPARAZIONE - 2 STELLE"
    return "NESSUN SETUP"


def duration_estimate(state: str) -> str:
    if state == "VERDE":
        return "Indicativamente 6-48 ore"
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
    plan: dict[str, float | str],
) -> str:
    # Solo caratteri ASCII: niente simboli corrotti su Telegram.
    if state == "ROSSO":
        return (
            "[ROSSO] BTC Trend AI v0.7.1\n\n"
            "NESSUN SETUP\n\n"
            f"Direzione osservata: {direction}\n"
            f"Score: {score}/100\n"
            f"Qualita' mercato: {quality}/100\n\n"
            f"AZIONE: {action}\n\n"
            "Il bot continua a controllare il mercato."
        )

    lot_size = float(plan["lot_size"])
    raw_lot_size = float(plan["raw_lot_size"])

    minimum_warning = ""

    if 0 < raw_lot_size < MIN_LOT:
        minimum_warning = (
            "\nATTENZIONE: la size teorica sarebbe inferiore "
            "al minimo di 0.01 lotti. Con 0.01 lotti il rischio "
            "reale sara' leggermente diverso.\n"
        )

    state_warning = (
        "Ingresso anticipato: conferme non complete."
        if state == "GIALLO"
        else "Setup confermato dai filtri del bot."
    )

    return (
        f"[{state}] BTC Trend AI v0.7.1\n\n"
        f"{setup_label(score)}\n\n"
        f"{direction}\n\n"
        f"Entrata: {float(plan['entry']):.2f}\n"
        f"Stop Loss: {float(plan['stop_loss']):.2f}\n"
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
        f"Rischio/Rendimento TP1: "
        f"1:{float(plan['rr_tp1']):.1f}\n"
        f"Rischio/Rendimento TP2: "
        f"1:{float(plan['rr_tp2']):.1f}\n\n"
        f"Affidabilita' tecnica: {score}/100\n"
        f"Qualita' mercato: {quality}/100\n"
        f"Durata stimata: {duration_estimate(state)}\n\n"
        f"AZIONE CONSIGLIATA: {action}\n"
        f"{minimum_warning}"
        f"ATTENZIONE: {state_warning}\n"
        "I livelli sono indicativi e non garantiscono profitto."
    )


async def notify_state_change(
    bot: Bot,
    state_key: str,
    message: str,
) -> None:
    global last_notified_state

    if state_key == last_notified_state:
        return

    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
    )

    last_notified_state = state_key


# =========================
# CICLO PRINCIPALE
# =========================

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

    direction, score, _reasons = choose_best_direction(
        h4,
        h1,
        m15,
    )

    quality = market_quality(h4, h1)

    state, action = determine_state(
        direction,
        score,
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

    now = datetime.now().strftime("%H:%M:%S")

    print(
        f"\n{now}\n{message}",
        flush=True,
    )

    # Invia solo quando cambia colore o direzione.
    state_key = f"{state}|{direction}"

    await notify_state_change(
        bot,
        state_key,
        message,
    )


async def main() -> None:
    print(
        "BTC Trend AI v0.7.1 avviato",
        flush=True,
    )

    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN mancante")

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID mancante")

    headers = {
        "User-Agent": "BTC-Trend-AI/0.7.1",
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
