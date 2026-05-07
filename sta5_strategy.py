"""
sta5 Strategy Module

Strategy Logic:
- Margin: 100 USDT
- Leverage: 50x
- Position size: 5,000 USDT

Entry:
- RSI > 70 → SHORT
- RSI < 30 → LONG
- If RSI is 30-70:
  - If 60%+ of latest 14 one-minute candles are red → SHORT
  - If 60%+ of latest 14 one-minute candles are green → LONG
  - Else → NO TRADE

Exit:
- Stop Loss: -7% ROI
- Take Profit: +10% ROI
"""

import logging

log = logging.getLogger(__name__)


def analyze_candle_colors(klines: list, lookback: int = 14) -> dict:
    """
    Analyze the color of the last 'lookback' candles.
    
    Args:
        klines: List of kline data from Binance API
        lookback: Number of candles to analyze (default 14)
    
    Returns:
        dict with 'green_pct', 'red_pct', 'direction' (LONG/SHORT/None)
    """
    if len(klines) < lookback:
        return {"green_pct": 0, "red_pct": 0, "direction": None}
    
    recent_candles = klines[-lookback:]
    green_count = 0
    red_count = 0
    
    for candle in recent_candles:
        open_price = float(candle[1])
        close_price = float(candle[4])
        
        if close_price > open_price:
            green_count += 1
        elif close_price < open_price:
            red_count += 1
        # Doji candles (open == close) are ignored
    
    total = green_count + red_count
    if total == 0:
        return {"green_pct": 0, "red_pct": 0, "direction": None}
    
    green_pct = (green_count / total) * 100
    red_pct = (red_count / total) * 100
    
    direction = None
    if red_pct >= 60:
        direction = "SHORT"
    elif green_pct >= 60:
        direction = "LONG"
    
    return {
        "green_count": green_count,
        "red_count": red_count,
        "green_pct": green_pct,
        "red_pct": red_pct,
        "direction": direction,
    }


def get_sta5_signal(klines_1m: list, rsi14: float) -> dict:
    """
    Generate sta5 strategy signal based on RSI and candle color analysis.
    
    Args:
        klines_1m: List of 1-minute klines from Binance API
        rsi14: Pre-calculated RSI(14) value
    
    Returns:
        dict with 'direction' (LONG/SHORT/None), 'signal_reason', 'score'
    """
    direction = None
    signal_reason = ""
    score = 0
    
    # RSI-based entries
    if rsi14 > 70:
        direction = "SHORT"
        signal_reason = f"RSI={rsi14:.2f} > 70 (overbought)"
        score = 3
        log.info(f"[STA5] SHORT signal: {signal_reason}")
    
    elif rsi14 < 30:
        direction = "LONG"
        signal_reason = f"RSI={rsi14:.2f} < 30 (oversold)"
        score = 3
        log.info(f"[STA5] LONG signal: {signal_reason}")
    
    # Neutral zone - use candle color analysis
    else:
        candle_analysis = analyze_candle_colors(klines_1m, lookback=14)
        
        if candle_analysis["direction"] == "SHORT":
            direction = "SHORT"
            signal_reason = (
                f"RSI={rsi14:.2f} neutral, "
                f"{candle_analysis['red_count']}/{len(klines_1m[-14:])} "
                f"red candles ({candle_analysis['red_pct']:.1f}%)"
            )
            score = 2
            log.info(f"[STA5] SHORT signal: {signal_reason}")
        
        elif candle_analysis["direction"] == "LONG":
            direction = "LONG"
            signal_reason = (
                f"RSI={rsi14:.2f} neutral, "
                f"{candle_analysis['green_count']}/{len(klines_1m[-14:])} "
                f"green candles ({candle_analysis['green_pct']:.1f}%)"
            )
            score = 2
            log.info(f"[STA5] LONG signal: {signal_reason}")
        
        else:
            signal_reason = (
                f"RSI={rsi14:.2f} neutral, "
                f"no clear candle bias "
                f"(green={candle_analysis['green_pct']:.1f}%, "
                f"red={candle_analysis['red_pct']:.1f}%)"
            )
            score = 0
            log.info(f"[STA5] NO TRADE: {signal_reason}")
    
    return {
        "direction": direction,
        "signal_reason": signal_reason,
        "score": score,
        "rsi14": rsi14,
    }