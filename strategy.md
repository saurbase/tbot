Below is a **safer training blueprint** for your scalping bot. I would **not** train it to expect 5% profit every trade; with **30x leverage**, fees, spread, slippage, funding, and liquidation risk can quickly destroy the account. Futures/perpetual trading has substantial loss risk, including total loss, and funding/fees matter on every trade. ([eCFR][1])

## Bot Strategy: 30x 100-Point Scalping Long/Short

## Strategy Selection

Set `STRATEGY=sta1` or `STRATEGY=sta2` in `.env`.

```text
sta1:
  Current 30x strategy.
  Uses fixed 100-point TP and 100-point SL.

sta2:
  30x scalping strategy for markets with movement between 50 and 200 points.
  Uses recent high-low range by default for the movement filter.
  Closes the active trade when unrealized PnL reaches $10.00 by default.
  Uses a $2.50 gross stop per trade.
  Stops opening new trades after $10.00 gross realized PnL for the UTC day.
```

### 1. Core rules

**Capital per trade:** $100 margin
**Leverage:** 30x
**Position size:** $3,000 notional
**Stop loss:** 3% on margin risk is too vague. Use price-based stop instead.

With 30x leverage:

```text
1% market move = ~30% gain/loss on margin before fees
100 BTCUSDT points at a 100,000 entry = ~3% gain/loss on margin before fees
```

So a true **3% price stop** is very dangerous. Better:

```text
Max loss per trade: 1% to 2% of total account
Stop distance: 100 points
Take profit: 100 points
Move stop to breakeven once leveraged PnL reaches +1%
Trail after leveraged PnL reaches +2%
```

## 2. Entry conditions

### Long setup

Enter **LONG** only when:

```text
Price > EMA 200
EMA 9 > EMA 21
RSI between 50 and 70
MACD histogram positive
Volume > 20-period average volume
Spread < max allowed spread
Funding rate acceptable
No major news event
```

### Short setup

Enter **SHORT** only when:

```text
Price < EMA 200
EMA 9 < EMA 21
RSI between 30 and 50
MACD histogram negative
Volume > 20-period average volume
Spread < max allowed spread
Funding rate acceptable
No major news event
```

## 3. Exit logic

Use partial profit-taking:

```text
Take Profit: close at a 100-point favorable move
Move stop loss to breakeven after +1% leveraged PnL
Trail after +2% leveraged PnL
Hard exit if opposite signal appears
Hard exit after 10 candles if no momentum
```

Do **not** keep every trade running forever. For scalping, stale trades usually become bad trades.

## 4. Risk controls

Add these rules before live trading:

```text
Max daily loss: 3% of account
Max consecutive losses: 3
Max open trades: 1
Cooldown after loss: 10–15 minutes
Do not trade during extreme volatility
Do not trade if spread/slippage is high
Avoid holding through funding time unless strategy accounts for it
```

Funding rates are important in perpetual futures and can affect performance, especially when holding positions across funding intervals. ([Binance][2])

## 5. Pseudocode

```python
def scalping_strategy(data, account_balance):
    price = data.close[-1]

    ema9 = EMA(data.close, 9)
    ema21 = EMA(data.close, 21)
    ema200 = EMA(data.close, 200)
    rsi = RSI(data.close, 14)
    macd_hist = MACD(data.close).histogram
    atr = ATR(data.high, data.low, data.close, 14)
    volume_ok = data.volume[-1] > SMA(data.volume, 20)[-1]

    max_risk_per_trade = account_balance * 0.01
    margin_per_trade = 100
    leverage = 30
    position_notional = margin_per_trade * leverage
    scalp_points = 100

    if daily_loss_exceeded():
        return "NO_TRADE"

    if consecutive_losses() >= 3:
        return "COOLDOWN"

    spread_ok = current_spread() < 0.05
    funding_ok = abs(current_funding_rate()) < 0.01

    long_signal = (
        price > ema200[-1]
        and ema9[-1] > ema21[-1]
        and 50 < rsi[-1] < 70
        and macd_hist[-1] > 0
        and volume_ok
        and spread_ok
        and funding_ok
    )

    short_signal = (
        price < ema200[-1]
        and ema9[-1] < ema21[-1]
        and 30 < rsi[-1] < 50
        and macd_hist[-1] < 0
        and volume_ok
        and spread_ok
        and funding_ok
    )

    stop_distance = scalp_points
    take_profit = scalp_points

    if long_signal:
        return {
            "side": "LONG",
            "margin": margin_per_trade,
            "leverage": leverage,
            "stop_loss": price - stop_distance,
            "take_profit": price + take_profit,
            "trailing_stop": "after +2% leveraged PnL"
        }

    if short_signal:
        return {
            "side": "SHORT",
            "margin": margin_per_trade,
            "leverage": leverage,
            "stop_loss": price + stop_distance,
            "take_profit": price - take_profit,
            "trailing_stop": "after +2% leveraged PnL"
        }

    return "NO_TRADE"
```

## 6. Training objective

Train your bot to optimize for:

```text
Profit factor > 1.3
Win rate > 50%
Max drawdown < 10%
Average win > average loss
No single trade loses more than 1% of account
Strategy survives fees, slippage, and funding
```

## Best version of your idea

```text
Use 30x leverage only when trend, momentum, volume, and spread all agree.
Target 100-point price moves, not 5% every trade.
Take profit quickly.
Move stop to breakeven.
Trail winners.
Stop trading after daily loss limit.
Backtest with fees, slippage, and funding included.
```

The biggest improvement: **replace “5% profit every trade” with a probability-based system that protects capital first.**

[1]: https://www.ecfr.gov/current/title-17/chapter-I/part-1/subject-group-ECFR13f523b74fee655/section-1.55?utm_source=chatgpt.com "17 CFR 1.55 -- Public disclosures by futures commission merchants."
[2]: https://www.binance.com/en/support/faq/detail/360033525031?utm_source=chatgpt.com "Introduction to Binance Futures Funding Rates"
