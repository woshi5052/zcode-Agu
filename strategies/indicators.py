"""
技术指标库 —— 纯 pandas/numpy 实现，无第三方 ta 依赖
"""

import numpy as np
import pandas as pd


def calc_ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def calc_ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50)


def calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calc_supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                    period: int = 14, multiplier: float = 3.0) -> pd.DataFrame:
    """返回含 supertrend / direction 两列的 DataFrame（标准实现，方向记录法）"""
    atr = calc_atr(high, low, close, period)
    hl2 = (high + low) / 2
    upper_basic = hl2 + multiplier * atr
    lower_basic = hl2 - multiplier * atr

    n = len(close)
    upper = np.zeros(n)
    lower = np.zeros(n)
    st = np.zeros(n)
    direction = np.ones(n, dtype=int)

    upper[0] = upper_basic.iloc[0]
    lower[0] = lower_basic.iloc[0]
    st[0] = lower[0]
    direction[0] = 1

    for i in range(1, n):
        up = upper_basic.iloc[i]
        low_ = lower_basic.iloc[i]
        prev_close = close.iloc[i - 1]

        # 通道带收紧逻辑
        if up < upper[i - 1] or prev_close > upper[i - 1]:
            upper[i] = up
        else:
            upper[i] = upper[i - 1]

        if low_ > lower[i - 1] or prev_close < lower[i - 1]:
            lower[i] = low_
        else:
            lower[i] = lower[i - 1]

        # 方向判定（基于上一根方向，不比较浮点值）
        if direction[i - 1] == 1:  # 多头
            direction[i] = -1 if close.iloc[i] < lower[i] else 1
        else:  # 空头
            direction[i] = 1 if close.iloc[i] > upper[i] else -1

        st[i] = lower[i] if direction[i] == 1 else upper[i]

    return pd.DataFrame(
        {"supertrend": st, "direction": direction},
        index=close.index,
    )
