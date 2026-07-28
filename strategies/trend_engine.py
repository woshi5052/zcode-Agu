"""
趋势策略引擎 —— Supertrend + MA + ATR 止损止盈
参考台湾量化架构，适配A股
"""

import numpy as np
import pandas as pd

from strategies.indicators import (
    calc_ma, calc_rsi, calc_macd, calc_atr, calc_supertrend
)
from config.settings import DEFAULT_PARAMS


class TrendEngine:
    """
    趋势跟随策略引擎

    过滤条件：
      1. Supertrend = 多头
      2. Close > MA20
      3. RSI > threshold
      4. Volume > MA_Volume * ratio

    入场信号（至少满足一个）：
      - 突破: close > N日最高价
      - 回踩: |close - MA20|/MA20 < 2%  且 从高点回落 > 2%
      - 动量: MACD > Signal 且 RSI > 50
    """

    def __init__(self, params: dict = None):
        p = params or DEFAULT_PARAMS

        self.atr_period = p.get("atr_period", 10)
        self.st_multiplier = p.get("st_multiplier", 3.0)
        self.ma_short = p.get("ma_short", 20)
        self.ma_long = p.get("ma_long", 60)
        self.rsi_period = p.get("rsi_period", 14)
        self.rsi_threshold = p.get("rsi_threshold", 35)
        self.volume_ratio = p.get("volume_ratio", 0.8)
        self.pullback_threshold = p.get("pullback_threshold", 2.0)
        self.breakout_days = p.get("breakout_days", 20)

        # 止损止盈
        self.atr_stop_multiplier = p.get("atr_stop_multiplier", 2.5)
        self.max_stop_pct = p.get("max_stop_pct", 5.0) / 100
        self.min_stop_pct = p.get("min_stop_pct", 1.5) / 100
        self.max_return_pct = p.get("max_return_pct", 10.0) / 100
        self.rr_target = p.get("rr_target", 1.5)

    # ================================================
    # 过滤条件检查
    # ================================================

    def check_trend_filter(self, df: pd.DataFrame) -> dict:
        """检查趋势过滤条件"""
        if df is None or len(df) < max(self.ma_long, self.atr_period) + 5:
            return {"pass": False, "reason": "数据不足", "details": []}

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        latest = close.iloc[-1]

        reasons = []

        # 1. Supertrend 方向
        st = calc_supertrend(high, low, close, self.atr_period, self.st_multiplier)
        st_dir = st["direction"].iloc[-1]
        if st_dir != 1:
            return {"pass": False, "reason": "Supertrend非多头", "details": [f"ST方向={st_dir}"]}

        # 2. Close > MA20
        ma20 = calc_ma(close, self.ma_short)
        if latest <= ma20.iloc[-1]:
            reasons.append(f"价格低于MA20 ({latest:.2f} <= {ma20.iloc[-1]:.2f})")

        # 3. RSI > threshold
        rsi = calc_rsi(close, self.rsi_period)
        if rsi.iloc[-1] <= self.rsi_threshold:
            reasons.append(f"RSI过低 ({rsi.iloc[-1]:.1f} <= {self.rsi_threshold})")

        # 4. 量能确认
        vol_ma20 = volume.rolling(20).mean()
        if vol_ma20.iloc[-1] > 0:
            vol_ratio = volume.iloc[-1] / vol_ma20.iloc[-1]
            if vol_ratio < self.volume_ratio:
                reasons.append(f"量能不足 ({vol_ratio:.2f} < {self.volume_ratio})")

        if reasons:
            return {"pass": False, "reason": " | ".join(reasons), "details": reasons}

        return {"pass": True, "reason": "趋势确认", "details": []}

    # ================================================
    # 入场信号检测
    # ================================================

    def detect_entry_signals(self, df: pd.DataFrame) -> list[str]:
        """检测入场信号，返回触发的信号列表"""
        if df is None or len(df) < 30:
            return []

        close = df["close"]
        high = df["high"]
        latest = close.iloc[-1]

        signals = []

        # 信号1：突破 —— close > N日最高价（不含今天）
        highest_n = high.iloc[-(self.breakout_days + 1):-1].max()
        if latest > highest_n:
            pct = (latest - highest_n) / highest_n * 100
            signals.append(f"突破{self.breakout_days}日高点(+{pct:.1f}%)")

        # 信号2：回踩 MA20
        ma20 = calc_ma(close, self.ma_short)
        ma20_val = ma20.iloc[-1]
        dist_to_ma = abs(latest - ma20_val) / ma20_val * 100

        high_20 = high.iloc[-20:].max()
        pullback_from_high = (high_20 - latest) / high_20 * 100

        if dist_to_ma <= self.pullback_threshold and pullback_from_high >= self.pullback_threshold:
            signals.append(f"回踩MA20(-{pullback_from_high:.1f}%)")

        # 信号3：动量 —— MACD > Signal + RSI > 50
        macd_line, signal_line, _ = calc_macd(close)
        rsi = calc_rsi(close, self.rsi_period)
        if macd_line.iloc[-1] > signal_line.iloc[-1] and rsi.iloc[-1] > 50:
            signals.append("MACD金叉+RSI>50")

        return signals

    # ================================================
    # 止损止盈计算
    # ================================================

    def calc_stop_loss_take_profit(self, df: pd.DataFrame, entry_price: float = None) -> dict:
        """
        计算止损价和止盈价
        止损 = max(ATR×2.5, 入场×(1-max_stop_pct), MA60, 10日低点)
        止盈 = 入场 + 止损距离 × rr_target
        """
        if df is None or len(df) < 20:
            return {"stop_loss": 0, "take_profit": 0, "rr_ratio": 0}

        if entry_price is None:
            entry_price = df["close"].iloc[-1]

        close = df["close"]
        high = df["high"]
        low = df["low"]

        # ATR 止损
        atr = calc_atr(high, low, close, self.atr_period)
        atr_val = atr.iloc[-1]
        atr_stop = entry_price - atr_val * self.atr_stop_multiplier

        # 百分比止损
        pct_stop = entry_price * (1 - self.max_stop_pct)

        # MA60 止损
        ma60 = calc_ma(close, self.ma_long)
        ma60_stop = ma60.iloc[-1]

        # 10日最低点
        low_10 = low.tail(10).min()

        # 取最严格（最高）的止损价
        stop_loss = max(atr_stop, pct_stop, ma60_stop, low_10)

        # 不超过最小止损百分比
        min_stop_price = entry_price * (1 - self.min_stop_pct)
        stop_loss = min(stop_loss, min_stop_price)

        # 止盈 = 入场 + 止损距离 × R/R
        stop_distance = entry_price - stop_loss
        take_profit = entry_price + stop_distance * self.rr_target

        # 不超过最大目标涨幅
        max_tp = entry_price * (1 + self.max_return_pct)
        take_profit = min(take_profit, max_tp)

        rr = (take_profit - entry_price) / stop_distance if stop_distance > 0 else 0

        return {
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "stop_pct": round(stop_distance / entry_price * 100, 2),
            "target_pct": round((take_profit - entry_price) / entry_price * 100, 2),
            "rr_ratio": round(rr, 2),
        }

    # ================================================
    # 综合评分
    # ================================================

    def score_stock(self, df: pd.DataFrame, signals: list[str]) -> float:
        """综合评分 (0-100)"""
        if df is None or len(df) < 20:
            return 0.0

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        latest = close.iloc[-1]

        score = 0.0

        # 1. 趋势强度 (25分) —— 价格在MA20上方距离
        ma20 = calc_ma(close, self.ma_short).iloc[-1]
        trend_strength = (latest - ma20) / ma20 * 100
        score += min(25, max(0, trend_strength * 5))  # 每1%得5分，上限25

        # 2. Supertrend强度 (20分) —— ATR占比
        st = calc_supertrend(high, low, close, self.atr_period, self.st_multiplier)
        st_val = st["supertrend"].iloc[-1]
        if st_val > 0:
            st_pct = (latest - st_val) / latest * 100
            score += min(20, max(0, st_pct * 4))

        # 3. 入场信号质量 (20分)
        if signals:
            score += min(20, len(signals) * 10)  # 每个信号10分

        # 4. 量能 (20分) —— 量比
        vol_ma = volume.rolling(20).mean().iloc[-1]
        if vol_ma > 0:
            vol_ratio = volume.iloc[-1] / vol_ma
            score += min(20, max(0, vol_ratio * 10))

        # 5. 波动稳定性 (15分) —— 越低越好
        returns = close.pct_change().dropna().tail(20)
        if len(returns) > 5:
            vol = returns.std()
            score += max(0, 15 - vol * 100)  # 波动率每1%扣1分

        return round(min(100, score), 1)

    # ================================================
    # 主分析入口
    # ================================================

    def analyze(self, df: pd.DataFrame, code: str = "", name: str = "") -> dict | None:
        """
        分析单只股票，返回推荐字典或 None（未通过过滤）
        """
        # 过滤检查
        trend_check = self.check_trend_filter(df)
        if not trend_check["pass"]:
            return None

        # 入场信号
        signals = self.detect_entry_signals(df)
        if not signals:
            return None

        # 止损止盈
        levels = self.calc_stop_loss_take_profit(df)

        # R/R 过滤
        if levels["rr_ratio"] < 1.0:
            return None

        # 评分
        score = self.score_stock(df, signals)

        # 信心等级
        if score >= 80:
            confidence = "HIGH"
        elif score >= 65:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        close = df["close"].iloc[-1]

        return {
            "code": str(code),
            "name": str(name),
            "entry_price": levels["entry_price"],
            "stop_loss": levels["stop_loss"],
            "take_profit": levels["take_profit"],
            "stop_pct": levels["stop_pct"],
            "target_pct": levels["target_pct"],
            "rr_ratio": levels["rr_ratio"],
            "score": score,
            "confidence": confidence,
            "signals": signals,
            "current_price": round(close, 2),
            "reason": trend_check["reason"],
        }
