import os
import time
import logging
import asyncio
import hmac
import hashlib
import csv
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Bot
import nest_asyncio

nest_asyncio.apply()
load_dotenv()

# ──────────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

API_KEY            = os.getenv("BINANCE_API_KEY")
SECRET_KEY         = os.getenv("BINANCE_SECRET_KEY")
TG_TOKEN           = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL             = os.getenv("SYMBOL", "BTCUSDT")

BASE_URL           = os.getenv("BINANCE_FUTURES_URL", "https://demo-fapi.binance.com")
INVEST_USDT        = float(os.getenv("INVEST_USDT", 100))
LEVERAGE           = int(os.getenv("LEVERAGE", 15))

# Improved strategy parameters
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", 0.06))     # 6% circuit breaker
CONSERVATIVE_DAILY_LOSS_PCT = float(os.getenv("CONSERVATIVE_DAILY_LOSS_PCT", 0.04))
USE_CONSERVATIVE_DAILY_STOP = os.getenv("USE_CONSERVATIVE_DAILY_STOP", "false").lower() == "true"

MAX_CONSEC_LOSSES  = int(os.getenv("MAX_CONSEC_LOSSES", 3))
COOLDOWN_SECS      = int(os.getenv("COOLDOWN_SECS", 900))             # 15 minutes
MAX_DAILY_TRADES   = int(os.getenv("MAX_DAILY_TRADES", 12))

POLL_INTERVAL      = int(os.getenv("POLL_INTERVAL", 5))
MAX_SPREAD         = float(os.getenv("MAX_SPREAD", 0.0005))           # 0.05%
MAX_SPREAD_SLIPPAGE = float(os.getenv("MAX_SPREAD_SLIPPAGE", 0.0008)) # 0.08%
EST_SLIPPAGE       = float(os.getenv("EST_SLIPPAGE", 0.0002))         # 0.02%
MAX_FUNDING        = float(os.getenv("MAX_FUNDING", 0.01))            # 1%
MIN_ADX            = float(os.getenv("MIN_ADX", 25))
MIN_VOL_RATIO      = float(os.getenv("MIN_VOL_RATIO", 1.0))             # relaxed from 1.2 for demo/testnet
MIN_SIGNAL_SCORE   = int(os.getenv("MIN_SIGNAL_SCORE", 3))              # use 2 for demo/testnet, 3 for safer live mode
ALLOW_COUNTER_TREND = os.getenv("ALLOW_COUNTER_TREND", "false").lower() == "true"
MIN_EMA_DISTANCE   = float(os.getenv("MIN_EMA_DISTANCE", 0.0002))     # 0.02%, relaxed from 0.05%
MIN_BB_WIDTH       = float(os.getenv("MIN_BB_WIDTH", 0.0005))         # 0.05%, relaxed from 0.10%

ATR_STOP_MULT      = float(os.getenv("ATR_STOP_MULT", 0.8))
MIN_STOP_PCT       = float(os.getenv("MIN_STOP_PCT", 0.0020))         # 0.20%
RR_MULTIPLIER      = float(os.getenv("RR_MULTIPLIER", 1.5))
BREAKEVEN_PNL_PCT  = float(os.getenv("BREAKEVEN_PNL_PCT", 2.0))       # leveraged PnL %
TRAIL_START_PNL_PCT = float(os.getenv("TRAIL_START_PNL_PCT", 3.5))    # leveraged PnL %
TRAIL_LEV_PCT      = float(os.getenv("TRAIL_LEV_PCT", 1.5))           # leveraged %
EARLY_EXIT_LOSS_PCT = float(os.getenv("EARLY_EXIT_LOSS_PCT", -1.5))   # leveraged PnL %
MAX_CANDLES_HELD   = int(os.getenv("MAX_CANDLES_HELD", 15))

TRADE_LOG_FILE     = os.getenv("TRADE_LOG_FILE", "trade_log.csv")

tg_bot = Bot(token=TG_TOKEN) if TG_TOKEN else None

# Runtime risk state
daily_realized_pnl = 0.0
consecutive_losses = 0
daily_trade_count = 0
account_balance = float(os.getenv("ACCOUNT_BALANCE_USDT", INVEST_USDT * 10))


# ──────────────────────────────────────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────────────────────────────────────

def notify(msg: str) -> None:
    if not tg_bot or not TG_CHAT_ID:
        return
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(tg_bot.send_message(chat_id=TG_CHAT_ID, text=msg))
        loop.close()
    except Exception as e:
        log.error(f"Telegram error: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Binance helpers
# ──────────────────────────────────────────────────────────────────────────────

def ts() -> int:
    return int(time.time() * 1000)


def sign(params: dict) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    params["signature"] = hmac.new(
        SECRET_KEY.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    return params


def bheaders() -> dict:
    return {"X-MBX-APIKEY": API_KEY}


def api_request(method: str, path: str, signed: bool = False, **kwargs) -> dict | list:
    params = kwargs.pop("params", {}) or {}
    headers = kwargs.pop("headers", {}) or {}

    if signed:
        params["timestamp"] = ts()
        params = sign(params)
        headers.update(bheaders())

    url = f"{BASE_URL}{path}"
    r = requests.request(method, url, params=params, headers=headers, timeout=20, **kwargs)
    r.raise_for_status()
    return r.json()


def set_leverage() -> None:
    api_request(
        "POST",
        "/fapi/v1/leverage",
        signed=True,
        params={"symbol": SYMBOL, "leverage": LEVERAGE},
    )
    log.info(f"[LEVERAGE] {LEVERAGE}x set for {SYMBOL}")


def get_klines(interval: str, limit: int) -> list:
    return api_request(
        "GET",
        "/fapi/v1/klines",
        params={"symbol": SYMBOL, "interval": interval, "limit": limit},
    )


def get_price_raw() -> float:
    d = api_request("GET", "/fapi/v1/ticker/price", params={"symbol": SYMBOL})
    return float(d["price"])


def get_funding_rate() -> float:
    d = api_request("GET", "/fapi/v1/premiumIndex", params={"symbol": SYMBOL})
    return float(d.get("lastFundingRate", 0))


def get_order_book_spread() -> float:
    d = api_request("GET", "/fapi/v1/ticker/bookTicker", params={"symbol": SYMBOL})
    ask, bid = float(d["askPrice"]), float(d["bidPrice"])
    return (ask - bid) / bid


def place_market_order(side: str, quantity: float, reduce_only: bool = False) -> dict:
    params = {
        "symbol": SYMBOL,
        "side": side,
        "type": "MARKET",
        "quantity": quantity,
    }
    if reduce_only:
        params["reduceOnly"] = "true"

    return api_request("POST", "/fapi/v1/order", signed=True, params=params)


def calc_quantity(price: float) -> float:
    # BTCUSDT usually supports 0.001 step. Adjust this if your symbol has a different lot size.
    return round((INVEST_USDT * LEVERAGE) / price, 3)


# ──────────────────────────────────────────────────────────────────────────────
# Technical indicators
# ──────────────────────────────────────────────────────────────────────────────

def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values for EMA")
    k = 2 / (period + 1)
    val = values[0]
    for v in values[1:]:
        val = v * k + val * (1 - k)
    return val


def rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        raise ValueError("Not enough closes for RSI")

    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(klines: list, period: int = 14) -> float:
    if len(klines) <= period:
        raise ValueError("Not enough candles for ATR")

    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i - 1][4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-period:]) / period


def adx(klines: list, period: int = 14) -> float:
    if len(klines) <= period + 1:
        raise ValueError("Not enough candles for ADX")

    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_high = float(klines[i - 1][2])
        prev_low = float(klines[i - 1][3])
        prev_close = float(klines[i - 1][4])

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)
        tr.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    atr_n = sum(tr[-period:]) / period
    if atr_n == 0:
        return 0.0

    plus_di = 100 * ((sum(plus_dm[-period:]) / period) / atr_n)
    minus_di = 100 * ((sum(minus_dm[-period:]) / period) / atr_n)

    denom = plus_di + minus_di
    if denom == 0:
        return 0.0

    dx = 100 * abs(plus_di - minus_di) / denom
    return dx


def bollinger_width(closes: list[float], period: int = 20, mult: float = 2.0) -> float:
    if len(closes) < period:
        raise ValueError("Not enough closes for Bollinger Bands")

    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    std = variance ** 0.5

    upper = mean + mult * std
    lower = mean - mult * std
    return (upper - lower) / mean if mean else 0.0


def vwap(klines: list) -> float:
    pv_sum = 0.0
    vol_sum = 0.0

    for k in klines:
        high = float(k[2])
        low = float(k[3])
        close = float(k[4])
        volume = float(k[5])
        typical = (high + low + close) / 3
        pv_sum += typical * volume
        vol_sum += volume

    return pv_sum / vol_sum if vol_sum else float(klines[-1][4])


def leveraged_pnl_pct(entry: float, current: float, direction: str) -> float:
    if direction == "LONG":
        raw = (current - entry) / entry
    else:
        raw = (entry - current) / entry
    return raw * LEVERAGE * 100


def get_signal_snapshot() -> dict:
    k1 = get_klines("1m", 220)
    k5 = get_klines("5m", 80)

    closes = [float(k[4]) for k in k1]
    highs = [float(k[2]) for k in k1]
    lows = [float(k[3]) for k in k1]
    volumes = [float(k[5]) for k in k1]

    closes5 = [float(k[4]) for k in k5]

    price = closes[-1]
    ema9 = ema(closes[-50:], 9)
    ema21 = ema(closes[-50:], 21)
    ema50_5m_now = ema(closes5[-60:], 50)
    ema50_5m_prev = ema(closes5[-61:-1], 50)

    rsi14 = rsi(closes[-40:], 14)
    atr14 = atr(k1[-30:], 14)
    adx14 = adx(k1[-40:], 14)
    vw = vwap(k1[-120:])
    bb_width = bollinger_width(closes[-30:], 20)

    avg_vol20 = sum(volumes[-21:-1]) / 20
    vol_ratio = volumes[-1] / avg_vol20 if avg_vol20 else 0

    # Binance demo/testnet can report very thin/odd candle volume.
    # Do not let a near-zero testnet volume feed permanently block all trades.
    if avg_vol20 <= 0 or volumes[-1] <= 0:
        vol_ratio = 1.0

    spread = get_order_book_spread()
    funding = abs(get_funding_rate())
    spread_slippage = spread + EST_SLIPPAGE

    ema_distance = abs(ema9 - ema21) / price if price else 0

    higher_trend = "NEUTRAL"
    if price > ema50_5m_now and ema50_5m_now > ema50_5m_prev:
        higher_trend = "BULLISH"
    elif price < ema50_5m_now and ema50_5m_now < ema50_5m_prev:
        higher_trend = "BEARISH"

    long_score = 0
    long_score += int(ema9 > ema21)
    long_score += int(50 < rsi14 < 68)
    long_score += int(vol_ratio >= MIN_VOL_RATIO)
    long_score += int(price > vw)

    short_score = 0
    short_score += int(ema9 < ema21)
    short_score += int(32 < rsi14 < 50)
    short_score += int(vol_ratio >= MIN_VOL_RATIO)
    short_score += int(price < vw)

    invalid_reasons = []
    if rsi14 > 75:
        invalid_reasons.append("RSI overbought")
    if rsi14 < 25:
        invalid_reasons.append("RSI oversold")
    if adx14 < MIN_ADX:
        invalid_reasons.append("ADX too weak")
    if ema_distance < MIN_EMA_DISTANCE:
        invalid_reasons.append("EMA9/EMA21 too close")
    if bb_width < MIN_BB_WIDTH:
        invalid_reasons.append("Bollinger width too low")
    if spread > MAX_SPREAD:
        invalid_reasons.append("spread too high")
    if spread_slippage > MAX_SPREAD_SLIPPAGE:
        invalid_reasons.append("spread + slippage too high")
    if funding > MAX_FUNDING:
        invalid_reasons.append("funding too high")

    candle_body = abs(closes[-1] - float(k1[-1][1]))
    candle_range = highs[-1] - lows[-1]
    if candle_range > 0 and candle_body / candle_range < 0.25 and candle_range / price > 0.003:
        invalid_reasons.append("abnormal wick")

    stop_distance = max(price * MIN_STOP_PCT, atr14 * ATR_STOP_MULT)
    tp_distance = stop_distance * RR_MULTIPLIER

    direction = None
    signal_reason = "no setup"
    if not invalid_reasons:
        long_trend_ok = higher_trend == "BULLISH" or ALLOW_COUNTER_TREND
        short_trend_ok = higher_trend == "BEARISH" or ALLOW_COUNTER_TREND

        if long_score >= MIN_SIGNAL_SCORE and long_trend_ok:
            direction = "LONG"
            signal_reason = (
                "long setup confirmed"
                if higher_trend == "BULLISH"
                else "long setup confirmed by test-mode counter-trend override"
            )
        elif short_score >= MIN_SIGNAL_SCORE and short_trend_ok:
            direction = "SHORT"
            signal_reason = (
                "short setup confirmed"
                if higher_trend == "BEARISH"
                else "short setup confirmed by test-mode counter-trend override"
            )
        elif long_score >= MIN_SIGNAL_SCORE and higher_trend != "BULLISH":
            signal_reason = (
                f"long score {long_score}, but 5m trend is {higher_trend}; "
                "set ALLOW_COUNTER_TREND=true for demo/testnet override"
            )
        elif short_score >= MIN_SIGNAL_SCORE and higher_trend != "BEARISH":
            signal_reason = (
                f"short score {short_score}, but 5m trend is {higher_trend}; "
                "set ALLOW_COUNTER_TREND=true for demo/testnet override"
            )
        else:
            signal_reason = (
                f"scores too low: long={long_score}, short={short_score}, "
                f"required={MIN_SIGNAL_SCORE}"
            )
    else:
        signal_reason = "invalid: " + ", ".join(invalid_reasons)

    return {
        "price": price,
        "ema9": ema9,
        "ema21": ema21,
        "ema50_5m": ema50_5m_now,
        "ema50_5m_prev": ema50_5m_prev,
        "rsi14": rsi14,
        "atr14": atr14,
        "adx14": adx14,
        "vwap": vw,
        "bb_width": bb_width,
        "vol_ratio": vol_ratio,
        "spread": spread,
        "funding": funding,
        "spread_slippage": spread_slippage,
        "higher_trend": higher_trend,
        "long_score": long_score,
        "short_score": short_score,
        "invalid_reasons": invalid_reasons,
        "stop_distance": stop_distance,
        "tp_distance": tp_distance,
        "direction": direction,
        "signal_reason": signal_reason,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Risk, logging, and trade helpers
# ──────────────────────────────────────────────────────────────────────────────

def active_daily_loss_limit() -> float:
    pct = CONSERVATIVE_DAILY_LOSS_PCT if USE_CONSERVATIVE_DAILY_STOP else MAX_DAILY_LOSS_PCT
    return account_balance * pct


def check_risk_gate() -> tuple[bool, str]:
    if abs(min(daily_realized_pnl, 0)) >= active_daily_loss_limit():
        return False, f"Daily loss limit reached: ${daily_realized_pnl:.2f}"
    if consecutive_losses >= MAX_CONSEC_LOSSES:
        return False, f"{consecutive_losses} consecutive losses; cooldown active"
    if daily_trade_count >= MAX_DAILY_TRADES:
        return False, f"Max daily trades reached: {daily_trade_count}/{MAX_DAILY_TRADES}"
    return True, ""


def pnl_usdt(entry: float, exit_price: float, qty: float, direction: str) -> float:
    if direction == "LONG":
        return round((exit_price - entry) * qty, 2)
    return round((entry - exit_price) * qty, 2)


def write_trade_log(row: dict) -> None:
    file_exists = os.path.exists(TRADE_LOG_FILE)
    fields = [
        "trade_id", "entry_time", "exit_time", "symbol", "direction",
        "entry_price", "exit_price", "margin_used", "leverage", "notional_size",
        "stop_loss_price", "take_profit_price", "exit_reason", "pnl_percent",
        "pnl_usd", "long_score", "short_score", "ema9_at_entry", "ema21_at_entry",
        "ema50_5m_at_entry", "rsi_at_entry", "adx_at_entry", "atr_at_entry",
        "vol_ratio_at_entry", "spread_at_entry", "slippage_estimate",
        "daily_pnl_after_trade", "consecutive_losses", "candles_held"
    ]

    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fields})


def close_position(close_side: str, qty: float, reason: str) -> tuple[float, dict]:
    order = place_market_order(close_side, qty, reduce_only=True)
    price = float(order.get("avgPrice") or order.get("price") or get_price_raw())
    if price == 0:
        price = get_price_raw()
    log.info(f"[CLOSE] {reason} @ {price:.2f}")
    return price, order


def calculate_order_levels(price: float, direction: str, stop_distance: float, tp_distance: float) -> tuple[float, float]:
    if direction == "LONG":
        stop_price = round(price - stop_distance, 2)
        tp_price = round(price + tp_distance, 2)
    else:
        stop_price = round(price + stop_distance, 2)
        tp_price = round(price - tp_distance, 2)
    return tp_price, stop_price


# ──────────────────────────────────────────────────────────────────────────────
# Main bot loop
# ──────────────────────────────────────────────────────────────────────────────

def run_bot() -> None:
    global daily_realized_pnl, consecutive_losses, daily_trade_count

    if not API_KEY or not SECRET_KEY:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_SECRET_KEY are required")

    log.info(
        f"Starting improved scalping bot | {SYMBOL} | ${INVEST_USDT} margin | "
        f"{LEVERAGE}x leverage | min_score={MIN_SIGNAL_SCORE} | "
        f"counter_trend={ALLOW_COUNTER_TREND}"
    )
    set_leverage()

    last_reset = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    while True:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != last_reset:
                daily_realized_pnl = 0.0
                consecutive_losses = 0
                daily_trade_count = 0
                last_reset = today
                log.info("[RISK] Daily counters reset")

            ok, reason = check_risk_gate()
            if not ok:
                log.warning(f"[RISK] {reason}")
                notify(f"⛔ TRADING PAUSED — {SYMBOL}\n{reason}")
                time.sleep(COOLDOWN_SECS)
                continue

            snap = get_signal_snapshot()
            log.info(
                "[TA] price=%.2f trend=%s EMA9=%.2f EMA21=%.2f RSI=%.2f ADX=%.2f "
                "VOL=%.2f LS=%s SS=%s min_score=%s counter_trend=%s invalid=%s",
                snap["price"],
                snap["higher_trend"],
                snap["ema9"],
                snap["ema21"],
                snap["rsi14"],
                snap["adx14"],
                snap["vol_ratio"],
                snap["long_score"],
                snap["short_score"],
                MIN_SIGNAL_SCORE,
                ALLOW_COUNTER_TREND,
                ",".join(snap["invalid_reasons"]) or "none",
            )

            direction = snap["direction"]
            if not direction:
                log.info("[SIGNAL] No trade: %s", snap.get("signal_reason", "unknown"))
                time.sleep(30)
                continue

            price = snap["price"]
            qty = calc_quantity(price)
            entry_side = "BUY" if direction == "LONG" else "SELL"
            close_side = "SELL" if direction == "LONG" else "BUY"

            tp_price, stop_price = calculate_order_levels(
                price,
                direction,
                snap["stop_distance"],
                snap["tp_distance"],
            )

            order = place_market_order(entry_side, qty)
            entry_price = float(order.get("avgPrice") or order.get("price") or price)
            if entry_price == 0:
                entry_price = price

            notional = round(entry_price * qty, 2)
            daily_trade_count += 1
            entry_time = datetime.now(timezone.utc).isoformat()
            trade_id = f"{SYMBOL}-{int(time.time())}"

            log.info(
                "[OPEN] %s qty=%s entry=%.2f TP=%.2f SL=%.2f notional=$%.2f",
                direction, qty, entry_price, tp_price, stop_price, notional
            )

            notify(
                f"{'🚀' if direction == 'LONG' else '🔻'} {direction} OPENED — {SYMBOL}\n"
                f"Entry: {entry_price:.2f}\n"
                f"TP: {tp_price:.2f}\n"
                f"SL: {stop_price:.2f}\n"
                f"Score: L{snap['long_score']} / S{snap['short_score']}\n"
                f"Trend: {snap['higher_trend']} | ADX: {snap['adx14']:.2f}"
            )

            breakeven_moved = False
            trailing_active = False
            trail_stop = stop_price
            candles_held = 0
            exit_reason = ""

            while True:
                current = get_price_raw()
                pnl_pct = leveraged_pnl_pct(entry_price, current, direction)

                hit_sl = current <= trail_stop if direction == "LONG" else current >= trail_stop
                hit_tp = current >= tp_price if direction == "LONG" else current <= tp_price

                log.info(
                    "[WATCH] current=%.2f pnl=%.2f%% TP=%.2f SL/TRAIL=%.2f candles=%s",
                    current, pnl_pct, tp_price, trail_stop, candles_held
                )

                if hit_sl:
                    exit_reason = "STOP_LOSS" if not trailing_active else "TRAILING_STOP"
                    break

                if hit_tp:
                    exit_reason = "TAKE_PROFIT"
                    break

                if pnl_pct >= BREAKEVEN_PNL_PCT and not breakeven_moved:
                    trail_stop = entry_price
                    breakeven_moved = True
                    log.info("[BREAKEVEN] Stop moved to entry %.2f", entry_price)
                    notify(f"🔒 {SYMBOL} stop moved to breakeven: {entry_price:.2f}")

                if pnl_pct >= TRAIL_START_PNL_PCT:
                    trailing_active = True
                    trail_raw_pct = TRAIL_LEV_PCT / LEVERAGE / 100
                    if direction == "LONG":
                        proposed = round(current * (1 - trail_raw_pct), 2)
                        trail_stop = max(trail_stop, proposed)
                    else:
                        proposed = round(current * (1 + trail_raw_pct), 2)
                        trail_stop = min(trail_stop, proposed)

                # Refresh technicals once per candle-equivalent interval for early/counter exits.
                if candles_held > 0:
                    live = get_signal_snapshot()

                    if direction == "LONG" and pnl_pct <= EARLY_EXIT_LOSS_PCT and live["ema9"] < live["ema21"]:
                        exit_reason = "EARLY_EXIT"
                        break

                    if direction == "SHORT" and pnl_pct <= EARLY_EXIT_LOSS_PCT and live["ema9"] > live["ema21"]:
                        exit_reason = "EARLY_EXIT"
                        break

                    if direction == "LONG" and live["short_score"] >= 3:
                        exit_reason = "COUNTER_SIGNAL"
                        break

                    if direction == "SHORT" and live["long_score"] >= 3:
                        exit_reason = "COUNTER_SIGNAL"
                        break

                if candles_held >= MAX_CANDLES_HELD:
                    exit_reason = "TIME_EXIT"
                    break

                time.sleep(60)
                candles_held += 1

            exit_price, _ = close_position(close_side, qty, exit_reason)
            exit_time = datetime.now(timezone.utc).isoformat()
            trade_pnl = pnl_usdt(entry_price, exit_price, qty, direction)
            trade_pnl_pct = leveraged_pnl_pct(entry_price, exit_price, direction)

            daily_realized_pnl += trade_pnl
            if trade_pnl < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0

            write_trade_log({
                "trade_id": trade_id,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "symbol": SYMBOL,
                "direction": direction,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "margin_used": INVEST_USDT,
                "leverage": LEVERAGE,
                "notional_size": notional,
                "stop_loss_price": stop_price,
                "take_profit_price": tp_price,
                "exit_reason": exit_reason,
                "pnl_percent": round(trade_pnl_pct, 2),
                "pnl_usd": trade_pnl,
                "long_score": snap["long_score"],
                "short_score": snap["short_score"],
                "ema9_at_entry": round(snap["ema9"], 2),
                "ema21_at_entry": round(snap["ema21"], 2),
                "ema50_5m_at_entry": round(snap["ema50_5m"], 2),
                "rsi_at_entry": round(snap["rsi14"], 2),
                "adx_at_entry": round(snap["adx14"], 2),
                "atr_at_entry": round(snap["atr14"], 2),
                "vol_ratio_at_entry": round(snap["vol_ratio"], 2),
                "spread_at_entry": round(snap["spread"], 6),
                "slippage_estimate": EST_SLIPPAGE,
                "daily_pnl_after_trade": round(daily_realized_pnl, 2),
                "consecutive_losses": consecutive_losses,
                "candles_held": candles_held,
            })

            notify(
                f"🏁 POSITION CLOSED — {SYMBOL}\n"
                f"Reason: {exit_reason}\n"
                f"Exit: {exit_price:.2f}\n"
                f"P&L: {'+' if trade_pnl >= 0 else ''}${trade_pnl} "
                f"({trade_pnl_pct:.2f}% leveraged)\n"
                f"Daily P&L: ${daily_realized_pnl:.2f}\n"
                f"Loss streak: {consecutive_losses}"
            )

            time.sleep(5)

        except requests.HTTPError as e:
            msg = e.response.text if e.response is not None else str(e)
            log.error(f"HTTP error: {msg}")
            notify(f"⚠️ HTTP error — {SYMBOL}\n{msg[:1000]}")
            time.sleep(10)

        except Exception as e:
            log.exception(f"Unexpected error: {e}")
            notify(f"⚠️ Bot error — {SYMBOL}\n{str(e)[:1000]}")
            time.sleep(10)


if __name__ == "__main__":
    run_bot()
