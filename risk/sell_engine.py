"""
卖出决策引擎 v2.0 — 四层体系 + 评分卡（回测/实盘共用，单一事实来源）

接口:
  evaluate(pos, bar, regime) -> SellSignal | None
  evaluate_portfolio(positions, bars, regime) -> list[SellSignal]
"""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
from pathlib import Path


class ExecTime(Enum):
    NEXT_OPEN = "next_open"    # 次日开盘执行 (硬止损/翻空)
    NEXT_1450 = "next_1450"    # 次日14:50执行 (评分触发/锁利/到期)


@dataclass
class SellSignal:
    action: str          # "立即卖出" / "建议卖出" / "建议锁利" / "到期清仓"
    priority: int        # 1硬止损 2翻空 3评分 4锁利 5到期
    reason: str
    exec_time: ExecTime
    new_stop_loss: float
    sell_score: int


@dataclass
class Position:
    code: str
    name: str
    entry_price: float
    hold_days: int
    prev_stop_loss: float
    highest_price: float


@dataclass
class Bar:
    """当日K线数据"""
    close: float
    open: float
    high: float
    low: float
    atr: float
    supertrend_bullish: bool
    ma20: float
    rsi: float
    volume_ratio: float
    day_change_pct: float
    recent_3d_high: float
    prev_3d_high: float


@dataclass
class Regime:
    state: str = "ranging"   # bull / bear / ranging
    hs300_below_ma20: bool = False


@dataclass
class SellConfig:
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
    score_broken_ma20: int = 3
    score_heavy_volume_stagnation: int = 3
    score_market_risk: int = 4
    score_long_hold: int = 2
    score_overbought: int = 2
    score_high_profit: int = 1

    @classmethod
    def load(cls, path: str = "config/params.json") -> "SellConfig":
        p = Path(path)
        if p.exists():
            with open(p) as f:
                data = json.load(f)
            return cls(**data.get("sell_config", {}))
        return cls()


_SELL_CONFIG = SellConfig()


def set_config(cfg: SellConfig):
    global _SELL_CONFIG
    _SELL_CONFIG = cfg


def evaluate(pos: Position, bar: Bar, regime: Regime = None) -> Optional[SellSignal]:
    """
    评估单支持仓是否应该卖出
    """
    cfg = _SELL_CONFIG
    regime = regime or Regime()
    profit_pct = (bar.close - pos.entry_price) / pos.entry_price * 100
    triggers = []

    # ---- Layer1: 硬止损（最高优先级） ----
    trailing_stop = pos.highest_price - cfg.atr_stop_multiplier * bar.atr
    effective_stop = max(trailing_stop, pos.prev_stop_loss)

    if bar.close <= effective_stop:
        return SellSignal(
            action="立即卖出", priority=1, reason="ATR移动止损触发",
            exec_time=ExecTime.NEXT_OPEN,
            new_stop_loss=round(effective_stop, 2), sell_score=10,
        )

    # ---- Layer1b: Supertrend 翻空 ----
    if not bar.supertrend_bullish:
        return SellSignal(
            action="立即卖出", priority=2, reason="Supertrend翻空，买入逻辑失效",
            exec_time=ExecTime.NEXT_OPEN,
            new_stop_loss=round(bar.close, 2), sell_score=10,
        )

    # ---- 计算各层止损 ----
    stop_candidates = [trailing_stop]

    # Layer3: 利润保护
    if profit_pct >= cfg.profit_trigger_2:
        stop_candidates.append(
            pos.highest_price - cfg.profit_atr_multiplier * bar.atr)
    elif profit_pct >= cfg.profit_trigger_1:
        stop_candidates.append(pos.entry_price)  # 保本

    # Layer4: 时间衰减 (仅浮盈<3%)
    if profit_pct < cfg.profit_trigger_1:
        days = pos.hold_days
        if days <= cfg.time_stage_1_days:
            mult = cfg.time_atr_1
        elif days <= cfg.time_stage_2_days:
            mult = cfg.time_atr_2
        elif days <= cfg.time_stage_3_days:
            mult = cfg.time_atr_3
        else:
            mult = cfg.time_atr_4
        stop_candidates.append(pos.highest_price - mult * bar.atr)

    raw_new_stop = max(stop_candidates)
    new_stop_loss = max(raw_new_stop, pos.prev_stop_loss)

    # ---- 评分卡 ----
    score = 0

    if regime.hs300_below_ma20 or regime.state == "bear":
        score += cfg.score_market_risk
        triggers.append("大盘弱势")

    if bar.close < bar.ma20:
        score += cfg.score_broken_ma20
        triggers.append("收盘跌破MA20")

    if bar.volume_ratio > 1.5 and abs(bar.day_change_pct) < 1.0:
        score += cfg.score_heavy_volume_stagnation
        triggers.append("放量滞涨")

    if bar.rsi > 80 and bar.recent_3d_high <= bar.prev_3d_high:
        score += cfg.score_overbought
        triggers.append("RSI超买+3天不新高")

    if pos.hold_days > cfg.time_stage_3_days and profit_pct < cfg.profit_trigger_1:
        score += cfg.score_long_hold
        triggers.append(f"持仓{pos.hold_days}天浮盈不足")

    high_profit = profit_pct >= cfg.profit_trigger_3
    if high_profit:
        score += cfg.score_high_profit
        triggers.append("浮盈超10%，建议锁利")

    # ---- 操作建议 ----
    if score >= cfg.score_sell_threshold:
        action = "建议卖出"
        priority = 3
        exec_time = ExecTime.NEXT_1450
    elif high_profit:
        action = "建议锁利(卖50%)"
        priority = 4
        exec_time = ExecTime.NEXT_1450
    elif pos.hold_days >= cfg.max_hold_days:
        action = "到期清仓"
        priority = 5
        exec_time = ExecTime.NEXT_1450
    else:
        return None  # 无需操作

    return SellSignal(
        action=action, priority=priority,
        reason=" | ".join(triggers) if triggers else action,
        exec_time=exec_time,
        new_stop_loss=round(new_stop_loss, 2),
        sell_score=score,
    )


def evaluate_portfolio(
    positions: List[Position],
    bars: dict[str, Bar],
    regime: Regime = None,
) -> List[SellSignal]:
    """批量评估持仓"""
    results = []
    for pos in positions:
        bar = bars.get(pos.code)
        if bar:
            sig = evaluate(pos, bar, regime)
            if sig:
                results.append(sig)
    return results
