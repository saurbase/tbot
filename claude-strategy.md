# Scalping Bot Strategy — Training Document

## Overview

This document defines the complete trading strategy for a scalping bot operating on cryptocurrency futures markets. Follow every rule precisely. No rule is optional.

---

## Core Parameters

| Parameter | Value |
|---|---|
| Base investment per trade | $100 |
| Leverage | 30x |
| Notional position size | $3,000 |
| Take profit price move | 100 points |
| Stop loss price move | 100 points |
| Approx. leveraged TP/SL | Depends on entry price; `100 / entry_price * 30` |
| Risk-to-reward ratio | 1 : 1 |
| Minimum win rate to break even | Above 50% before fees/slippage |
| Target win rate | ≥ 55% |
| Timeframe | 1-minute candles |

---

## Part 1 — Market Analysis

### 1.1 Pre-Trade Environment Check

Before scanning for signals, the bot must verify all of the following. If any condition fails, skip the candle and wait.

- Spread is less than 0.05%
- No major news event within the next 5 minutes
- Daily loss has not reached the −6% circuit breaker
- Consecutive loss streak is fewer than 3
- ADX (14) is above 20, confirming a trending environment
- Current session is not a low-liquidity period (avoid weekends and late-night UTC hours)

### 1.2 Indicator Calculations

Compute the following on every 1-minute candle close:

```
EMA_9     = Exponential Moving Average of close price, period 9
EMA_21    = Exponential Moving Average of close price, period 21
RSI_14    = Relative Strength Index of close price, period 14
VOL_RATIO = current volume / average volume over last 20 candles
VWAP      = Volume Weighted Average Price, reset each session
BB_UPPER  = Upper Bollinger Band (period 20, 2 standard deviations)
BB_LOWER  = Lower Bollinger Band (period 20, 2 standard deviations)
BB_WIDTH  = (BB_UPPER - BB_LOWER) / middle band
ADX_14    = Average Directional Index, period 14
```

---

## Part 2 — Signal Scoring

### 2.1 How Scoring Works

Each candle receives a LONG score and a SHORT score. A trade is only entered when a score reaches 3 or above. Higher scores indicate stronger conviction.

**Never enter a trade with a score below 3.**

### 2.2 Long Signal Scoring

```
long_score = 0

if EMA_9 > EMA_21:                      long_score += 1   # trend is bullish
if 50 < RSI_14 < 68:                    long_score += 1   # momentum building, not overbought
if VOL_RATIO >= 1.5:                    long_score += 1   # volume confirms move
if close > VWAP:                        long_score += 1   # price above fair value
```

### 2.3 Short Signal Scoring

```
short_score = 0

if EMA_9 < EMA_21:                      short_score += 1  # trend is bearish
if 32 < RSI_14 < 50:                    short_score += 1  # momentum fading, not oversold
if VOL_RATIO >= 1.5:                    short_score += 1  # volume confirms move
if close < VWAP:                        short_score += 1  # price below fair value
```

### 2.4 Signal Invalidation Rules

Do not enter even if score is 3 or above when any of the following is true:

- RSI is above 75 — overbought, high reversal risk
- RSI is below 25 — oversold, high reversal risk
- Price is outside Bollinger Bands without a preceding squeeze — chasing a breakout
- EMA_9 and EMA_21 are within 0.05% of each other — no clear trend
- A position is already open — one trade at a time only

---

## Part 3 — Entry Logic

### 3.1 Long Entry

Conditions that must all be true on the **previous candle's close** before placing the order:

1. `long_score >= 3`
2. `EMA_9 > EMA_21`
3. `RSI_14` is between 50 and 68
4. `VOL_RATIO >= 1.5`
5. No open position exists
6. All pre-trade environment checks passed

**Action:** Place a market BUY order at the open of the next candle.

### 3.2 Short Entry

Conditions that must all be true on the **previous candle's close** before placing the order:

1. `short_score >= 3`
2. `EMA_9 < EMA_21`
3. `RSI_14` is between 32 and 50
4. `VOL_RATIO >= 1.5`
5. No open position exists
6. All pre-trade environment checks passed

**Action:** Place a market SELL order at the open of the next candle.

### 3.3 Order Placement

Immediately after the entry order fills, calculate the following exit levels. The current bot monitors these levels and closes at market when one is reached:

```
For a LONG trade:
  TP limit order = entry_price + 100 points
  SL stop order  = entry_price - 100 points

For a SHORT trade:
  TP limit order = entry_price - 100 points
  SL stop order  = entry_price + 100 points
```

**Never enter a trade without both TP and SL levels calculated and logged.**

---

## Part 4 — In-Trade Management

Monitor the open position on every 1-minute candle close and apply the following rules in order.

### 4.1 Break-Even Stop

```
if current_pnl_percent >= 1.0%:
    move SL order to entry_price
    # This ensures the trade cannot become a loss once momentum confirms
```

### 4.2 Trailing Stop Activation

```
if current_pnl_percent >= 2.0%:
    cancel fixed SL order
    activate trailing stop with trail = 0.75% leveraged below running high (long)
                                or     0.75% leveraged above running low  (short)
    # This protects a small scalp while allowing the trade to run
```

### 4.3 Counter-Signal Exit

```
if in_long_position:
    if short_score >= 3:
        close position at market immediately

if in_short_position:
    if long_score >= 3:
        close position at market immediately
```

### 4.4 Time-Based Exit

```
if candles_since_entry >= 10:
    close position at market immediately
    # Stale scalping trades indicate the move did not materialise
    # Holding longer ties up capital and increases risk
```

---

## Part 5 — Exit Logic Summary

The bot exits an open trade when the **first** of these conditions is met:

| Priority | Condition | Action |
|---|---|---|
| 1 | TP limit order fills | Trade closed after a 100-point favorable move |
| 2 | SL stop order fills | Trade closed after a 100-point adverse move |
| 3 | Trailing stop triggers | Trade closed at locked-in profit |
| 4 | Counter-signal score ≥ 3 | Close at market |
| 5 | Trade age ≥ 10 candles | Close at market |

---

## Part 6 — Risk Management Rules

### 6.1 Daily Circuit Breaker

```
if cumulative_daily_pnl <= -6.0%:
    halt all trading
    set status = HALTED
    resume only at start of next session
```

With 30x leverage, a 100-point stop produces leveraged PnL of roughly `100 / entry_price * 30`. The circuit breaker prevents catastrophic drawdown from bad market conditions or signal failures.

### 6.2 Consecutive Loss Pause

```
if consecutive_losses >= 3:
    pause trading for 10 minutes
    reset consecutive_loss_counter only after a winning trade
    log reason: "3-loss streak pause — waiting for market conditions to reset"
```

### 6.3 One Trade at a Time

The bot must never open a second position while one is already open. There is no exception to this rule. Pyramiding and averaging down are prohibited.

### 6.4 Position Size

Position size is fixed at $100 per trade during the training and initial live phase. Do not adjust position size based on recent wins or losses. Consistent sizing is required for accurate performance measurement.

### 6.5 Maximum Daily Trades

Limit to 20 trades per session. If 20 trades complete before session end, halt and review performance before continuing. High trade count with low win rate indicates signal problems, not opportunity.

---

## Part 7 — Performance Tracking

The bot must log every trade with the following fields:

```
trade_id          : unique identifier
entry_time        : UTC timestamp
direction         : LONG or SHORT
entry_price       : actual fill price
exit_time         : UTC timestamp
exit_price        : actual fill price
exit_reason       : TP | SL | TRAILING | COUNTER_SIGNAL | TIME_BASED
pnl_percent       : leveraged PnL in percent
long_score        : signal score at entry
short_score       : signal score at entry
ema9_at_entry     : value
ema21_at_entry    : value
rsi_at_entry      : value
vol_ratio_entry   : value
session_daily_pnl : running daily PnL at time of entry
```

### 7.1 Performance Metrics to Monitor

Compute these after every 50 trades:

- Win rate — must be above 37.5% to remain in operation; target 55%+
- Average win — should trend toward +5%
- Average loss — should trend toward −3%
- Profit factor — total gains divided by total losses; target above 1.5
- Max consecutive losses — alert if this exceeds 4
- Average trade duration — alert if consistently above 10 minutes (signals may be too slow)

---

## Part 8 — Expected Value Model

The strategy's theoretical edge:

```
EV per trade = (win_rate × TP) − (loss_rate × SL)

At 37.5% win rate (breakeven):
  EV = (0.375 × 5%) − (0.625 × 3%) = 1.875% − 1.875% = 0.0%

At 50% win rate:
  EV = (0.50 × 5%) − (0.50 × 3%) = 2.5% − 1.5% = +1.0% per trade

At 55% win rate:
  EV = (0.55 × 5%) − (0.45 × 3%) = 2.75% − 1.35% = +1.4% per trade
```

A 55% win rate on 10 trades per day at $100 base = approximately +$14 expected daily profit before fees.

---

## Part 9 — Complete Decision Pseudocode

```
ON EVERY 1-MINUTE CANDLE CLOSE:

  # --- environment check ---
  if spread > 0.05%:              skip candle
  if near_news(minutes=5):        skip candle
  if daily_pnl <= -6.0%:          halt_session()
  if consecutive_losses >= 3:     pause(minutes=10)
  if daily_trade_count >= 20:     halt_session()

  # --- compute indicators ---
  ema9       = EMA(close, 9)
  ema21      = EMA(close, 21)
  rsi        = RSI(close, 14)
  vol_ratio  = volume / AVG(volume, 20)
  vwap       = VWAP(session)
  adx        = ADX(14)

  if adx < 20: skip candle

  # --- score signals ---
  long_score  = (ema9 > ema21) + (50 < rsi < 68) + (vol_ratio >= 1.5) + (close > vwap)
  short_score = (ema9 < ema21) + (32 < rsi < 50) + (vol_ratio >= 1.5) + (close < vwap)

  # --- entry ---
  if long_score >= 3 and not in_position:
    entry = open_long($100, leverage=30)
    place_tp(entry + 100)
    place_sl(entry - 100)
    start_timer()

  if short_score >= 3 and not in_position:
    entry = open_short($100, leverage=30)
    place_tp(entry - 100)
    place_sl(entry + 100)
    start_timer()

  # --- in-trade management ---
  if in_position:
    pnl = get_current_pnl_percent()

    if pnl >= 1.0:
      move_sl_to_breakeven()

    if pnl >= 2.0:
      activate_trailing_stop(trail_percent=0.75)

    if (in_long and short_score >= 3) or (in_short and long_score >= 3):
      close_at_market(reason="COUNTER_SIGNAL")

    if candles_open >= 10:
      close_at_market(reason="TIME_BASED")

  # --- log trade on close ---
  if trade_just_closed:
    log_trade(all fields listed in Part 7)
    update_daily_pnl()
    update_consecutive_loss_counter()
```

---

## Part 10 — Rules Summary (Quick Reference)

```
DO:
  Enter only when score >= 3
  Always calculate TP and SL before considering entry confirmed
  Move SL to breakeven at +1%
  Activate trailing stop at +2%
  Exit after 10 candles if neither TP nor SL has triggered
  Halt after -6% daily loss
  Pause 10 minutes after 3 consecutive losses
  Log every trade with full details
  Review performance every 50 trades

DO NOT:
  Trade with a score below 3
  Enter without both TP and SL levels calculated
  Open more than one trade at a time
  Remove the SL order once placed
  Trade during news events or low liquidity
  Average down on a losing position
  Increase position size after a winning streak
  Continue trading after the daily circuit breaker triggers
```

---

*Strategy version: 1.1 - 30x 100-point scalping - 1-min timeframe*
*Parameters: $100 base - 100-point TP - 100-point SL - min 3-signal confluence*
