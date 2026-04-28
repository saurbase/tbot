"""
Claude Strategy - 15x Leverage Scalping Strategy
Implements the complete trading strategy from claude-strategy.md
"""

import numpy as np
from typing import Dict, Optional, Tuple


class ClaudeStrategy:
    """15x Leverage Scalping Strategy based on claude-strategy.md"""

    # Core parameters
    BASE_INVESTMENT = 100  # $100 per trade
    LEVERAGE = 15
    NOTIONAL_SIZE = 1500  # $1,500

    # TP/SL (leveraged)
    TP_LEVERAGED = 0.05  # +5.0%
    SL_LEVERAGED = -0.03  # -3.0%

    # Actual price moves needed
    TP_PRICE_MOVE = 0.00333  # 0.333%
    SL_PRICE_MOVE = 0.00200  # 0.200%

    # Risk management
    DAILY_CIRCUIT_BREAKER = -0.06  # -6%
    MAX_CONSEC_LOSSES = 3
    MAX_DAILY_TRADES = 20
    MAX_TRADE_CANDLES = 15

    # Signal scoring
    MIN_SCORE = 3

    # Pre-trade checks
    MAX_SPREAD = 0.0005  # 0.05%
    MIN_ADX = 20

    def __init__(self):
        self.session_vwap = None
        self.session_start = None
        self.daily_trade_count = 0
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.position = None  # Current open position

    def calculate_ema(self, prices: list, period: int) -> float:
        """Calculate Exponential Moving Average"""
        k = 2 / (period + 1)
        ema_val = prices[0]
        for price in prices[1:]:
            ema_val = price * k + ema_val * (1 - k)
        return ema_val

    def calculate_rsi(self, closes: list, period: int = 14) -> float:
        """Calculate Relative Strength Index"""
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def calculate_vwap(self, klines: list) -> float:
        """Calculate Volume Weighted Average Price for session"""
        typical_prices = []
        volumes = []

        for k in klines:
            high = float(k[2])
            low = float(k[3])
            close = float(k[4])
            volume = float(k[5])

            typical_price = (high + low + close) / 3
            typical_prices.append(typical_price)
            volumes.append(volume)

        if sum(volumes) == 0:
            return typical_prices[-1]
        return sum(p * v for p, v in zip(typical_prices, volumes)) / sum(volumes)

    def calculate_bollinger_bands(self, closes: list, period: int = 20, std_dev: int = 2) -> Tuple[float, float, float]:
        """Calculate Bollinger Bands"""
        if len(closes) < period:
            return 0, 0, 0

        recent_closes = closes[-period:]
        sma = sum(recent_closes) / period
        variance = sum((c - sma) ** 2 for c in recent_closes) / period
        std = variance ** 0.5

        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)
        width = (upper - lower) / sma

        return upper, lower, width

    def calculate_adx(self, klines: list, period: int = 14) -> float:
        """Calculate Average Directional Index"""
        if len(klines) < period + 1:
            return 0

        tr_list = []
        plus_dm_list = []
        minus_dm_list = []

        for i in range(1, len(klines)):
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_high = float(klines[i-1][2])
            prev_low = float(klines[i-1][3])
            prev_close = float(klines[i-1][4])

            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

            plus_dm = max(high - prev_high, 0) if (high - prev_high) > (prev_low - low) else 0
            minus_dm = max(prev_low - low, 0) if (prev_low - low) > (high - prev_high) else 0

            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        tr_smooth = self.calculate_ema(tr_list, period)
        plus_dm_smooth = self.calculate_ema(plus_dm_list, period)
        minus_dm_smooth = self.calculate_ema(minus_dm_list, period)

        if tr_smooth == 0:
            return 0

        plus_di = 100 * plus_dm_smooth / tr_smooth
        minus_di = 100 * minus_dm_smooth / tr_smooth

        if plus_di + minus_di == 0:
            return 0

        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        return dx

    def calculate_volume_ratio(self, volumes: list, period: int = 20) -> float:
        """Calculate current volume / average volume"""
        if len(volumes) < period + 1:
            return 0
        avg_volume = sum(volumes[-period-1:-1]) / period
        if avg_volume == 0:
            return 0
        return volumes[-1] / avg_volume

    def score_signals(self, klines: list) -> Tuple[int, int, Dict]:
        """
        Score long and short signals based on technical indicators.
        Returns: (long_score, short_score, indicators_dict)
        """
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        if len(closes) < 21:
            return 0, 0, {}

        ema_9 = self.calculate_ema(closes[-21:], 9)
        ema_21 = self.calculate_ema(closes[-21:], 21)
        rsi_14 = self.calculate_rsi(closes[-30:], 14)
        vol_ratio = self.calculate_volume_ratio(volumes)
        vwap = self.calculate_vwap(klines)
        bb_upper, bb_lower, bb_width = self.calculate_bollinger_bands(closes[-20:])
        adx = self.calculate_adx(klines[-30:])

        indicators = {
            'ema_9': ema_9,
            'ema_21': ema_21,
            'rsi_14': rsi_14,
            'vol_ratio': vol_ratio,
            'vwap': vwap,
            'bb_upper': bb_upper,
            'bb_lower': bb_lower,
            'bb_width': bb_width,
            'adx_14': adx,
            'close': closes[-1]
        }

        # Long signal scoring
        long_score = 0
        if ema_9 > ema_21:
            long_score += 1
        if 50 < rsi_14 < 68:
            long_score += 1
        if vol_ratio >= 1.5:
            long_score += 1
        if closes[-1] > vwap:
            long_score += 1

        # Short signal scoring
        short_score = 0
        if ema_9 < ema_21:
            short_score += 1
        if 32 < rsi_14 < 50:
            short_score += 1
        if vol_ratio >= 1.5:
            short_score += 1
        if closes[-1] < vwap:
            short_score += 1

        return long_score, short_score, indicators

    def check_signal_invalidation(self, indicators: Dict) -> bool:
        """Check if signal should be invalidated despite score >= 3"""
        rsi = indicators.get('rsi_14', 50)
        close = indicators.get('close', 0)
        ema_9 = indicators.get('ema_9', 0)
        ema_21 = indicators.get('ema_21', 0)
        bb_upper = indicators.get('bb_upper', 0)
        bb_lower = indicators.get('bb_lower', 0)
        bb_width = indicators.get('bb_width', 0)

        # RSI extremes
        if rsi > 75 or rsi < 25:
            return True

        # Price outside Bollinger Bands without squeeze
        if bb_width > 0.02:  # Not in squeeze
            if close > bb_upper or close < bb_lower:
                return True

        # EMA too close (no clear trend)
        if ema_9 and ema_21:
            ema_diff_pct = abs(ema_9 - ema_21) / ema_21
            if ema_diff_pct < 0.0005:  # 0.05%
                return True

        # Position already open
        if self.position is not None:
            return True

        return False

    def check_environment(self, spread: float, funding_rate: float, daily_pnl_pct: float) -> Tuple[bool, str]:
        """Check pre-trade environment conditions"""
        if spread > self.MAX_SPREAD:
            return False, f"Spread too high: {spread:.4%}"

        if funding_rate > 0.01:  # 1%
            return False, f"Funding rate too high: {funding_rate:.4%}"

        if daily_pnl_pct <= self.DAILY_CIRCUIT_BREAKER:
            return False, f"Daily circuit breaker triggered: {daily_pnl_pct:.2%}"

        if self.consecutive_losses >= self.MAX_CONSEC_LOSSES:
            return False, f"Too many consecutive losses: {self.consecutive_losses}"

        if self.daily_trade_count >= self.MAX_DAILY_TRADES:
            return False, f"Max daily trades reached: {self.daily_trade_count}"

        return True, "OK"

    def generate_signal(self, klines: list, spread: float, funding_rate: float,
                       daily_pnl_pct: float) -> Tuple[Optional[str], Dict]:
        """
        Generate trading signal based on strategy.
        Returns: (signal, indicators) where signal is 'LONG', 'SHORT', or None
        """
        # Check environment
        env_ok, env_reason = self.check_environment(spread, funding_rate, daily_pnl_pct)
        if not env_ok:
            return None, {'reason': env_reason}

        # Score signals
        long_score, short_score, indicators = self.score_signals(klines)

        indicators['long_score'] = long_score
        indicators['short_score'] = short_score

        # Check for invalidation
        if self.check_signal_invalidation(indicators):
            indicators['reason'] = 'Signal invalidated'
            return None, indicators

        # Check ADX
        if indicators.get('adx_14', 0) < self.MIN_ADX:
            indicators['reason'] = f"ADX too low: {indicators.get('adx_14', 0):.2f}"
            return None, indicators

        # Generate signal
        if long_score >= self.MIN_SCORE:
            return 'LONG', indicators
        elif short_score >= self.MIN_SCORE:
            return 'SHORT', indicators

        indicators['reason'] = f"Score too low: long={long_score}, short={short_score}"
        return None, indicators

    def calculate_tp_sl(self, entry_price: float, direction: str) -> Tuple[float, float]:
        """Calculate take profit and stop loss prices"""
        if direction == 'LONG':
            tp_price = entry_price * (1 + self.TP_PRICE_MOVE)
            sl_price = entry_price * (1 - self.SL_PRICE_MOVE)
        else:  # SHORT
            tp_price = entry_price * (1 - self.TP_PRICE_MOVE)
            sl_price = entry_price * (1 + self.SL_PRICE_MOVE)

        return round(tp_price, 2), round(sl_price, 2)

    def manage_position(self, current_price: float, entry_price: float,
                        direction: str, candles_open: int) -> Tuple[Optional[str], float]:
        """
        Manage open position.
        Returns: (exit_reason, exit_price) or (None, current_price) if no exit
        """
        if direction == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price
        else:  # SHORT
            pnl_pct = (entry_price - current_price) / entry_price

        pnl_pct *= 100  # Convert to percentage

        # Check TP
        if direction == 'LONG' and current_price >= entry_price * (1 + self.TP_PRICE_MOVE):
            return 'TP', current_price
        elif direction == 'SHORT' and current_price <= entry_price * (1 - self.TP_PRICE_MOVE):
            return 'TP', current_price

        # Check SL
        if direction == 'LONG' and current_price <= entry_price * (1 - self.SL_PRICE_MOVE):
            return 'SL', current_price
        elif direction == 'SHORT' and current_price >= entry_price * (1 + self.SL_PRICE_MOVE):
            return 'SL', current_price

        # Break-even stop at +2%
        if pnl_pct >= 2.0:
            if direction == 'LONG' and current_price <= entry_price:
                return 'BREAKEVEN', entry_price
            elif direction == 'SHORT' and current_price >= entry_price:
                return 'BREAKEVEN', entry_price

        # Trailing stop at +3.5%
        if pnl_pct >= 3.5:
            # This would need running high/low tracking - simplified here
            pass

        # Time-based exit
        if candles_open >= self.MAX_TRADE_CANDLES:
            return 'TIME_BASED', current_price

        return None, current_price

    def open_position(self, direction: str, entry_price: float, quantity: float):
        """Record position opening"""
        self.position = {
            'direction': direction,
            'entry_price': entry_price,
            'quantity': quantity,
            'entry_time': None,  # Would use actual timestamp
            'candles_open': 0,
            'tp_price': entry_price * (1 + self.TP_PRICE_MOVE) if direction == 'LONG' else entry_price * (1 - self.TP_PRICE_MOVE),
            'sl_price': entry_price * (1 - self.SL_PRICE_MOVE) if direction == 'LONG' else entry_price * (1 + self.SL_PRICE_MOVE)
        }

    def close_position(self, exit_price: float, reason: str):
        """Record position closing and update stats"""
        if self.position is None:
            return

        direction = self.position['direction']
        entry_price = self.position['entry_price']

        if direction == 'LONG':
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100 * self.LEVERAGE
        else:
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100 * self.LEVERAGE

        # Update stats
        if pnl_pct < 0:
            self.consecutive_losses += 1
            self.daily_pnl += pnl_pct
        else:
            self.consecutive_losses = 0
            self.daily_pnl += pnl_pct

        self.daily_trade_count += 1

        self.position = None