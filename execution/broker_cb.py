"""
可转债虚拟券商 —— 继承股票 Broker，覆盖:
  - 涨跌幅 ±20%（非股票±10%）
  - 成本模型（无印花税 + 万分之1佣金）
  - T+0 资金回转（引擎层处理，broker 不做限制）
  - 买卖单位（张，非股）
"""
from dataclasses import dataclass
from execution.broker import Broker, Order, OrderSide, Fill
from execution.cost_cb import CBCostModel


class CBBroker(Broker):
    """可转债券商，继承股票 Broker 的基类逻辑"""

    def __init__(self, cost_model: CBCostModel = None, mode: str = "backtest"):
        super().__init__(cost_model=cost_model or CBCostModel(), mode=mode)
        self.is_sh_cache: dict[str, bool] = {}  # 缓存沪市/深市

    def set_symbol_market(self, symbol: str, is_sh: bool):
        """标注转债的交易所（用于买卖单位计算）"""
        self.is_sh_cache[symbol] = is_sh

    def try_fill(self, order: Order, open_p: float, high_p: float,
                 low_p: float, close_p: float, prev_close: float,
                 volume: float, is_sh: bool = True) -> Fill | None:
        """尝试成交 — 可转债版

        与股票版差异：
        - 涨跌幅 ±20%（非 ±9.8%）
        - 无 T+1 限制（T+0 品种）
        - 手/张换算在调用方完成
        """
        if volume is None or volume <= 0:
            return None  # 停牌

        change_pct = (open_p - prev_close) / prev_close if prev_close else 0

        if order.side == OrderSide.BUY:
            if change_pct >= 0.198:  # ±20% 涨停
                return None
            price = open_p * (1 + self.cost.slippage_rate)
        else:
            if change_pct <= -0.198:  # -20% 跌停
                return None
            price = open_p * (1 - self.cost.slippage_rate)

        if order.shares <= 0:
            return None

        shares = order.shares
        fee = (self.cost.buy_cost(price, shares, is_sh) if order.side == OrderSide.BUY
               else self.cost.sell_cost(price, shares, is_sh))

        return Fill(symbol=order.symbol, price=price, shares=shares, fee=fee)
