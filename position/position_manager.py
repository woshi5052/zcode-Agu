"""
持仓管理与卖出决策引擎 v1.0
四层卖出体系 + 综合评分卡，强卖优先，弱信号参考
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict
import json
from pathlib import Path


# ===================== 数据结构 =====================

@dataclass
class Position:
    """单只持仓信息"""
    code: str
    name: str
    entry_price: float
    hold_days: int
    prev_stop_loss: float
    highest_price: float


@dataclass
class DailyIndicator:
    """当日指标"""
    close: float
    atr: float
    supertrend_bullish: bool
    ma20: float
    rsi: float
    volume_ratio: float
    day_change_pct: float
    recent_3d_high: float
    prev_3d_high: float


@dataclass
class MarketEnv:
    """大盘环境"""
    hs300_below_ma20: bool = False


@dataclass
class SellAnalysisResult:
    """单支持仓分析结果"""
    code: str
    name: str
    current_price: float
    profit_pct: float
    new_stop_loss: float
    sell_score: int
    action: str
    triggers: List[str] = field(default_factory=list)


# ===================== 配置 =====================

@dataclass
class SellConfig:
    """卖出策略参数"""
    atr_stop_multiplier: float = 2.0
    profit_trigger_1: float = 3.0
    profit_trigger_2: float = 5.0
    profit_trigger_3: float = 10.0
    profit_atr_multiplier: float = 1.5
    time_stage_1_days: int = 5
    time_stage_2_days: int = 10
    time_stage_3_days: int = 15
    time_atr_1: float = 2.0
    time_atr_2: float = 1.8
    time_atr_3: float = 1.5
    time_atr_4: float = 1.2
    max_hold_days: int = 20
    score_sell_threshold: int = 6
    score_strong_sell: int = 10
    score_broken_ma20: int = 3
    score_heavy_volume_stagnation: int = 3
    score_market_risk: int = 4
    score_long_hold: int = 2
    score_overbought: int = 2
    score_high_profit: int = 1

    @classmethod
    def load_from_json(cls, path: str = "config/params.json") -> "SellConfig":
        if Path(path).exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f).get("sell_config", {})
            return cls(**data)
        return cls()


# ===================== 核心引擎 =====================

class PositionManager:
    def __init__(self, config: Optional[SellConfig] = None):
        self.config = config or SellConfig()

    def analyze_portfolio(
        self,
        positions: List[Position],
        indicators: Dict[str, DailyIndicator],
        market_env: Optional[MarketEnv] = None
    ) -> List[SellAnalysisResult]:
        market_env = market_env or MarketEnv()
        results = []
        for pos in positions:
            ind = indicators.get(pos.code)
            if not ind:
                continue
            results.append(self._analyze_single(pos, ind, market_env))
        return results

    def _analyze_single(self, pos: Position, ind: DailyIndicator,
                        market_env: MarketEnv) -> SellAnalysisResult:
        cfg = self.config
        triggers = []
        profit_pct = (ind.close - pos.entry_price) / pos.entry_price * 100

        # ---- Step1: 强卖检查（最高优先级） ----
        trailing_stop = pos.highest_price - cfg.atr_stop_multiplier * ind.atr

        # ATR移动止损触发
        if ind.close <= trailing_stop:
            return SellAnalysisResult(
                code=pos.code, name=pos.name,
                current_price=ind.close, profit_pct=round(profit_pct, 2),
                new_stop_loss=round(trailing_stop, 2),
                sell_score=cfg.score_strong_sell,
                action="🔴 立即卖出",
                triggers=["ATR移动止损触发"]
            )

        # Supertrend翻空
        if not ind.supertrend_bullish:
            return SellAnalysisResult(
                code=pos.code, name=pos.name,
                current_price=ind.close, profit_pct=round(profit_pct, 2),
                new_stop_loss=round(ind.close, 2),
                sell_score=cfg.score_strong_sell,
                action="🔴 立即卖出",
                triggers=["Supertrend翻空，买入逻辑失效"]
            )

        # ---- Step2: 计算各层止损（取最紧值） ----
        stop_candidates = [trailing_stop]

        # Layer3: 利润保护
        profit_stop = self._calc_profit_stop(pos, ind, profit_pct)
        if profit_stop:
            stop_candidates.append(profit_stop)

        # Layer4: 时间衰减（仅浮盈<3%）
        if profit_pct < cfg.profit_trigger_1:
            time_stop = self._calc_time_decay_stop(pos, ind)
            if time_stop:
                stop_candidates.append(time_stop)

        # 止损只上移不下移
        raw_new_stop = max(stop_candidates)
        new_stop_loss = max(raw_new_stop, pos.prev_stop_loss)

        # ---- Step3: 卖出评分 ----
        score = 0

        # 大盘风险
        if market_env.hs300_below_ma20:
            score += cfg.score_market_risk
            triggers.append("大盘跌破MA20")

        # 跌破MA20
        if ind.close < ind.ma20:
            score += cfg.score_broken_ma20
            triggers.append("收盘跌破MA20")

        # 放量滞涨
        if ind.volume_ratio > 1.5 and abs(ind.day_change_pct) < 1.0:
            score += cfg.score_heavy_volume_stagnation
            triggers.append("放量滞涨")

        # 超买钝化：RSI>80 且 3天不新高
        if ind.rsi > 80 and ind.recent_3d_high <= ind.prev_3d_high:
            score += cfg.score_overbought
            triggers.append("RSI超买+3天不新高")

        # 持仓久且无盈利
        if pos.hold_days > cfg.time_stage_3_days and profit_pct < cfg.profit_trigger_1:
            score += cfg.score_long_hold
            triggers.append(f"持仓{pos.hold_days}天浮盈不足")

        # 高浮盈提示
        high_profit_flag = profit_pct >= cfg.profit_trigger_3
        if high_profit_flag:
            score += cfg.score_high_profit
            triggers.append("浮盈超10%，建议锁利")

        # ---- Step4: 操作建议 ----
        if score >= cfg.score_sell_threshold:
            action = "🔴 建议卖出"
        elif high_profit_flag:
            action = "🟡 建议锁利(卖50%)"
        elif pos.hold_days >= cfg.max_hold_days:
            action = "🔴 到期清仓"
        else:
            action = "🟢 持有观望"

        return SellAnalysisResult(
            code=pos.code, name=pos.name,
            current_price=round(ind.close, 2),
            profit_pct=round(profit_pct, 2),
            new_stop_loss=round(new_stop_loss, 2),
            sell_score=score, action=action, triggers=triggers
        )

    def _calc_profit_stop(self, pos: Position, ind: DailyIndicator,
                          profit_pct: float) -> Optional[float]:
        cfg = self.config
        if profit_pct >= cfg.profit_trigger_2:
            return pos.highest_price - cfg.profit_atr_multiplier * ind.atr
        elif profit_pct >= cfg.profit_trigger_1:
            return pos.entry_price  # 保本
        return None

    def _calc_time_decay_stop(self, pos: Position, ind: DailyIndicator) -> Optional[float]:
        cfg = self.config
        days = pos.hold_days
        if days <= cfg.time_stage_1_days:
            mult = cfg.time_atr_1
        elif days <= cfg.time_stage_2_days:
            mult = cfg.time_atr_2
        elif days <= cfg.time_stage_3_days:
            mult = cfg.time_atr_3
        else:
            mult = cfg.time_atr_4
        return pos.highest_price - mult * ind.atr
