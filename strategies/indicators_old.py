"""
技术指标库 —— MA / RSI / MACD / KDJ / ATR / Supertrend
"""

import numpy as np
import pandas as pd


def calc_ma(close: pd.Series, period: int = 20) -> pd.Series:
    """移动均线"""
    return close.rolling(window=period).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 相对强弱指标"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD 指标，返回 (macd, signal_line, histogram)"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
             n: int = 9, m1: int = 3, m2: int = 3):
    """KDJ 指标，返回 (K, D, J)"""
    lowest_low = low.rolling(window=n).min()
    highest_high = high.rolling(window=n).max()

    rsv = (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan) * 100

    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d

    return k, d, j


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """ATR 平均真实波幅"""
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, min_periods=period).mean()

    return atr


def calc_supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                    period: int = 10, multiplier: float = 3.0):
    """
    Supertrend 超级趋势指标

    返回 DataFrame:
      - supertrend: 趋势线
      - direction: 1=多头, -1=空头
    """
    atr = calc_atr(high, low, close, period)

    # 基础波段
    hl_avg = (high + low) / 2

    # 上轨 = 中轨 + multiplier * ATR
    # 下轨 = 中轨 - multiplier * ATR
    upper_band = hl_avg + multiplier * atr
    lower_band = hl_avg - multiplier * atr

    # 逐日计算最终 SuperTrend
    n = len(close)
    supertrend = pd.Series(np.nan, index=close.index)
    direction = pd.Series(0, index=close.index)

    for i in range(1, n):
        # 上轨：当前上轨 < 前上轨 或 前收盘 > 前上轨 → 当前上轨
        if upper_band.iloc[i] < upper_band.iloc[i-1] or close.iloc[i-1] > upper_band.iloc[i-1]:
            curr_upper = upper_band.iloc[i]
        else:
            curr_upper = upper_band.iloc[i-1]

        # 下轨：当前下轨 > 前下轨 或 前收盘 < 前下轨 → 当前下轨
        if lower_band.iloc[i] > lower_band.iloc[i-1] or close.iloc[i-1] < lower_band.iloc[i-1]:
            curr_lower = lower_band.iloc[i]
        else:
            curr_lower = lower_band.iloc[i-1]

        # 方向判断
        if close.iloc[i] > curr_upper:
            direction.iloc[i] = 1   # 多头
        elif close.iloc[i] < curr_lower:
            direction.iloc[i] = -1  # 空头
        else:
            direction.iloc[i] = direction.iloc[i-1] if i > 0 else 1

        supertrend.iloc[i] = curr_lower if direction.iloc[i] == 1 else curr_upper

    return pd.DataFrame({
        "supertrend": supertrend,
        "direction": direction.astype(int),
    })


def calc_volatility(close: pd.Series, period: int = 20) -> float:
    """年化波动率"""
    returns = close.pct_change().dropna()
    if len(returns) < period:
        return 0.0
    return float(returns.tail(period).std() * np.sqrt(250))


def calc_max_drawdown(close: pd.Series) -> float:
    """最大回撤"""
    peak = close.expanding().max()
    dd = (close - peak) / peak
    return float(dd.min())
