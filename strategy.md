Below is a **safer training blueprint** for your scalping bot. I would **not** train it to expect 5% profit every trade; with **15x leverage**, fees, spread, slippage, funding, and liquidation risk can quickly destroy the account. Futures/perpetual trading has substantial loss risk, including total loss, and funding/fees matter on every trade. ([eCFR][1])

## Bot Strategy: 15x Scalping Long/Short

### 1. Core rules

**Capital per trade:** $100 margin
**Leverage:** 15x
**Position size:** $1,500 notional
**Stop loss:** 3% on margin risk is too vague. Use price-based stop instead.

With 15x leverage:

```text
1% market move = ~15% gain/loss on margin before fees
3% market move against you = ~45% loss on margin before fees
```

So a true **3% price stop** is very dangerous. Better:

```text
Max loss per trade: 1% to 2% of total account
Stop distance: 0.20% to 0.50% price move
Take profit 1: 0.30% to 0.60%
Take profit 2: trailing stop
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
Take Profit 1: close 50% at +0.35% price move
Move stop loss to breakeven
Take Profit 2: trail remaining 50% using ATR or EMA 9
Hard exit if opposite signal appears
Hard exit after 10–20 candles if no momentum
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
    leverage = 15
    position_notional = margin_per_trade * leverage

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

    stop_distance = max(atr[-1] * 0.8, price * 0.0025)
    take_profit_1 = price * 0.0035

    if long_signal:
        return {
            "side": "LONG",
            "margin": margin_per_trade,
            "leverage": leverage,
            "stop_loss": price - stop_distance,
            "take_profit_1": price + take_profit_1,
            "trailing_stop": "EMA9 or ATR"
        }

    if short_signal:
        return {
            "side": "SHORT",
            "margin": margin_per_trade,
            "leverage": leverage,
            "stop_loss": price + stop_distance,
            "take_profit_1": price - take_profit_1,
            "trailing_stop": "EMA9 or ATR"
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
Use 15x leverage only when trend, momentum, volume, and spread all agree.
Target small price moves, not 5% every trade.
Take partial profit quickly.
Move stop to breakeven.
Trail winners.
Stop trading after daily loss limit.
Backtest with fees, slippage, and funding included.
```

The biggest improvement: **replace “5% profit every trade” with a probability-based system that protects capital first.**

[1]: https://www.ecfr.gov/current/title-17/chapter-I/part-1/subject-group-ECFR13f523b74fee655/section-1.55?utm_source=chatgpt.com "17 CFR 1.55 -- Public disclosures by futures commission merchants."
[2]: https://www.binance.com/en/support/faq/detail/360033525031?utm_source=chatgpt.com "Introduction to Binance Futures Funding Rates"
