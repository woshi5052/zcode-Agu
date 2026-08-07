"""
虚拟券商 — 最小实现
v3.0: 回测与实盘共用
"""

from dataclasses import dataclass
from enum import Enum


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    symbol: str
    side: OrderSide
    shares: int
    limit_price: float = 0.0   # 0 = 市价


@dataclass
class Fill:
    symbol: str
    side: OrderSide
    shares: int
    price: float
    fee: float


class Broker:
    """虚拟券商"""

    def __init__(self, trade_cost: float = 0.003):
        self.trade_cost = trade_cost

    def try_fill(self, order: Order, bar_open: float, bar_high: float,
                 bar_low: float, bar_close: float, volume: float = 0) -> Fill | None:
        """
        尝试成交

        Args:
            order:     订单
            bar_open:  当日开盘价
            bar_high:  当日最高价
            bar_low:   当日最低价
            bar_close: 当日收盘价
            volume:    当日成交量 (0 = 停牌)

        Returns:
            Fill 或 None (拒单)
        """
        # 停牌拒单
        if volume <= 0:
            return None

        # 涨停拒买 (涨幅 ≥ 9.8%)
        is_limit_up = bar_low >= bar_open * 1.098
        if order.side == OrderSide.BUY and is_limit_up:
            return None

        # 跌停拒卖
        is_limit_down = bar_high <= bar_open * 0.902
        if order.side == OrderSide.SELL and is_limit_down:
            return None

        # 成交价 (回测用开盘价)
        fill_price = bar_open if order.limit_price == 0 else min(
            order.limit_price, bar_open) if order.side == OrderSide.BUY else max(
            order.limit_price, bar_open)

        fee = fill_price * order.shares * self.trade_cost

        return Fill(
            symbol=order.symbol,
            side=order.side,
            shares=order.shares,
            price=fill_price,
            fee=round(fee, 2),
        )
