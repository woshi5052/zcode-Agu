"""
成本模型 v3.0 —— 佣金 + 印花税 + 滑点
"""


class CostModel:
    def __init__(self, commission_rate: float = 0.00025,
                 stamp_tax_rate: float = 0.0005,
                 slippage_rate: float = 0.0005,
                 min_commission: float = 5.0):
        self.commission_rate = commission_rate
        self.stamp_tax_rate = stamp_tax_rate
        self.slippage_rate = slippage_rate
        self.min_commission = min_commission

    def buy_cost(self, price: float, shares: int) -> float:
        """买入成本 = 佣金(万2.5, 最低5元) + 滑点"""
        amount = price * shares
        commission = max(self.min_commission, amount * self.commission_rate)
        slippage = amount * self.slippage_rate
        return commission + slippage

    def sell_cost(self, price: float, shares: int) -> float:
        """卖出成本 = 佣金 + 印花税(万5) + 滑点"""
        amount = price * shares
        commission = max(self.min_commission, amount * self.commission_rate)
        stamp = amount * self.stamp_tax_rate
        slippage = amount * self.slippage_rate
        return commission + stamp + slippage

    def round_trip_cost_pct(self) -> float:
        """往返总成本比例（用于估算）"""
        return (self.commission_rate * 2 + self.stamp_tax_rate
                + self.slippage_rate * 2) * 100
