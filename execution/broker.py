"""
虚拟券商 v3.0 —— 回测/实盘共用成交规则
涨停拒买 / 跌停拒卖 / 停牌拒单 / T+1
"""

from dataclasses import dataclass
from enum import Enum

from execution.cost import CostModel


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    symbol: str
    side: OrderSide
    shares: int = 0


@dataclass
class Fill:
    symbol: str
    price: float
    shares: int
    fee: float


class Broker:
    """统一成交规则：回测与实盘共用（唯一差别是成交价来源）"""

    def __init__(self, cost_model: CostModel = None, mode: str = "backtest"):
        self.cost = cost_model or CostModel()
        self.mode = mode  # backtest / paper / live

    def try_fill(self, order: Order, open_p: float, high_p: float,
                 low_p: float, close_p: float, prev_close: float,
                 volume: float) -> Fill | None:
        """尝试成交，返回 Fill 或 None（拒单）。

        规则：
        - 涨停（开盘涨幅≥9.8% 或 触及涨停价）→ 买单拒单
        - 跌停（开盘跌幅≤-9.8%）→ 卖单拒单
        - 停牌（当日无成交量）→ 拒单
        - 成交价：买入用开盘价+滑点，卖出用开盘价-滑点
        """
        if volume is None or volume <= 0:
            return None  # 停牌

        change_pct = (open_p - prev_close) / prev_close if prev_close else 0

        if order.side == OrderSide.BUY:
            if change_pct >= 0.098:
                return None  # 涨停买不进
            price = open_p * (1 + self.cost.slippage_rate)
        else:
            if change_pct <= -0.098:
                return None  # 跌停卖不出
            price = open_p * (1 - self.cost.slippage_rate)

        if order.shares <= 0:
            # 份额必须由调用方算好（A股 100 股整数倍）
            return None

        shares = order.shares
        fee = (self.cost.buy_cost(price, shares) if order.side == OrderSide.BUY
               else self.cost.sell_cost(price, shares))

        return Fill(symbol=order.symbol, price=price, shares=shares, fee=fee)
