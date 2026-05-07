import os
import time
import logging
import hmac
import hashlib
import csv
import json
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv
from strategy_profiles import load_strategy
from sta5_strategy import get_sta5_signal
from db import init_db, save_account_snapshot, save_bot_log, save_trade
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
db_log_handler_attached = False

API_KEY            = os.getenv("BINANCE_API_KEY")
SECRET_KEY         = os.getenv("BINANCE_SECRET_KEY")
TG_TOKEN           = os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID         = os.getenv("TELEGRAM_CHAT_ID")
SYMBOL             = os.getenv("SYMBOL", "BTCUSDT")

BASE_URL           = os.getenv("BINANCE_FUTURES_URL", "https://demo-fapi.binance.com")
INVEST_USDT        = float(os.getenv("INVEST_USDT", 100))
ACTIVE_STRATEGY    = load_strategy(os.environ)
LEVERAGE           = ACTIVE_STRATEGY.leverage

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
ACTIVE_MIN_SIGNAL_SCORE = ACTIVE_STRATEGY.min_signal_score or MIN_SIGNAL_SCORE
ALLOW_COUNTER_TREND = os.getenv("ALLOW_COUNTER_TREND", "false").lower() == "true"
MIN_EMA_DISTANCE   = float(os.getenv("MIN_EMA_DISTANCE", 0.0002))     # 0.02%, relaxed from 0.05%
MIN_BB_WIDTH       = float(os.getenv("MIN_BB_WIDTH", 0.0005))         # 0.05%, relaxed from 0.10%

BREAKEVEN_PNL_PCT  = ACTIVE_STRATEGY.breakeven_pnl_pct                # leveraged PnL %
TRAIL_START_PNL_PCT = ACTIVE_STRATEGY.trail_start_pnl_pct             # leveraged PnL %
TRAIL_LEV_PCT      = ACTIVE_STRATEGY.trail_lev_pct                    # leveraged %
EARLY_EXIT_LOSS_PCT = ACTIVE_STRATEGY.early_exit_loss_pct             # leveraged PnL %
MAX_CANDLES_HELD   = ACTIVE_STRATEGY.max_candles_held

TRADE_LOG_FILE     = os.getenv("TRADE_LOG_FILE", "trade_log.csv")

# Telegram messages are sent with requests to avoid async event-loop/pool issues in Docker.

# Runtime risk state
daily_realized_pnl = 0.0
consecutive_losses = 0
daily_trade_count = 0
account_balance = float(os.getenv("ACCOUNT_BALANCE_USDT", INVEST_USDT * 10))
sta3_pending_signal = None


class DatabaseLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            save_bot_log({
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "pathname": record.pathname,
                "line_no": record.lineno,
            })
        except Exception:
            # Logging must never interrupt trading or recursively log failures.
            pass


def install_db_log_handler() -> None:
    global db_log_handler_attached
    if db_log_handler_attached:
        return
    handler = DatabaseLogHandler(level=logging.INFO)
    log.addHandler(handler)
    db_log_handler_attached = True


# ──────────────────────────────────────────────────────────────────────────────
# Telegram
# ──────────────────────────────────────────────────────────────────────────────

def notify(msg: str) -> None:
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": msg},
            timeout=10,
        )
        if not r.ok:
            log.error(f"Telegram error: {r.status_code} {r.text[:500]}")
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


def set_leverage() -> bool:
    try:
        api_request(
            "POST",
            "/fapi/v1/leverage",
            signed=True,
            params={"symbol": SYMBOL, "leverage": LEVERAGE},
        )
        log.info(f"[LEVERAGE] {LEVERAGE}x set for {SYMBOL}")
        return True
    except requests.exceptions.HTTPError as e:
        response = getattr(e, "response", None)
        details = ""
        if response is not None:
            try:
                details = response.text[:500]
            except Exception:
                details = str(response)
        log.warning(
            "[LEVERAGE] Unable to set %sx for %s. Continuing with exchange-side leverage. %s",
            LEVERAGE,
            SYMBOL,
            details or str(e),
        )
        return False


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


def get_account_info() -> dict:
    return api_request("GET", "/fapi/v2/account", signed=True)


def get_symbol_unrealized_pnl() -> float | None:
    account = get_account_info()
    for position in account.get("positions", []):
        if position.get("symbol") != SYMBOL:
            continue
        amount = float(position.get("positionAmt", 0) or 0)
        if amount == 0:
            return 0.0
        value = position.get("unrealizedProfit", position.get("unRealizedProfit"))
        return float(value or 0)
    return None


def record_account_snapshot(reason: str) -> None:
    try:
        account = get_account_info()
        save_account_snapshot(account)
        log.info("[DB] Account snapshot saved: %s", reason)
    except Exception as e:
        log.warning("[DB] Account snapshot skipped (%s): %s", reason, e)


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


def ema_series(values: list[float], period: int) -> list[float]:
    if len(values) < period:
        raise ValueError(f"Need at least {period} values for EMA series")
    k = 2 / (period + 1)
    result = []
    val = values[0]
    for idx, v in enumerate(values):
        if idx == 0:
            val = v
        else:
            val = v * k + val * (1 - k)
        result.append(val)
    return result


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


def sta3_side_metrics(
    direction: str,
    ema9_value: float,
    ema21_value: float,
    rsi14: float,
    vol_ratio: float,
    higher_trend: str,
) -> dict:
    desired_trend = "BULLISH" if direction == "LONG" else "BEARISH"
    ema_ok = ema9_value > ema21_value if direction == "LONG" else ema9_value < ema21_value

    if direction == "LONG":
        rsi_min = ACTIVE_STRATEGY.rsi_long_min
        rsi_max = ACTIVE_STRATEGY.rsi_long_max
    else:
        rsi_min = ACTIVE_STRATEGY.rsi_short_min
        rsi_max = ACTIVE_STRATEGY.rsi_short_max

    rsi_ok = True
    if rsi_min is not None:
        rsi_ok = rsi_ok and rsi14 >= rsi_min
    if rsi_max is not None:
        rsi_ok = rsi_ok and rsi14 <= rsi_max

    volume_ok = (
        not ACTIVE_STRATEGY.require_volume_spike
        or vol_ratio >= ACTIVE_STRATEGY.volume_spike_ratio
    )
    trend_ok = (
        not ACTIVE_STRATEGY.use_higher_tf_trend_filter
        or higher_trend == desired_trend
        or ALLOW_COUNTER_TREND
    )

    reasons = []
    if not ema_ok:
        reasons.append("EMA confirmation failed")
    if not rsi_ok:
        reasons.append(
            f"RSI {rsi14:.2f} outside {direction.lower()} range "
            f"{rsi_min if rsi_min is not None else '-'}-{rsi_max if rsi_max is not None else '-'}"
        )
    if not volume_ok:
        reasons.append(
            f"volume {vol_ratio:.2f}x below {ACTIVE_STRATEGY.volume_spike_ratio:.2f}x"
        )
    if not trend_ok:
        reasons.append(
            f"5m trend is {higher_trend}; set ALLOW_COUNTER_TREND=true to override"
        )

    score = sum(int(flag) for flag in (ema_ok, rsi_ok, volume_ok, trend_ok))
    valid = ema_ok and rsi_ok and volume_ok and trend_ok and score >= ACTIVE_MIN_SIGNAL_SCORE
    return {
        "score": score,
        "valid": valid,
        "reasons": reasons,
    }


def resolve_sta3_signal(
    closed_times: list[int],
    crossover_direction: str | None,
    invalid_reasons: list[str],
    higher_trend: str,
    rsi14: float,
    vol_ratio: float,
    ema9_value: float,
    ema21_value: float,
) -> dict:
    global sta3_pending_signal

    long_metrics = sta3_side_metrics("LONG", ema9_value, ema21_value, rsi14, vol_ratio, higher_trend)
    short_metrics = sta3_side_metrics("SHORT", ema9_value, ema21_value, rsi14, vol_ratio, higher_trend)

    direction = None
    confirmed_signal = None
    signal_state = "idle"
    signal_reason = "no EMA crossover"
    pending_direction = None
    pending_waited = 0
    last_closed_time = closed_times[-1]

    if sta3_pending_signal and sta3_pending_signal["signal_candle_time"] not in closed_times:
        sta3_pending_signal = None

    if sta3_pending_signal:
        pending_direction = sta3_pending_signal["direction"]
        signal_idx = closed_times.index(sta3_pending_signal["signal_candle_time"])
        pending_waited = len(closed_times) - 1 - signal_idx

        if crossover_direction and crossover_direction != pending_direction:
            sta3_pending_signal = {
                "direction": crossover_direction,
                "signal_candle_time": last_closed_time,
            }
            pending_direction = crossover_direction
            pending_waited = 0
            signal_state = "pending"
            signal_reason = (
                f"{pending_direction.lower()} crossover replaced the opposite pending signal; "
                f"waiting {ACTIVE_STRATEGY.confirmation_candles} candles"
            )
        elif pending_waited < ACTIVE_STRATEGY.confirmation_candles:
            signal_state = "pending"
            signal_reason = (
                f"{pending_direction.lower()} crossover waiting "
                f"{pending_waited}/{ACTIVE_STRATEGY.confirmation_candles} closed candles"
            )
        else:
            metrics = long_metrics if pending_direction == "LONG" else short_metrics
            if invalid_reasons:
                signal_state = "blocked"
                signal_reason = (
                    f"{pending_direction.lower()} confirmation blocked: "
                    + ", ".join(invalid_reasons)
                )
            elif metrics["valid"]:
                direction = pending_direction
                confirmed_signal = pending_direction
                signal_state = "confirmed"
                signal_reason = (
                    f"{pending_direction.lower()} crossover confirmed after "
                    f"{pending_waited} closed candles"
                )
            else:
                signal_state = "blocked"
                signal_reason = (
                    f"{pending_direction.lower()} confirmation failed: "
                    + ", ".join(metrics["reasons"])
                )
            sta3_pending_signal = None
            pending_direction = None
            pending_waited = 0
    elif crossover_direction:
        sta3_pending_signal = {
            "direction": crossover_direction,
            "signal_candle_time": last_closed_time,
        }
        pending_direction = crossover_direction
        signal_state = "pending"
        signal_reason = (
            f"{crossover_direction.lower()} crossover detected; waiting "
            f"{ACTIVE_STRATEGY.confirmation_candles} closed candles"
        )
    elif invalid_reasons:
        signal_state = "blocked"
        signal_reason = "invalid: " + ", ".join(invalid_reasons)

    return {
        "direction": direction,
        "confirmed_signal": confirmed_signal,
        "signal_state": signal_state,
        "signal_reason": signal_reason,
        "pending_direction": pending_direction,
        "pending_waited_candles": pending_waited,
        "long_score": long_metrics["score"],
        "short_score": short_metrics["score"],
    }


def get_signal_snapshot() -> dict:
    k1 = get_klines("1m", 220)
    k5 = get_klines("5m", 80)

    closes = [float(k[4]) for k in k1]
    closed_times = [int(k[0]) for k in k1[:-1]]
    highs = [float(k[2]) for k in k1]
    lows = [float(k[3]) for k in k1]
    volumes = [float(k[5]) for k in k1]

    closes5 = [float(k[4]) for k in k5]
    closed_closes = closes[:-1]
    closed_highs = highs[:-1]
    closed_lows = lows[:-1]
    closed_volumes = volumes[:-1]
    closed_closes5 = closes5[:-1]

    price = closes[-1]
    ema9_values = ema_series(closes, 9)
    ema21_values = ema_series(closes, 21)
    ema9 = ema9_values[-1]
    ema21 = ema21_values[-1]
    ema50_5m_now = ema(closes5[-60:], 50)
    ema50_5m_prev = ema(closes5[-61:-1], 50)
    closed_ema9 = ema9_values[-2]
    closed_ema21 = ema21_values[-2]
    closed_price = closed_closes[-1]

    closed_ema50_5m_now = ema(closed_closes5[-60:], 50)
    closed_ema50_5m_prev = ema(closed_closes5[-61:-1], 50)

    rsi14 = rsi(closes[-40:], 14)
    atr14 = atr(k1[-30:], 14)
    adx14 = adx(k1[-40:], 14)
    vw = vwap(k1[-120:])
    bb_width = bollinger_width(closes[-30:], 20)
    closed_rsi14 = rsi(closed_closes[-40:], 14)
    closed_atr14 = atr(k1[:-1][-30:], 14)
    closed_adx14 = adx(k1[:-1][-40:], 14)
    closed_vw = vwap(k1[:-1][-120:])
    closed_bb_width = bollinger_width(closed_closes[-30:], 20)

    avg_vol20 = sum(volumes[-21:-1]) / 20
    vol_ratio = volumes[-1] / avg_vol20 if avg_vol20 else 0
    closed_avg_vol20 = sum(closed_volumes[-21:-1]) / 20 if len(closed_volumes) >= 21 else 0
    closed_vol_ratio = closed_volumes[-1] / closed_avg_vol20 if closed_avg_vol20 else 0

    # Binance demo/testnet can report very thin/odd candle volume.
    # Do not let a near-zero testnet volume feed permanently block all trades.
    if avg_vol20 <= 0 or volumes[-1] <= 0:
        vol_ratio = 1.0
    if closed_avg_vol20 <= 0 or closed_volumes[-1] <= 0:
        closed_vol_ratio = 1.0

    spread = get_order_book_spread()
    funding = abs(get_funding_rate())
    spread_slippage = spread + EST_SLIPPAGE

    ema_distance = abs(ema9 - ema21) / price if price else 0
    closed_ema_distance = abs(closed_ema9 - closed_ema21) / closed_price if closed_price else 0

    higher_trend = "NEUTRAL"
    if price > ema50_5m_now and ema50_5m_now > ema50_5m_prev:
        higher_trend = "BULLISH"
    elif price < ema50_5m_now and ema50_5m_now < ema50_5m_prev:
        higher_trend = "BEARISH"

    closed_higher_trend = "NEUTRAL"
    if closed_price > closed_ema50_5m_now and closed_ema50_5m_now > closed_ema50_5m_prev:
        closed_higher_trend = "BULLISH"
    elif closed_price < closed_ema50_5m_now and closed_ema50_5m_now < closed_ema50_5m_prev:
        closed_higher_trend = "BEARISH"

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
    movement_lookback = min(ACTIVE_STRATEGY.movement_lookback, len(highs), len(lows))
    recent_range = max(highs[-movement_lookback:]) - min(lows[-movement_lookback:])
    movement_points = ACTIVE_STRATEGY.movement_value(atr14, candle_range, recent_range)

    if candle_range > 0 and candle_body / candle_range < 0.25 and candle_range / price > 0.003:
        invalid_reasons.append("abnormal wick")

    movement_rejection = ACTIVE_STRATEGY.movement_rejection(movement_points)
    if movement_rejection:
        invalid_reasons.append(movement_rejection)

    approx_qty = calc_quantity(price)
    tp_distance, stop_distance = ACTIVE_STRATEGY.exit_distances(price, approx_qty)

    tp_pnl_pct = (tp_distance / price) * LEVERAGE * 100 if price else 0
    sl_pnl_pct = (stop_distance / price) * LEVERAGE * 100 if price else 0

    direction = None
    signal_reason = "no setup"
    confirmed_signal = None
    signal_state = "direct"
    pending_direction = None
    pending_waited_candles = 0
    # sta5 strategy - RSI + candle color analysis
    if ACTIVE_STRATEGY.name == "sta5":
        sta5_result = get_sta5_signal(k1, rsi14)
        direction = sta5_result["direction"]
        signal_reason = sta5_result["signal_reason"]
        signal_state = "sta5_signal"
        long_score = sta5_result["score"] if direction == "LONG" else 0
        short_score = sta5_result["score"] if direction == "SHORT" else 0
        
        if direction:
            log.info(f"[STA5] Signal generated: {direction} - {signal_reason}")
        else:
            log.info(f"[STA5] No trade: {signal_reason}")

    elif ACTIVE_STRATEGY.name == "sta3":
        closed_fast_prev = ema9_values[-3]
        closed_slow_prev = ema21_values[-3]
        closed_fast_now = closed_ema9
        closed_slow_now = closed_ema21

        sta3_invalid_reasons = []
        if closed_rsi14 > 75:
            sta3_invalid_reasons.append("RSI overbought")
        if closed_rsi14 < 25:
            sta3_invalid_reasons.append("RSI oversold")
        if closed_adx14 < MIN_ADX:
            sta3_invalid_reasons.append("ADX too weak")
        if closed_ema_distance < MIN_EMA_DISTANCE:
            sta3_invalid_reasons.append("EMA9/EMA21 too close")
        if closed_bb_width < MIN_BB_WIDTH:
            sta3_invalid_reasons.append("Bollinger width too low")
        if spread > MAX_SPREAD:
            sta3_invalid_reasons.append("spread too high")
        if spread_slippage > MAX_SPREAD_SLIPPAGE:
            sta3_invalid_reasons.append("spread + slippage too high")
        if funding > MAX_FUNDING:
            sta3_invalid_reasons.append("funding too high")

        closed_candle_body = abs(closed_closes[-1] - float(k1[-2][1]))
        closed_candle_range = closed_highs[-1] - closed_lows[-1]
        if (
            closed_candle_range > 0
            and closed_candle_body / closed_candle_range < 0.25
            and closed_candle_range / closed_price > 0.003
        ):
            sta3_invalid_reasons.append("abnormal wick")

        crossover_direction = None
        if closed_fast_prev <= closed_slow_prev and closed_fast_now > closed_slow_now:
            crossover_direction = "LONG"
        elif closed_fast_prev >= closed_slow_prev and closed_fast_now < closed_slow_now:
            crossover_direction = "SHORT"

        sta3_signal = resolve_sta3_signal(
            closed_times=closed_times,
            crossover_direction=crossover_direction,
            invalid_reasons=sta3_invalid_reasons,
            higher_trend=closed_higher_trend,
            rsi14=closed_rsi14,
            vol_ratio=closed_vol_ratio,
            ema9_value=closed_ema9,
            ema21_value=closed_ema21,
        )
        direction = sta3_signal["direction"]
        confirmed_signal = sta3_signal["confirmed_signal"]
        signal_state = sta3_signal["signal_state"]
        signal_reason = sta3_signal["signal_reason"]
        pending_direction = sta3_signal["pending_direction"]
        pending_waited_candles = sta3_signal["pending_waited_candles"]
        long_score = sta3_signal["long_score"]
        short_score = sta3_signal["short_score"]
        higher_trend = closed_higher_trend
        rsi14 = closed_rsi14
        atr14 = closed_atr14
        adx14 = closed_adx14
        vw = closed_vw
        bb_width = closed_bb_width
        vol_ratio = closed_vol_ratio
        ema9 = closed_ema9
        ema21 = closed_ema21
        invalid_reasons = sta3_invalid_reasons
    elif not invalid_reasons:
        long_trend_ok = higher_trend == "BULLISH" or ALLOW_COUNTER_TREND
        short_trend_ok = higher_trend == "BEARISH" or ALLOW_COUNTER_TREND

        long_valid = long_score >= ACTIVE_MIN_SIGNAL_SCORE and long_trend_ok
        short_valid = short_score >= ACTIVE_MIN_SIGNAL_SCORE and short_trend_ok

        # Choose the stronger signal. If tied, prefer the higher-timeframe trend direction.
        if long_valid and short_valid:
            if short_score > long_score:
                direction = "SHORT"
            elif long_score > short_score:
                direction = "LONG"
            elif higher_trend == "BEARISH":
                direction = "SHORT"
            elif higher_trend == "BULLISH":
                direction = "LONG"
            else:
                direction = "LONG"

            signal_reason = (
                f"{direction.lower()} selected from both valid signals "
                f"(long={long_score}, short={short_score}, trend={higher_trend})"
            )

        elif long_valid:
            direction = "LONG"
            signal_reason = (
                "long setup confirmed"
                if higher_trend == "BULLISH"
                else "long setup confirmed by test-mode counter-trend override"
            )

        elif short_valid:
            direction = "SHORT"
            signal_reason = (
                "short setup confirmed"
                if higher_trend == "BEARISH"
                else "short setup confirmed by test-mode counter-trend override"
            )

        elif long_score >= ACTIVE_MIN_SIGNAL_SCORE and higher_trend != "BULLISH":
            signal_reason = (
                f"long score {long_score}, but 5m trend is {higher_trend}; "
                "set ALLOW_COUNTER_TREND=true for demo/testnet override"
            )

        elif short_score >= ACTIVE_MIN_SIGNAL_SCORE and higher_trend != "BEARISH":
            signal_reason = (
                f"short score {short_score}, but 5m trend is {higher_trend}; "
                "set ALLOW_COUNTER_TREND=true for demo/testnet override"
            )

        else:
            signal_reason = (
                f"scores too low: long={long_score}, short={short_score}, "
                f"required={ACTIVE_MIN_SIGNAL_SCORE}"
            )
    else:
        signal_state = "blocked"
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
        "last_candle_range": candle_range,
        "recent_range_points": recent_range,
        "movement_points": movement_points,
        "movement_source": ACTIVE_STRATEGY.movement_source,
        "higher_trend": higher_trend,
        "long_score": long_score,
        "short_score": short_score,
        "invalid_reasons": invalid_reasons,
        "stop_distance": stop_distance,
        "tp_distance": tp_distance,
        "tp_pnl_pct": tp_pnl_pct,
        "sl_pnl_pct": sl_pnl_pct,
        "direction": direction,
        "confirmed_signal": confirmed_signal,
        "signal_state": signal_state,
        "signal_reason": signal_reason,
        "pending_direction": pending_direction,
        "pending_waited_candles": pending_waited_candles,
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
    if (
        ACTIVE_STRATEGY.daily_profit_target_usdt is not None
        and daily_realized_pnl >= ACTIVE_STRATEGY.daily_profit_target_usdt
    ):
        return (
            False,
            f"{ACTIVE_STRATEGY.name} profit target reached: "
            f"${daily_realized_pnl:.2f}/${ACTIVE_STRATEGY.daily_profit_target_usdt:.2f}",
        )
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
        "daily_pnl_after_trade", "consecutive_losses", "candles_held",
        "strategy", "target_profit_usdt", "unrealized_profit_target_usdt",
        "daily_profit_target_usdt", "movement_points"
    ]

    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fields})

    try:
        save_trade(row)
    except Exception as e:
        log.warning("[DB] Trade save skipped for %s: %s", row.get("trade_id", "unknown"), e)


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


def startup_message() -> str:
    lines = [
        f"✅ BOT STARTED — {SYMBOL}",
        f"Strategy: {ACTIVE_STRATEGY.name}",
        f"Settings: {ACTIVE_STRATEGY.startup_summary()}",
        f"Margin: ${INVEST_USDT:g}",
        f"Daily loss limit: ${active_daily_loss_limit():.2f}",
        f"Max trades/day: {MAX_DAILY_TRADES}",
        f"Max consecutive losses: {MAX_CONSEC_LOSSES}",
        f"Counter-trend: {ALLOW_COUNTER_TREND}",
    ]
    if ACTIVE_STRATEGY.daily_profit_target_usdt is not None:
        lines.append(f"Daily profit target: ${ACTIVE_STRATEGY.daily_profit_target_usdt:.2f}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Main bot loop
# ──────────────────────────────────────────────────────────────────────────────

def run_bot() -> None:
    global daily_realized_pnl, consecutive_losses, daily_trade_count

    if not API_KEY or not SECRET_KEY:
        raise RuntimeError("BINANCE_API_KEY and BINANCE_SECRET_KEY are required")

    init_db()
    install_db_log_handler()

    log.info(
        f"Starting improved scalping bot | {SYMBOL} | ${INVEST_USDT} margin | "
        f"{ACTIVE_STRATEGY.startup_summary()} | "
        f"counter_trend={ALLOW_COUNTER_TREND}"
    )
    set_leverage()
    record_account_snapshot("startup")
    notify(startup_message())

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
                "[TA] strategy=%s state=%s price=%.2f trend=%s EMA9=%.2f EMA21=%.2f RSI=%.2f ADX=%.2f "
                "move(%s)=%.2f VOL=%.2f LS=%s SS=%s TPpts=%.2f SLpts=%.2f "
                "TPlev=%.2f%% SLlev=%.2f%% "
                "min_score=%s counter_trend=%s pending=%s waited=%s invalid=%s",
                ACTIVE_STRATEGY.name,
                snap.get("signal_state", "direct"),
                snap["price"],
                snap["higher_trend"],
                snap["ema9"],
                snap["ema21"],
                snap["rsi14"],
                snap["adx14"],
                snap["movement_source"],
                snap["movement_points"],
                snap["vol_ratio"],
                snap["long_score"],
                snap["short_score"],
                snap["tp_distance"],
                snap["stop_distance"],
                snap["tp_pnl_pct"],
                snap["sl_pnl_pct"],
                ACTIVE_MIN_SIGNAL_SCORE,
                ALLOW_COUNTER_TREND,
                snap.get("pending_direction") or "none",
                snap.get("pending_waited_candles", 0),
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

            order = place_market_order(entry_side, qty)
            entry_price = float(order.get("avgPrice") or order.get("price") or price)
            if entry_price == 0:
                entry_price = price

            tp_distance, stop_distance = ACTIVE_STRATEGY.exit_distances(entry_price, qty)
            tp_price, stop_price = calculate_order_levels(
                entry_price,
                direction,
                stop_distance,
                tp_distance,
            )

            snap["tp_distance"] = tp_distance
            snap["stop_distance"] = stop_distance
            snap["tp_pnl_pct"] = (tp_distance / entry_price) * LEVERAGE * 100
            snap["sl_pnl_pct"] = (stop_distance / entry_price) * LEVERAGE * 100

            notional = round(entry_price * qty, 2)
            daily_trade_count += 1
            entry_time = datetime.now(timezone.utc).isoformat()
            trade_id = f"{SYMBOL}-{int(time.time())}"

            log.info(
                "[OPEN] strategy=%s %s qty=%s entry=%.2f TP=%.2f SL=%.2f notional=$%.2f reason=%s",
                ACTIVE_STRATEGY.name,
                direction,
                qty,
                entry_price,
                tp_price,
                stop_price,
                notional,
                snap.get("signal_reason", ""),
            )

            target_line = ""
            if ACTIVE_STRATEGY.target_profit_usdt is not None:
                target_line = f"Target P&L: ${ACTIVE_STRATEGY.target_profit_usdt:.2f}\n"
            elif ACTIVE_STRATEGY.unrealized_profit_target_usdt is not None:
                target_line = (
                    f"Unrealized target: ${ACTIVE_STRATEGY.unrealized_profit_target_usdt:.2f}\n"
                )
            daily_target_line = ""
            if ACTIVE_STRATEGY.daily_profit_target_usdt is not None:
                daily_target_line = f"Daily target: ${ACTIVE_STRATEGY.daily_profit_target_usdt:.2f}\n"

            notify(
                f"{'🚀' if direction == 'LONG' else '🔻'} {direction} OPENED — {SYMBOL}\n"
                f"Strategy: {ACTIVE_STRATEGY.name}\n"
                f"Entry: {entry_price:.2f}\n"
                f"TP: {tp_price:.2f}\n"
                f"SL: {stop_price:.2f}\n"
                f"Move: TP {snap['tp_distance']:.2f} pts / SL {snap['stop_distance']:.2f} pts\n"
                f"{target_line}"
                f"{daily_target_line}"
                f"Leverage: {LEVERAGE}x\n"
                f"Score: L{snap['long_score']} / S{snap['short_score']}\n"
                f"Movement: {snap['movement_points']:.2f} pts ({snap['movement_source']})\n"
                f"Trend: {snap['higher_trend']} | ADX: {snap['adx14']:.2f}\n"
                f"Reason: {snap.get('signal_reason', '')}"
            )
            record_account_snapshot(f"trade opened: {trade_id}")

            breakeven_moved = False
            trailing_active = False
            trail_stop = stop_price
            candles_held = 0
            entry_started = time.time()
            last_live_refresh_candle = -1
            exit_reason = ""

            while True:
                current = get_price_raw()
                pnl_pct = leveraged_pnl_pct(entry_price, current, direction)
                current_pnl_usdt = pnl_usdt(entry_price, current, qty, direction)
                unrealized_pnl_usdt = current_pnl_usdt
                if ACTIVE_STRATEGY.unrealized_profit_target_usdt is not None:
                    try:
                        exchange_unrealized = get_symbol_unrealized_pnl()
                        if exchange_unrealized is not None:
                            unrealized_pnl_usdt = exchange_unrealized
                    except Exception as e:
                        log.warning("[PNL] Exchange unrealized PnL unavailable, using price estimate: %s", e)
                candles_held = int((time.time() - entry_started) // 60)

                hit_sl = current <= trail_stop if direction == "LONG" else current >= trail_stop
                if ACTIVE_STRATEGY.unrealized_profit_target_usdt is not None:
                    hit_tp = False
                else:
                    hit_tp = current >= tp_price if direction == "LONG" else current <= tp_price

                log.info(
                    "[WATCH] current=%.2f pnl=$%.2f unrealized=$%.2f lev=%.2f%% "
                    "TP=%.2f SL/TRAIL=%.2f candles=%s",
                    current, current_pnl_usdt, unrealized_pnl_usdt, pnl_pct,
                    tp_price, trail_stop, candles_held
                )

                if (
                    ACTIVE_STRATEGY.unrealized_profit_target_usdt is not None
                    and unrealized_pnl_usdt >= ACTIVE_STRATEGY.unrealized_profit_target_usdt
                ):
                    exit_reason = "UNREALIZED_TP"
                    break

                if hit_sl:
                    exit_reason = "STOP_LOSS" if not trailing_active else "TRAILING_STOP"
                    break

                if hit_tp:
                    exit_reason = "TAKE_PROFIT"
                    break

                if ACTIVE_STRATEGY.use_breakeven and pnl_pct >= BREAKEVEN_PNL_PCT and not breakeven_moved:
                    trail_stop = entry_price
                    breakeven_moved = True
                    log.info("[BREAKEVEN] Stop moved to entry %.2f", entry_price)
                    notify(f"🔒 {SYMBOL} stop moved to breakeven: {entry_price:.2f}")

                if ACTIVE_STRATEGY.use_trailing and pnl_pct >= TRAIL_START_PNL_PCT:
                    trailing_active = True
                    trail_raw_pct = TRAIL_LEV_PCT / LEVERAGE / 100
                    if direction == "LONG":
                        proposed = round(current * (1 - trail_raw_pct), 2)
                        trail_stop = max(trail_stop, proposed)
                    else:
                        proposed = round(current * (1 + trail_raw_pct), 2)
                        trail_stop = min(trail_stop, proposed)

                # Refresh technicals once per completed minute for early/counter exits.
                if candles_held > 0 and candles_held != last_live_refresh_candle:
                    last_live_refresh_candle = candles_held
                    live = get_signal_snapshot()
                    record_account_snapshot(f"trade open: {trade_id} minute {candles_held}")

                    if (
                        ACTIVE_STRATEGY.use_early_exit
                        and direction == "LONG"
                        and pnl_pct <= EARLY_EXIT_LOSS_PCT
                        and live["ema9"] < live["ema21"]
                    ):
                        exit_reason = "EARLY_EXIT"
                        break

                    if (
                        ACTIVE_STRATEGY.use_early_exit
                        and direction == "SHORT"
                        and pnl_pct <= EARLY_EXIT_LOSS_PCT
                        and live["ema9"] > live["ema21"]
                    ):
                        exit_reason = "EARLY_EXIT"
                        break

                    if (
                        ACTIVE_STRATEGY.use_counter_signal_exit
                        and direction == "LONG"
                        and (
                            (ACTIVE_STRATEGY.name == "sta3" and live.get("confirmed_signal") == "SHORT")
                            or (ACTIVE_STRATEGY.name != "sta3" and live["short_score"] >= 3)
                        )
                    ):
                        exit_reason = "COUNTER_SIGNAL"
                        break

                    if (
                        ACTIVE_STRATEGY.use_counter_signal_exit
                        and direction == "SHORT"
                        and (
                            (ACTIVE_STRATEGY.name == "sta3" and live.get("confirmed_signal") == "LONG")
                            or (ACTIVE_STRATEGY.name != "sta3" and live["long_score"] >= 3)
                        )
                    ):
                        exit_reason = "COUNTER_SIGNAL"
                        break

                if candles_held >= MAX_CANDLES_HELD:
                    exit_reason = "TIME_EXIT"
                    break

                time.sleep(POLL_INTERVAL)

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
                "strategy": ACTIVE_STRATEGY.name,
                "target_profit_usdt": ACTIVE_STRATEGY.target_profit_usdt,
                "unrealized_profit_target_usdt": ACTIVE_STRATEGY.unrealized_profit_target_usdt,
                "daily_profit_target_usdt": ACTIVE_STRATEGY.daily_profit_target_usdt,
                "movement_points": round(snap["movement_points"], 2),
            })
            record_account_snapshot(f"trade closed: {trade_id}")

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
