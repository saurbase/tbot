import os
from dataclasses import dataclass
from typing import Mapping, Optional


def _env(env: Mapping[str, str], key: str, default: Optional[str] = None) -> Optional[str]:
    return env.get(key) or env.get(key.lower()) or default


def _env_float(env: Mapping[str, str], key: str, default: float) -> float:
    value = _env(env, key)
    return float(value) if value not in (None, "") else default


def _env_optional_float(env: Mapping[str, str], key: str) -> Optional[float]:
    value = _env(env, key)
    return float(value) if value not in (None, "") else None


def _env_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = _env(env, key)
    return int(value) if value not in (None, "") else default


def _env_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = _env(env, key)
    if value in (None, ""):
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class StrategySettings:
    name: str
    leverage: int
    tp_points: Optional[float] = None
    sl_points: Optional[float] = None
    tp_pct: Optional[float] = None
    sl_pct: Optional[float] = None
    target_profit_usdt: Optional[float] = None
    unrealized_profit_target_usdt: Optional[float] = None
    stop_loss_usdt: Optional[float] = None
    daily_profit_target_usdt: Optional[float] = None
    min_movement_points: Optional[float] = None
    max_movement_points: Optional[float] = None
    movement_source: str = "atr14"
    movement_lookback: int = 5
    breakeven_pnl_pct: float = 1.0
    trail_start_pnl_pct: float = 2.0
    trail_lev_pct: float = 0.75
    early_exit_loss_pct: float = -1.5
    max_candles_held: int = 10
    use_breakeven: bool = True
    use_trailing: bool = True
    use_early_exit: bool = True
    use_counter_signal_exit: bool = True
    min_signal_score: Optional[int] = None
    confirmation_candles: int = 0
    require_volume_spike: bool = False
    volume_spike_ratio: float = 1.0
    rsi_long_min: Optional[float] = None
    rsi_long_max: Optional[float] = None
    rsi_short_min: Optional[float] = None
    rsi_short_max: Optional[float] = None
    use_higher_tf_trend_filter: bool = False

    def validate(self) -> None:
        if self.leverage <= 0:
            raise ValueError("strategy leverage must be greater than zero")
        if (
            self.tp_points is None
            and self.tp_pct is None
            and self.target_profit_usdt is None
            and self.unrealized_profit_target_usdt is None
        ):
            raise ValueError(
                "strategy must define tp_points, tp_pct, target_profit_usdt, "
                "or unrealized_profit_target_usdt"
            )
        if self.sl_points is None and self.sl_pct is None and self.stop_loss_usdt is None:
            raise ValueError("strategy must define sl_points, sl_pct, or stop_loss_usdt")
        if self.tp_points is not None and self.tp_points <= 0:
            raise ValueError("strategy tp_points must be greater than zero")
        if self.sl_points is not None and self.sl_points <= 0:
            raise ValueError("strategy sl_points must be greater than zero")
        if self.tp_pct is not None and self.tp_pct <= 0:
            raise ValueError("strategy tp_pct must be greater than zero")
        if self.sl_pct is not None and self.sl_pct <= 0:
            raise ValueError("strategy sl_pct must be greater than zero")
        if self.target_profit_usdt is not None and self.target_profit_usdt <= 0:
            raise ValueError("strategy target_profit_usdt must be greater than zero")
        if self.unrealized_profit_target_usdt is not None and self.unrealized_profit_target_usdt <= 0:
            raise ValueError("strategy unrealized_profit_target_usdt must be greater than zero")
        if self.stop_loss_usdt is not None and self.stop_loss_usdt <= 0:
            raise ValueError("strategy stop_loss_usdt must be greater than zero")
        if self.daily_profit_target_usdt is not None and self.daily_profit_target_usdt <= 0:
            raise ValueError("strategy daily_profit_target_usdt must be greater than zero")
        if self.min_movement_points is not None and self.min_movement_points < 0:
            raise ValueError("strategy min_movement_points cannot be negative")
        if self.max_movement_points is not None and self.max_movement_points <= 0:
            raise ValueError("strategy max_movement_points must be greater than zero")
        if (
            self.min_movement_points is not None
            and self.max_movement_points is not None
            and self.min_movement_points > self.max_movement_points
        ):
            raise ValueError("strategy min_movement_points cannot exceed max_movement_points")
        if self.movement_source not in {"atr14", "last_candle", "recent_range"}:
            raise ValueError("strategy movement_source must be atr14, last_candle, or recent_range")
        if self.movement_lookback <= 0:
            raise ValueError("strategy movement_lookback must be greater than zero")
        if self.max_candles_held <= 0:
            raise ValueError("strategy max_candles_held must be greater than zero")
        if self.min_signal_score is not None and self.min_signal_score < 1:
            raise ValueError("strategy min_signal_score must be at least 1")
        if self.confirmation_candles < 0:
            raise ValueError("strategy confirmation_candles cannot be negative")
        if self.volume_spike_ratio <= 0:
            raise ValueError("strategy volume_spike_ratio must be greater than zero")

    def exit_distances(self, entry_price: float, quantity: float) -> tuple[float, float]:
        if entry_price <= 0:
            raise ValueError("entry_price must be greater than zero")
        if quantity <= 0:
            raise ValueError("quantity must be greater than zero")

        tp_distance = self.tp_points
        if self.tp_pct is not None:
            tp_distance = entry_price * (self.tp_pct / 100)
        if self.target_profit_usdt is not None:
            tp_distance = self.target_profit_usdt / quantity
        elif self.unrealized_profit_target_usdt is not None:
            tp_distance = self.unrealized_profit_target_usdt / quantity

        stop_distance = self.sl_points
        if self.sl_pct is not None:
            stop_distance = entry_price * (self.sl_pct / 100)
        if self.stop_loss_usdt is not None:
            stop_distance = self.stop_loss_usdt / quantity

        if tp_distance is None or stop_distance is None:
            raise ValueError("strategy exit distances are incomplete")
        return tp_distance, stop_distance

    def movement_value(self, atr14: float, last_candle_range: float, recent_range: float) -> float:
        if self.movement_source == "last_candle":
            return last_candle_range
        if self.movement_source == "recent_range":
            return recent_range
        return atr14

    def movement_rejection(self, movement_points: float) -> Optional[str]:
        if self.min_movement_points is not None and movement_points < self.min_movement_points:
            return (
                f"movement too small "
                f"({movement_points:.2f} < {self.min_movement_points:.2f} points)"
            )
        if self.max_movement_points is not None and movement_points > self.max_movement_points:
            return (
                f"movement too large "
                f"({movement_points:.2f} > {self.max_movement_points:.2f} points)"
            )
        return None

    def startup_summary(self) -> str:
        parts = [f"strategy={self.name}", f"{self.leverage}x leverage"]
        if self.target_profit_usdt is not None:
            parts.append(f"target=${self.target_profit_usdt:g}")
        elif self.unrealized_profit_target_usdt is not None:
            parts.append(f"unrealized_target=${self.unrealized_profit_target_usdt:g}")
        elif self.tp_pct is not None:
            parts.append(f"TP={self.tp_pct:g}%")
        elif self.tp_points is not None:
            parts.append(f"TP={self.tp_points:g} pts")
        if self.stop_loss_usdt is not None:
            parts.append(f"stop=${self.stop_loss_usdt:g}")
        elif self.sl_pct is not None:
            parts.append(f"SL={self.sl_pct:g}%")
        elif self.sl_points is not None:
            parts.append(f"SL={self.sl_points:g} pts")
        if self.daily_profit_target_usdt is not None:
            parts.append(f"daily_target=${self.daily_profit_target_usdt:g}")
        if self.min_movement_points is not None or self.max_movement_points is not None:
            min_move = "-" if self.min_movement_points is None else f"{self.min_movement_points:g}"
            max_move = "-" if self.max_movement_points is None else f"{self.max_movement_points:g}"
            parts.append(f"movement={self.movement_source}:{min_move}-{max_move} pts")
        if self.min_signal_score is not None:
            parts.append(f"min_score={self.min_signal_score}")
        if self.confirmation_candles:
            parts.append(f"confirm={self.confirmation_candles} candles")
        if self.require_volume_spike:
            parts.append(f"vol>={self.volume_spike_ratio:g}x")
        return " | ".join(parts)


def load_strategy(env: Mapping[str, str] = os.environ) -> StrategySettings:
    raw_name = (_env(env, "STRATEGY", "sta1") or "sta1").strip().lower()
    name = "sta1" if raw_name in {"claude", "current", "sta1"} else raw_name

    if name == "sta1":
        settings = StrategySettings(
            name="sta1",
            leverage=_env_int(env, "STA1_LEVERAGE", _env_int(env, "LEVERAGE", 30)),
            tp_points=_env_float(env, "SCALP_TP_POINTS", 100.0),
            sl_points=_env_float(env, "SCALP_SL_POINTS", 100.0),
            daily_profit_target_usdt=_env_optional_float(env, "STA1_DAILY_PROFIT_TARGET_USDT"),
            breakeven_pnl_pct=_env_float(env, "BREAKEVEN_PNL_PCT", 1.0),
            trail_start_pnl_pct=_env_float(env, "TRAIL_START_PNL_PCT", 2.0),
            trail_lev_pct=_env_float(env, "TRAIL_LEV_PCT", 0.75),
            early_exit_loss_pct=_env_float(env, "EARLY_EXIT_LOSS_PCT", -1.5),
            max_candles_held=_env_int(env, "MAX_CANDLES_HELD", 10),
            use_breakeven=_env_bool(env, "STA1_USE_BREAKEVEN", True),
            use_trailing=_env_bool(env, "STA1_USE_TRAILING", True),
            use_early_exit=_env_bool(env, "STA1_USE_EARLY_EXIT", True),
            use_counter_signal_exit=_env_bool(env, "STA1_USE_COUNTER_SIGNAL_EXIT", True),
            min_signal_score=_env_int(env, "STA1_MIN_SIGNAL_SCORE", _env_int(env, "MIN_SIGNAL_SCORE", 3)),
        )
    elif name == "sta2":
        settings = StrategySettings(
            name="sta2",
            leverage=_env_int(env, "STA2_LEVERAGE", 30),
            unrealized_profit_target_usdt=_env_float(env, "STA2_UNREALIZED_PROFIT_TARGET_USDT", 10.0),
            stop_loss_usdt=_env_float(env, "STA2_STOP_LOSS_USDT", 2.5),
            daily_profit_target_usdt=_env_float(env, "STA2_DAILY_PROFIT_TARGET_USDT", 10.0),
            min_movement_points=_env_float(env, "STA2_MIN_MOVE_POINTS", 50.0),
            max_movement_points=_env_float(env, "STA2_MAX_MOVE_POINTS", 200.0),
            movement_source=(_env(env, "STA2_MOVEMENT_SOURCE", "recent_range") or "recent_range").strip().lower(),
            movement_lookback=_env_int(env, "STA2_MOVEMENT_LOOKBACK", 5),
            breakeven_pnl_pct=_env_float(env, "STA2_BREAKEVEN_PNL_PCT", 1.0),
            trail_start_pnl_pct=_env_float(env, "STA2_TRAIL_START_PNL_PCT", 2.0),
            trail_lev_pct=_env_float(env, "STA2_TRAIL_LEV_PCT", 0.75),
            early_exit_loss_pct=_env_float(env, "STA2_EARLY_EXIT_LOSS_PCT", -1.5),
            max_candles_held=_env_int(env, "STA2_MAX_CANDLES_HELD", 5),
            use_breakeven=_env_bool(env, "STA2_USE_BREAKEVEN", False),
            use_trailing=_env_bool(env, "STA2_USE_TRAILING", False),
            use_early_exit=_env_bool(env, "STA2_USE_EARLY_EXIT", True),
            use_counter_signal_exit=_env_bool(env, "STA2_USE_COUNTER_SIGNAL_EXIT", True),
            min_signal_score=_env_int(env, "STA2_MIN_SIGNAL_SCORE", _env_int(env, "MIN_SIGNAL_SCORE", 2)),
        )
    elif name == "sta3":
        settings = StrategySettings(
            name="sta3",
            leverage=_env_int(env, "STA3_LEVERAGE", 30),
            tp_pct=_env_float(env, "STA3_TP_PCT", 1.2),
            sl_pct=_env_float(env, "STA3_SL_PCT", 0.6),
            daily_profit_target_usdt=_env_optional_float(env, "STA3_DAILY_PROFIT_TARGET_USDT"),
            breakeven_pnl_pct=_env_float(env, "STA3_BREAKEVEN_PNL_PCT", 15.0),
            trail_start_pnl_pct=_env_float(env, "STA3_TRAIL_START_PNL_PCT", 15.0),
            trail_lev_pct=_env_float(env, "STA3_TRAIL_LEV_PCT", 6.0),
            early_exit_loss_pct=_env_float(env, "STA3_EARLY_EXIT_LOSS_PCT", -6.0),
            max_candles_held=_env_int(env, "STA3_MAX_CANDLES_HELD", 20),
            use_breakeven=_env_bool(env, "STA3_USE_BREAKEVEN", False),
            use_trailing=_env_bool(env, "STA3_USE_TRAILING", False),
            use_early_exit=_env_bool(env, "STA3_USE_EARLY_EXIT", True),
            use_counter_signal_exit=_env_bool(env, "STA3_USE_COUNTER_SIGNAL_EXIT", True),
            min_signal_score=_env_int(env, "STA3_MIN_SIGNAL_SCORE", 1),
            confirmation_candles=_env_int(env, "STA3_CONFIRMATION_CANDLES", 2),
            require_volume_spike=_env_bool(env, "STA3_REQUIRE_VOLUME_SPIKE", False),
            volume_spike_ratio=_env_float(env, "STA3_MIN_VOLUME_RATIO", 1.0),
            rsi_long_min=_env_optional_float(env, "STA3_RSI_LONG_MIN"),
            rsi_long_max=_env_optional_float(env, "STA3_RSI_LONG_MAX"),
            rsi_short_min=_env_optional_float(env, "STA3_RSI_SHORT_MIN"),
            rsi_short_max=_env_optional_float(env, "STA3_RSI_SHORT_MAX"),
            use_higher_tf_trend_filter=_env_bool(env, "STA3_USE_HIGHER_TF_TREND", False),
        )
    elif name == "sta4":
        settings = StrategySettings(
            name="sta4",
            leverage=_env_int(env, "STA4_LEVERAGE", 20),
            tp_pct=_env_float(env, "STA4_TP_PCT", 0.8),
            sl_pct=_env_float(env, "STA4_SL_PCT", 0.4),
            daily_profit_target_usdt=_env_optional_float(env, "STA4_DAILY_PROFIT_TARGET_USDT"),
            breakeven_pnl_pct=_env_float(env, "STA4_BREAKEVEN_PNL_PCT", 15.0),
            trail_start_pnl_pct=_env_float(env, "STA4_TRAIL_START_PNL_PCT", 15.0),
            trail_lev_pct=_env_float(env, "STA4_TRAIL_LEV_PCT", 6.0),
            early_exit_loss_pct=_env_float(env, "STA4_EARLY_EXIT_LOSS_PCT", -6.0),
            max_candles_held=_env_int(env, "STA4_MAX_CANDLES_HELD", 20),
            use_breakeven=_env_bool(env, "STA4_USE_BREAKEVEN", False),
            use_trailing=_env_bool(env, "STA4_USE_TRAILING", False),
            use_early_exit=_env_bool(env, "STA4_USE_EARLY_EXIT", True),
            use_counter_signal_exit=_env_bool(env, "STA4_USE_COUNTER_SIGNAL_EXIT", True),
            min_signal_score=_env_int(env, "STA4_MIN_SIGNAL_SCORE", 1),
            confirmation_candles=_env_int(env, "STA4_CONFIRMATION_CANDLES", 2),
            require_volume_spike=_env_bool(env, "STA4_REQUIRE_VOLUME_SPIKE", True),
            volume_spike_ratio=_env_float(env, "STA4_MIN_VOLUME_RATIO", 1.5),
            rsi_long_min=_env_optional_float(env, "STA4_RSI_LONG_MIN", 30.0),
            rsi_long_max=_env_optional_float(env, "STA4_RSI_LONG_MAX", 60.0),
            rsi_short_min=_env_optional_float(env, "STA4_RSI_SHORT_MIN", 40.0),
            rsi_short_max=_env_optional_float(env, "STA4_RSI_SHORT_MAX", 70.0),
            use_higher_tf_trend_filter=_env_bool(env, "STA4_USE_HIGHER_TF_TREND", False),
        )
    elif name == "set":
        settings = StrategySettings(
            name="set",
            leverage=_env_int(env, "SET_LEVERAGE", 10),
            tp_pct=_env_float(env, "SET_TP_PCT", 0.5),
            sl_pct=_env_float(env, "SET_SL_PCT", 0.25),
            daily_profit_target_usdt=_env_optional_float(env, "SET_DAILY_PROFIT_TARGET_USDT"),
            breakeven_pnl_pct=_env_float(env, "SET_BREAKEVEN_PNL_PCT", 0.0),
            trail_start_pnl_pct=_env_float(env, "SET_TRAIL_START_PNL_PCT", 0.0),
            trail_lev_pct=_env_float(env, "SET_TRAIL_LEV_PCT", 0.0),
            early_exit_loss_pct=_env_float(env, "SET_EARLY_EXIT_LOSS_PCT", 0.0),
            max_candles_held=_env_int(env, "SET_MAX_CANDLES_HELD", 10),
            use_breakeven=_env_bool(env, "SET_USE_BREAKEVEN", False),
            use_trailing=_env_bool(env, "SET_USE_TRAILING", False),
            use_early_exit=_env_bool(env, "SET_USE_EARLY_EXIT", False),
            use_counter_signal_exit=_env_bool(env, "SET_USE_COUNTER_SIGNAL_EXIT", False),
            min_signal_score=_env_int(env, "SET_MIN_SIGNAL_SCORE", 1),
            confirmation_candles=_env_int(env, "SET_CONFIRMATION_CANDLES", 0),
            require_volume_spike=_env_bool(env, "SET_REQUIRE_VOLUME_SPIKE", False),
            volume_spike_ratio=_env_float(env, "SET_MIN_VOLUME_RATIO", 1.0),
            rsi_long_min=_env_optional_float(env, "SET_RSI_LONG_MIN", 30.0),
            rsi_long_max=_env_optional_float(env, "SET_RSI_LONG_MAX", 70.0),
            rsi_short_min=_env_optional_float(env, "SET_RSI_SHORT_MIN", 30.0),
            rsi_short_max=_env_optional_float(env, "SET_RSI_SHORT_MAX", 70.0),
            use_higher_tf_trend_filter=_env_bool(env, "SET_USE_HIGHER_TF_TREND", False),
        )
    elif name == "sta5":
        settings = StrategySettings(
            name="sta5",
            leverage=_env_int(env, "STA5_LEVERAGE", 50),
            tp_pct=_env_float(env, "STA5_TP_PCT", 10.0),
            sl_pct=_env_float(env, "STA5_SL_PCT", 7.0),
            daily_profit_target_usdt=_env_optional_float(env, "STA5_DAILY_PROFIT_TARGET_USDT"),
            breakeven_pnl_pct=_env_float(env, "STA5_BREAKEVEN_PNL_PCT", 0.0),
            trail_start_pnl_pct=_env_float(env, "STA5_TRAIL_START_PNL_PCT", 0.0),
            trail_lev_pct=_env_float(env, "STA5_TRAIL_LEV_PCT", 0.0),
            early_exit_loss_pct=_env_float(env, "STA5_EARLY_EXIT_LOSS_PCT", 0.0),
            max_candles_held=_env_int(env, "STA5_MAX_CANDLES_HELD", 20),
            use_breakeven=_env_bool(env, "STA5_USE_BREAKEVEN", False),
            use_trailing=_env_bool(env, "STA5_USE_TRAILING", False),
            use_early_exit=_env_bool(env, "STA5_USE_EARLY_EXIT", False),
            use_counter_signal_exit=_env_bool(env, "STA5_USE_COUNTER_SIGNAL_EXIT", False),
            min_signal_score=_env_int(env, "STA5_MIN_SIGNAL_SCORE", 1),
            confirmation_candles=_env_int(env, "STA5_CONFIRMATION_CANDLES", 0),
            require_volume_spike=_env_bool(env, "STA5_REQUIRE_VOLUME_SPIKE", False),
            volume_spike_ratio=_env_float(env, "STA5_MIN_VOLUME_RATIO", 1.0),
            rsi_long_min=_env_optional_float(env, "STA5_RSI_LONG_MIN", None),
            rsi_long_max=_env_optional_float(env, "STA5_RSI_LONG_MAX", 30.0),
            rsi_short_min=_env_optional_float(env, "STA5_RSI_SHORT_MIN", 70.0),
            rsi_short_max=_env_optional_float(env, "STA5_RSI_SHORT_MAX", None),
            use_higher_tf_trend_filter=_env_bool(env, "STA5_USE_HIGHER_TF_TREND", False),
        )
    else:
        raise ValueError("Unsupported STRATEGY. Use sta1, sta2, sta3, sta4, sta5, or set.")

    settings.validate()
    return settings
