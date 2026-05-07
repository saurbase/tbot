import logging
import os
from typing import Any, Optional

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # Allows the bot to run without DB extras when DATABASE_URL is unset.
    psycopg = None
    dict_row = None
    Jsonb = None


log = logging.getLogger(__name__)


TRADE_FIELDS = [
    "trade_id",
    "entry_time",
    "exit_time",
    "symbol",
    "direction",
    "strategy",
    "entry_price",
    "exit_price",
    "margin_used",
    "leverage",
    "notional_size",
    "stop_loss_price",
    "take_profit_price",
    "exit_reason",
    "pnl_percent",
    "pnl_usd",
    "long_score",
    "short_score",
    "ema9_at_entry",
    "ema21_at_entry",
    "ema50_5m_at_entry",
    "rsi_at_entry",
    "adx_at_entry",
    "atr_at_entry",
    "vol_ratio_at_entry",
    "spread_at_entry",
    "slippage_estimate",
    "daily_pnl_after_trade",
    "consecutive_losses",
    "candles_held",
    "target_profit_usdt",
    "unrealized_profit_target_usdt",
    "daily_profit_target_usdt",
    "movement_points",
]


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def database_enabled() -> bool:
    return bool(database_url())


def connect(row_factory=None):
    if not database_enabled():
        return None
    if psycopg is None:
        log.warning("DATABASE_URL is set but psycopg is not installed; database writes disabled")
        return None
    kwargs: dict[str, Any] = {"autocommit": True}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    return psycopg.connect(database_url(), **kwargs)


def init_db() -> None:
    conn = connect()
    if conn is None:
        return

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id BIGSERIAL PRIMARY KEY,
                    trade_id TEXT UNIQUE NOT NULL,
                    entry_time TIMESTAMPTZ,
                    exit_time TIMESTAMPTZ,
                    symbol TEXT,
                    direction TEXT,
                    strategy TEXT,
                    entry_price NUMERIC,
                    exit_price NUMERIC,
                    margin_used NUMERIC,
                    leverage INTEGER,
                    notional_size NUMERIC,
                    stop_loss_price NUMERIC,
                    take_profit_price NUMERIC,
                    exit_reason TEXT,
                    pnl_percent NUMERIC,
                    pnl_usd NUMERIC,
                    long_score INTEGER,
                    short_score INTEGER,
                    ema9_at_entry NUMERIC,
                    ema21_at_entry NUMERIC,
                    ema50_5m_at_entry NUMERIC,
                    rsi_at_entry NUMERIC,
                    adx_at_entry NUMERIC,
                    atr_at_entry NUMERIC,
                    vol_ratio_at_entry NUMERIC,
                    spread_at_entry NUMERIC,
                    slippage_estimate NUMERIC,
                    daily_pnl_after_trade NUMERIC,
                    consecutive_losses INTEGER,
                    candles_held INTEGER,
                    target_profit_usdt NUMERIC,
                    unrealized_profit_target_usdt NUMERIC,
                    daily_profit_target_usdt NUMERIC,
                    movement_points NUMERIC,
                    raw JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS account_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    source TEXT NOT NULL DEFAULT 'binance_futures',
                    total_wallet_balance NUMERIC,
                    total_margin_balance NUMERIC,
                    available_balance NUMERIC,
                    total_unrealized_profit NUMERIC,
                    total_initial_margin NUMERIC,
                    total_maint_margin NUMERIC,
                    max_withdraw_amount NUMERIC,
                    assets JSONB,
                    positions JSONB,
                    raw JSONB
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_logs (
                    id BIGSERIAL PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    level TEXT NOT NULL,
                    logger TEXT,
                    message TEXT NOT NULL,
                    pathname TEXT,
                    line_no INTEGER
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades (exit_time DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol)")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_account_snapshots_time "
                "ON account_snapshots (captured_at DESC)"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_bot_logs_time ON bot_logs (created_at DESC)")
            cur.execute(
                "ALTER TABLE trades "
                "ADD COLUMN IF NOT EXISTS unrealized_profit_target_usdt NUMERIC"
            )


def _float_or_none(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def save_trade(row: dict[str, Any]) -> None:
    conn = connect()
    if conn is None:
        return

    payload = {field: row.get(field) for field in TRADE_FIELDS}
    payload["raw"] = Jsonb(row)
    columns = list(payload)
    placeholders = ", ".join(f"%({column})s" for column in columns)
    update_columns = [column for column in columns if column != "trade_id"]
    assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in update_columns)

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO trades ({", ".join(columns)})
                VALUES ({placeholders})
                ON CONFLICT (trade_id) DO UPDATE SET {assignments}
                """,
                payload,
            )


def save_account_snapshot(account: dict[str, Any]) -> None:
    conn = connect()
    if conn is None:
        return

    payload = {
        "total_wallet_balance": _float_or_none(account.get("totalWalletBalance")),
        "total_margin_balance": _float_or_none(account.get("totalMarginBalance")),
        "available_balance": _float_or_none(account.get("availableBalance")),
        "total_unrealized_profit": _float_or_none(account.get("totalUnrealizedProfit")),
        "total_initial_margin": _float_or_none(account.get("totalInitialMargin")),
        "total_maint_margin": _float_or_none(account.get("totalMaintMargin")),
        "max_withdraw_amount": _float_or_none(account.get("maxWithdrawAmount")),
        "assets": Jsonb(account.get("assets", [])),
        "positions": Jsonb(account.get("positions", [])),
        "raw": Jsonb(account),
    }

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO account_snapshots (
                    total_wallet_balance,
                    total_margin_balance,
                    available_balance,
                    total_unrealized_profit,
                    total_initial_margin,
                    total_maint_margin,
                    max_withdraw_amount,
                    assets,
                    positions,
                    raw
                )
                VALUES (
                    %(total_wallet_balance)s,
                    %(total_margin_balance)s,
                    %(available_balance)s,
                    %(total_unrealized_profit)s,
                    %(total_initial_margin)s,
                    %(total_maint_margin)s,
                    %(max_withdraw_amount)s,
                    %(assets)s,
                    %(positions)s,
                    %(raw)s
                )
                """,
                payload,
            )


def save_bot_log(record: dict[str, Any]) -> None:
    conn = connect()
    if conn is None:
        return

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bot_logs (
                    level,
                    logger,
                    message,
                    pathname,
                    line_no
                )
                VALUES (
                    %(level)s,
                    %(logger)s,
                    %(message)s,
                    %(pathname)s,
                    %(line_no)s
                )
                """,
                record,
            )


def fetch_dashboard_data(limit: int = 100) -> dict[str, Any]:
    conn = connect(row_factory=dict_row)
    if conn is None:
        return {
            "database_enabled": False,
            "account": None,
            "summary": {},
            "trades": [],
            "positions": [],
            "logs": [],
        }

    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM account_snapshots
                ORDER BY captured_at DESC
                LIMIT 1
                """
            )
            account = cur.fetchone()

            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_trades,
                    COALESCE(SUM(pnl_usd), 0) AS total_pnl,
                    COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END), 0) AS losses,
                    COALESCE(SUM(CASE WHEN exit_time::date = CURRENT_DATE THEN pnl_usd ELSE 0 END), 0)
                        AS today_pnl,
                    COALESCE(SUM(CASE WHEN exit_time::date = CURRENT_DATE THEN 1 ELSE 0 END), 0)
                        AS today_trades
                FROM trades
                """
            )
            summary = cur.fetchone() or {}

            cur.execute(
                """
                SELECT *
                FROM trades
                ORDER BY COALESCE(exit_time, created_at) DESC
                LIMIT %(limit)s
                """,
                {"limit": limit},
            )
            trades = cur.fetchall()

            cur.execute(
                """
                SELECT *
                FROM bot_logs
                ORDER BY created_at DESC
                LIMIT 200
                """
            )
            logs = cur.fetchall()

    positions = []
    if account and account.get("positions"):
        positions = [
            position
            for position in account["positions"]
            if abs(_float_or_none(position.get("positionAmt")) or 0) > 0
        ]

    return {
        "database_enabled": True,
        "account": account,
        "summary": summary,
        "trades": trades,
        "positions": positions,
        "logs": logs,
    }
