"""
回撤熔断 — 三级风控
"""

from dataclasses import dataclass
from enum import Enum


class FuseState(Enum):
    NORMAL = "normal"          # 正常
    REDUCE_HALF = "reduce"     # 减半仓
    FLATTEN = "flatten"        # 清仓
    SHUTDOWN = "shutdown"      # 停机


@dataclass
class DrawdownFuse:
    equity_peak: float = 0.0
    current_equity: float = 0.0
    cooldown_days: int = 0      # 清仓冷却剩余交易日
    consecutive_losing_days: int = 0  # 连续亏损天数

    def update(self, current_equity: float, pnl: float = 0.0):
        """每日更新"""
        self.current_equity = current_equity
        if current_equity > self.equity_peak:
            self.equity_peak = current_equity

        # 冷却倒计时
        if self.cooldown_days > 0:
            self.cooldown_days -= 1

        # 连续亏损追踪
        if pnl < 0:
            self.consecutive_losing_days += 1
        else:
            self.consecutive_losing_days = 0

    @property
    def drawdown_pct(self) -> float:
        if self.equity_peak <= 0:
            return 0.0
        return round((self.current_equity - self.equity_peak) / self.equity_peak * 100, 2)

    def check(self) -> FuseState:
        """检查熔断状态"""
        if self.cooldown_days > 0:
            return FuseState.FLATTEN

        dd = abs(self.drawdown_pct)

        if dd >= 25:
            return FuseState.SHUTDOWN
        elif dd >= 15:
            self.cooldown_days = 5
            return FuseState.FLATTEN
        elif dd >= 10:
            return FuseState.REDUCE_HALF

        # 连续5天亏损触发保护
        if self.consecutive_losing_days >= 5:
            return FuseState.REDUCE_HALF

        return FuseState.NORMAL

    def get_max_positions(self) -> int:
        """根据熔断状态返回最大持仓数"""
        state = self.check()
        if state == FuseState.SHUTDOWN or state == FuseState.FLATTEN:
            return 0
        elif state == FuseState.REDUCE_HALF:
            return 2
        else:
            return 5

    def summary(self) -> dict:
        return {
            "drawdown_pct": self.drawdown_pct,
            "equity_peak": round(self.equity_peak, 2),
            "current": round(self.current_equity, 2),
            "fuse": self.check().value,
            "cooldown": self.cooldown_days,
            "losing_streak": self.consecutive_losing_days,
        }
