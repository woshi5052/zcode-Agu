"""
仓位管理
"""

from dataclasses import dataclass


@dataclass
class PositionLimits:
    max_positions: int = 5
    max_single_weight: float = 0.25
    min_cash_buffer: float = 0.15
    base_position_pct: float = 0.20

    def calc_shares(self, price: float, equity: float, score: float = 70) -> int:
        """根据价格和资金计算买入股数（A股100股整数倍）"""
        max_capital = equity * self.max_single_weight
        base_capital = equity * self.base_position_pct

        # 高分可上浮10%
        if score >= 80:
            base_capital *= 1.1

        capital = min(base_capital, max_capital)

        # 预留现金缓冲
        available = equity * (1 - self.min_cash_buffer)
        capital = min(capital, available)

        shares = int(capital / price / 100) * 100
        return max(shares, 100)  # 最少1手


from dataclasses import dataclass
