# Trading Bot

## Services

- `trading-bot`: runs the Binance scalping bot.
- `postgres`: stores closed trades, account snapshots, and bot logs.
- `dashboard`: read-only web dashboard for trades, balances, account assets, open positions, and realtime bot logs.

## Run

```bash
docker compose up --build -d
```

Open the dashboard:

```text
http://localhost:8000
```

Set the host port with `DASHBOARD_PORT` in `.env`.

## Strategy Selection

Use `STRATEGY=sta1` for the current 30x fixed 100-point strategy.

Use `STRATEGY=sta2` for the 30x movement-filter strategy:

```env
STRATEGY=sta2
STA2_UNREALIZED_PROFIT_TARGET_USDT=10
STA2_STOP_LOSS_USDT=2.5
STA2_DAILY_PROFIT_TARGET_USDT=10
STA2_MIN_MOVE_POINTS=50
STA2_MAX_MOVE_POINTS=200
STA2_MOVEMENT_SOURCE=recent_range
STA2_MOVEMENT_LOOKBACK=5
STA2_MIN_SIGNAL_SCORE=2
```

`sta2` uses gross bot-calculated P&L. Fees, slippage, and funding can make actual exchange P&L lower.

Use `STRATEGY=sta3` for the delayed EMA crossover strategy from `pine.md`:

```env
STRATEGY=sta3
STA3_LEVERAGE=30
STA3_TP_PCT=1.2
STA3_SL_PCT=0.6
STA3_CONFIRMATION_CANDLES=2
STA3_MIN_SIGNAL_SCORE=1
STA3_REQUIRE_VOLUME_SPIKE=false
STA3_MIN_VOLUME_RATIO=1.0
STA3_RSI_LONG_MIN=
STA3_RSI_LONG_MAX=
STA3_RSI_SHORT_MIN=
STA3_RSI_SHORT_MAX=
STA3_USE_HIGHER_TF_TREND=false
```

`sta3` waits for an EMA9/EMA21 crossover, delays entry by the configured number of closed candles, confirms the EMA alignment again, and cancels the setup if the opposite crossover appears first.

## Database

Postgres is configured by these `.env` values:

```env
POSTGRES_DB=tradingbot
POSTGRES_USER=tradingbot
POSTGRES_PASSWORD=tradingbot_change_me
DATABASE_URL=postgresql://tradingbot:tradingbot_change_me@postgres:5432/tradingbot
```

Data is stored in the Docker volume `trading_bot_pgdata`.
