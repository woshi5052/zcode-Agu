"""
趋势策略引擎 v2.1 —— 统一ATR止损 + 大盘过滤 + 仓位管理
"""

import numpy as np
import pandas as pd

from strategies.indicators import calc_ma, calc_rsi, calc_macd, calc_atr, calc_supertrend


class TrendEngine:
    """
    趋势跟随策略引擎 v2.1

    新增:
      - 大盘过滤: HS300指数 < MA20 → 不开仓
      - 统一止损: ATR×2 初始止损 + ATR×2 移动止损
      - 仓位管理: 强势3仓 / 震荡1仓 / 弱势空仓

    保留:
      - Supertrend多头 + Close>MA20 + RSI区间 + 量比过滤
      - 突破 / MACD 入场信号
    """

    def __init__(self, params: dict = None):
        p = params or {}

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

        # 统一止损 (v2.1 简化)
        self.atr_stop_mult = p.get("atr_stop_multiplier", 2.0)
        self.trailing_atr_mult = p.get("trailing_atr_multiplier", 2.0)
        self.min_stop_pct = p.get("min_stop_pct", 2.0) / 100

        # 持仓管理
        self.max_holding_days = p.get("max_holding_days", 20)
        self.single_position_pct = p.get("single_position_pct", 0.20)

        # 大盘过滤 (v2.1 新增)
        self.index_ma_period = p.get("index_ma_period", 20)
        self.bull_threshold = p.get("bull_threshold", 1.02)
        self.bear_threshold = p.get("bear_threshold", 0.98)
        self.bull_positions = p.get("bull_positions", 3)
        self.sideways_positions = p.get("sideways_positions", 1)
        self.bear_positions = p.get("bear_positions", 0)

        # 大盘状态
        self._index_df: pd.DataFrame | None = None
        self._market_regime: str = "sideways"  # bull / sideways / bear
        self._max_positions: int = 1

    def set_index_data(self, index_df: pd.DataFrame):
        """设置大盘指数数据，用于牛熊判断"""
        self._index_df = index_df
        self._update_regime()

    def _update_regime(self):
        """根据HS300指数判断当前市场状态"""
        if self._index_df is None or len(self._index_df) < self.index_ma_period:
            self._market_regime = "sideways"
            self._max_positions = self.sideways_positions
            return

        close = self._index_df["close"]
        ma = calc_ma(close, self.index_ma_period)
        latest = close.iloc[-1]
        ma_val = ma.iloc[-1]

        ratio = latest / ma_val if ma_val > 0 else 1.0

        if ratio > self.bull_threshold:
            self._market_regime = "bull"
            self._max_positions = self.bull_positions
        elif ratio < self.bear_threshold:
            self._market_regime = "bear"
            self._max_positions = self.bear_positions
        else:
            self._market_regime = "sideways"
            self._max_positions = self.sideways_positions

    def get_regime(self) -> str:
        return self._market_regime

    def get_max_positions(self) -> int:
        return self._max_positions

    def can_open_position(self) -> bool:
        """大盘是否允许开仓"""
        return self._max_positions > 0

    # ================================================
    # 过滤条件
    # ================================================

    def check_trend_filter(self, df: pd.DataFrame, idx: int = -1) -> dict:
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
            reasons.append("价格低于MA20")

        # 3. RSI 区间
        rsi = calc_rsi(close, self.rsi_period)
        rsi_val = rsi.iloc[idx]
        if rsi_val <= self.rsi_threshold:
            reasons.append(f"RSI过低({rsi_val:.1f})")
        if rsi_val >= self.rsi_upper:
            reasons.append(f"RSI过高({rsi_val:.1f})")

        # 4. 量能
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
        if df is None or len(df) < max(self.breakout_days, 26) + 5:
            return []

        close = df["close"]
        high = df["high"]
        signals = []

        # 突破 N日高点
        start = idx - self.breakout_days
        if start >= 0:
            highest_n = high.iloc[start:idx].max()
            if close.iloc[idx] > highest_n:
                pct = (close.iloc[idx] - highest_n) / highest_n * 100
                signals.append(f"突破{self.breakout_days}日高点(+{pct:.1f}%)")

        # MACD金叉 + RSI>50
        macd_line, signal_line, _ = calc_macd(close)
        rsi = calc_rsi(close, self.rsi_period)
        if macd_line.iloc[idx] > signal_line.iloc[idx] and rsi.iloc[idx] > 50:
            signals.append("MACD金叉+RSI>50")

        return signals

    # ================================================
    # 统一止损 (v2.1 简化)
    # ================================================

    def calc_initial_stop(self, df: pd.DataFrame, entry_price: float,
                          entry_idx: int = -1) -> float:
        """初始止损 = 入场价 - ATR × 2.0（不低于最小止损%）"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        atr = calc_atr(high, low, close, self.atr_period)
        atr_val = atr.iloc[entry_idx]
        atr_stop = entry_price - atr_val * self.atr_stop_mult

        # 最小止损保护
        min_stop = entry_price * (1 - self.min_stop_pct)

        return round(max(atr_stop, min_stop), 2)

    def update_trailing_stop(self, df: pd.DataFrame,
                             highest_since_entry: float,
                             current_idx: int) -> float:
        """移动止损 = 持仓最高价 - ATR × 2.0"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        atr = calc_atr(high, low, close, self.atr_period)
        atr_val = atr.iloc[current_idx]

        return round(highest_since_entry - atr_val * self.trailing_atr_mult, 2)

    def check_exit(self, df: pd.DataFrame, current_idx: int,
                   stop_price: float, holding_days: int) -> dict:
        """检查离场条件"""
        close = df["close"]
        high = df["high"]
        low = df["low"]

        current_close = close.iloc[current_idx]

        # 跌破移动止损
        if current_close <= stop_price:
            return {"exit": True, "reason": "跌破止损", "price": stop_price}

        # Supertrend 翻空
        st = calc_supertrend(high, low, close, self.atr_period, self.st_multiplier)
        if st["direction"].iloc[current_idx] == -1:
            return {"exit": True, "reason": "ST翻空", "price": current_close}

        # 超时
        if holding_days >= self.max_holding_days:
            return {"exit": True, "reason": f"持仓{self.max_holding_days}天到期",
                    "price": current_close}

        return {"exit": False, "reason": "", "price": 0}

    # ================================================
    # T+1 入场
    # ================================================

    def simulate_entry(self, df: pd.DataFrame, entry_price: float,
                       entry_idx: int) -> dict:
        stop_loss = self.calc_initial_stop(df, entry_price, entry_idx)
        return {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "stop_pct": round((entry_price - stop_loss) / entry_price * 100, 2),
            "trailing_stop": stop_loss,
            "highest_since_entry": entry_price,
        }

    # ================================================
    # 评分
    # ================================================

    def score_stock(self, df: pd.DataFrame, signals: list[str],
                    idx: int = -1) -> float:
        if df is None or len(df) < 20:
            return 0.0

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        latest = close.iloc[idx]

        score = 0.0

        # 趋势强度 (30分)
        ma20 = calc_ma(close, self.ma_short).iloc[idx]
        trend_strength = (latest - ma20) / ma20 * 100
        score += min(30, max(0, trend_strength * 6))

        # Supertrend强度 (20分)
        st = calc_supertrend(high, low, close, self.atr_period, self.st_multiplier)
        st_val = st["supertrend"].iloc[idx]
        if st_val > 0:
            st_pct = (latest - st_val) / latest * 100
            score += min(20, max(0, st_pct * 4))

        # 入场信号 (20分)
        if "突破" in str(signals):
            score += 12
        if "MACD" in str(signals):
            score += 8

        # 量能 (20分)
        vol_ma = volume.rolling(20).mean().iloc[idx]
        if vol_ma > 0:
            vol_ratio = volume.iloc[idx] / vol_ma
            score += min(20, max(0, (vol_ratio - 1) * 10))

        # 波动稳定性 (10分)
        returns = close.pct_change().dropna().tail(20)
        if len(returns) > 5:
            vol_val = returns.std()
            score += max(0, 10 - vol_val * 80)

        return round(min(100, score), 1)

    # ================================================
    # 主分析入口
    # ================================================

    def analyze(self, df: pd.DataFrame, code: str = "",
                name: str = "") -> dict | None:
        idx = -1

        trend_check = self.check_trend_filter(df, idx)
        if not trend_check["pass"]:
            return None

        signals = self.detect_entry_signals(df, idx)
        if not signals:
            return None

        close = df["close"].iloc[idx]
        entry_price = round(close, 2)
        stop_loss = self.calc_initial_stop(df, entry_price, idx)
        stop_pct = round((entry_price - stop_loss) / entry_price * 100, 2)

        if stop_pct < 1.0:
            return None

        score = self.score_stock(df, signals, idx)

        atr = calc_atr(df["high"], df["low"], df["close"], self.atr_period).iloc[idx]
        target_price = round(entry_price + atr * self.trailing_atr_mult, 2)
        target_pct = round((target_price - entry_price) / entry_price * 100, 2)

        confidence = "HIGH" if score >= 80 else ("MEDIUM" if score >= 65 else "LOW")

        return {
            "code": str(code),
            "name": str(name),
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "stop_pct": stop_pct,
            "target_price": target_price,
            "target_pct": target_pct,
            "holding_days": self.max_holding_days,
            "score": score,
            "confidence": confidence,
            "signals": signals,
            "current_price": entry_price,
            "reason": trend_check["reason"],
            "regime": self._market_regime,
            "max_positions": self._max_positions,
        }
