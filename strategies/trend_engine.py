"""
趋势策略引擎 v2.0 —— 移动止损 + T+1执行 + 纯趋势跟随
"""

import numpy as np
import pandas as pd

from strategies.indicators import (
    calc_ma, calc_rsi, calc_macd, calc_atr, calc_supertrend
)
from config.settings import DEFAULT_PARAMS


class TrendEngine:
    """
    趋势跟随策略引擎 v2.0

    过滤条件（全部通过）：
      1. Supertrend = 多头
      2. Close > MA20
      3. 40 < RSI < 65
      4. Volume / MA_Volume(20) >= 1.2

    入场信号（至少一个）：
      - 突破: close > N日最高价
      - 动量: MACD > Signal + RSI > 50

    离场条件（任一触发）：
      - 收盘价 <= 移动止损线（入场后最高价 - ATR×3.0）
      - Supertrend 翻空
      - 持仓 > 20天强制平仓
    """

    def __init__(self, params: dict = None):
        p = params or DEFAULT_PARAMS

        # 指标参数
        self.atr_period = p.get("atr_period", 10)
        self.st_multiplier = p.get("st_multiplier", 3.0)
        self.ma_short = p.get("ma_short", 20)
        self.ma_long = p.get("ma_long", 60)
        self.rsi_period = p.get("rsi_period", 14)

        # 过滤阈值
        self.rsi_threshold = p.get("rsi_threshold", 40)
        self.rsi_upper = p.get("rsi_upper", 65)
        self.volume_ratio = p.get("volume_ratio", 1.2)
        self.breakout_days = p.get("breakout_days", 20)

        # 止损参数
        self.initial_stop_mult = p.get("atr_stop_multiplier", 2.5)
        self.trailing_atr_mult = p.get("trailing_atr_multiplier", 3.0)
        self.max_stop_pct = p.get("max_stop_pct", 6.0) / 100
        self.min_stop_pct = p.get("min_stop_pct", 2.0) / 100

        # 持仓管理
        self.max_holding_days = p.get("max_holding_days", 20)
        self.max_holding_days = p.get("max_holding_days", 20)

    # ================================================
    # 过滤条件
    # ================================================

    def check_trend_filter(self, df: pd.DataFrame, idx: int = -1) -> dict:
        """检查趋势过滤条件"""
        min_bars = max(self.ma_long, self.atr_period) + 5
        if df is None or len(df) < min_bars:
            return {"pass": False, "reason": "数据不足"}

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        reasons = []

        # 1. Supertrend
        st = calc_supertrend(high, low, close, self.atr_period, self.st_multiplier)
        if st["direction"].iloc[idx] != 1:
            return {"pass": False, "reason": "Supertrend非多头"}

        # 2. Close > MA20
        ma20 = calc_ma(close, self.ma_short)
        if close.iloc[idx] <= ma20.iloc[idx]:
            reasons.append(f"价格低于MA20")

        # 3. RSI 区间
        rsi = calc_rsi(close, self.rsi_period)
        rsi_val = rsi.iloc[idx]
        if rsi_val <= self.rsi_threshold:
            reasons.append(f"RSI过低({rsi_val:.1f})")
        if rsi_val >= self.rsi_upper:
            reasons.append(f"RSI过高({rsi_val:.1f})")

        # 4. 量能确认
        vol_ma20 = volume.rolling(20).mean()
        if vol_ma20.iloc[idx] > 0:
            vr = volume.iloc[idx] / vol_ma20.iloc[idx]
            if vr < self.volume_ratio:
                reasons.append(f"量能不足({vr:.2f})")

        if reasons:
            return {"pass": False, "reason": " | ".join(reasons)}

        return {"pass": True, "reason": "趋势确认"}

    # ================================================
    # 入场信号
    # ================================================

    def detect_entry_signals(self, df: pd.DataFrame, idx: int = -1) -> list[str]:
        """检测入场信号"""
        if df is None or len(df) < max(self.breakout_days, 26) + 5:
            return []

        close = df["close"]
        high = df["high"]
        signals = []

        # 信号1：突破 N日高点
        start = idx - self.breakout_days
        end = idx
        if start >= 0:
            highest_n = high.iloc[start:end].max()
            if close.iloc[idx] > highest_n:
                pct = (close.iloc[idx] - highest_n) / highest_n * 100
                signals.append(f"突破{self.breakout_days}日高点(+{pct:.1f}%)")

        # 信号2：动量确认 MACD > Signal + RSI > 50
        macd_line, signal_line, _ = calc_macd(close)
        rsi = calc_rsi(close, self.rsi_period)
        if (macd_line.iloc[idx] > signal_line.iloc[idx] and
                rsi.iloc[idx] > 50):
            signals.append("MACD金叉+RSI>50")

        return signals

    # ================================================
    # 止损计算
    # ================================================

    def calc_initial_stop(self, df: pd.DataFrame, entry_price: float,
                          entry_idx: int = -1) -> float:
        """计算初始止损价"""
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ATR止损
        atr = calc_atr(high, low, close, self.atr_period)
        atr_val = atr.iloc[entry_idx]
        atr_stop = entry_price - atr_val * self.initial_stop_mult

        # 百分比止损
        pct_stop = entry_price * (1 - self.max_stop_pct)

        # MA60止损
        ma60 = calc_ma(close, self.ma_long)
        ma60_stop = ma60.iloc[entry_idx]

        # 10日低点
        low_10 = low.iloc[max(0, entry_idx-9):entry_idx+1].min()

        stop = max(atr_stop, pct_stop, ma60_stop, low_10)

        # 不超过最小止损百分比
        min_stop = entry_price * (1 - self.min_stop_pct)
        stop = min(stop, min_stop)

        return round(stop, 2)

    def update_trailing_stop(self, df: pd.DataFrame,
                             entry_price: float,
                             highest_since_entry: float,
                             current_idx: int) -> float:
        """
        更新移动止损线
        = 入场后最高价 − ATR × trailing_mult
        止损只能上移，不能下移
        """
        high = df["high"]
        low = df["low"]
        close = df["close"]

        atr = calc_atr(high, low, close, self.atr_period)
        atr_val = atr.iloc[current_idx]

        trailing = highest_since_entry - atr_val * self.trailing_atr_mult

        # 止损不低于初始止损
        initial_stop = self.calc_initial_stop(df, entry_price, current_idx)
        trailing = max(trailing, initial_stop)

        return round(trailing, 2)

    def check_exit(self, df: pd.DataFrame, current_idx: int,
                   stop_price: float, holding_days: int) -> dict:
        """
        检查离场条件
        返回 {"exit": bool, "reason": str, "price": float}
        """
        close = df["close"]
        high = df["high"]
        low = df["low"]

        current_close = close.iloc[current_idx]

        # 条件1：跌破移动止损
        if current_close <= stop_price:
            return {"exit": True, "reason": "跌破止损", "price": stop_price}

        # 条件2：Supertrend 翻空
        st = calc_supertrend(high, low, close, self.atr_period, self.st_multiplier)
        if st["direction"].iloc[current_idx] == -1:
            return {"exit": True, "reason": "ST翻空", "price": current_close}

        # 条件3：超时强制平仓
        if holding_days >= self.max_holding_days:
            return {"exit": True, "reason": f"持仓{self.max_holding_days}天到期",
                    "price": current_close}

        return {"exit": False, "reason": "", "price": 0}

    # ================================================
    # T+1 入场模拟（回测用）
    # ================================================

    def simulate_entry(self, df: pd.DataFrame, entry_price: float,
                       entry_idx: int) -> dict:
        """
        基于T+1开盘价重新计算入场参数
        用于回测消除未来函数
        """
        # 用实际入场价重新算止损
        stop_loss = self.calc_initial_stop(df, entry_price, entry_idx)

        close = df["close"]
        rsi = calc_rsi(close, self.rsi_period)

        return {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "stop_pct": round((entry_price - stop_loss) / entry_price * 100, 2),
            "trailing_stop": stop_loss,  # 初始=固定止损
            "highest_since_entry": entry_price,
            "rsi_at_entry": round(rsi.iloc[entry_idx], 1),
        }

    # ================================================
    # 综合评分
    # ================================================

    def score_stock(self, df: pd.DataFrame, signals: list[str],
                    idx: int = -1) -> float:
        """综合评分 (0-100)"""
        if df is None or len(df) < 20:
            return 0.0

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        latest = close.iloc[idx]

        score = 0.0

        # 1. 趋势强度 (30分)
        ma20 = calc_ma(close, self.ma_short).iloc[idx]
        trend_strength = (latest - ma20) / ma20 * 100
        score += min(30, max(0, trend_strength * 6))

        # 2. Supertrend强度 (20分)
        st = calc_supertrend(high, low, close, self.atr_period, self.st_multiplier)
        st_val = st["supertrend"].iloc[idx]
        if st_val > 0:
            st_pct = (latest - st_val) / latest * 100
            score += min(20, max(0, st_pct * 4))

        # 3. 入场信号 (20分)
        if "突破" in str(signals):
            score += 12
        if "MACD" in str(signals):
            score += 8

        # 4. 量能 (20分)
        vol_ma = volume.rolling(20).mean().iloc[idx]
        if vol_ma > 0:
            vol_ratio = volume.iloc[idx] / vol_ma
            score += min(20, max(0, (vol_ratio - 1) * 10))

        # 5. 波动稳定性 (10分)
        returns = close.pct_change().dropna().tail(20)
        if len(returns) > 5:
            vol = returns.std()
            score += max(0, 10 - vol * 80)

        return round(min(100, score), 1)

    # ================================================
    # 主分析入口（实时推送用）
    # ================================================

    def analyze(self, df: pd.DataFrame, code: str = "",
                name: str = "") -> dict | None:
        """
        分析单只股票（实时模式）
        基于最新收盘价，给出次日入场建议
        """
        idx = -1

        # 过滤
        trend_check = self.check_trend_filter(df, idx)
        if not trend_check["pass"]:
            return None

        # 入场信号
        signals = self.detect_entry_signals(df, idx)
        if not signals:
            return None

        # 初始止损（基于收盘价预估）
        close = df["close"].iloc[idx]
        entry_price = round(close, 2)
        stop_loss = self.calc_initial_stop(df, entry_price, idx)
        stop_pct = round((entry_price - stop_loss) / entry_price * 100, 2)

        # R/R 过滤
        if stop_pct < 1.0:
            return None

        # 评分
        score = self.score_stock(df, signals, idx)

        # 信心
        if score >= 80:
            confidence = "HIGH"
        elif score >= 65:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return {
            "code": str(code),
            "name": str(name),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "stop_pct": stop_pct,
            "score": score,
            "confidence": confidence,
            "signals": signals,
            "current_price": entry_price,
            "reason": trend_check["reason"],
        }
