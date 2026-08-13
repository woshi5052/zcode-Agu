"""
可转债成本模型 —— 独立配置，禁止复用股票参数

交易成本：
  - 佣金：沪市万0.4~1 / 深市万1~2（双向）
  - 印花税：无（股票有0.05%）
  - 无过户费、无经手费（包含在佣金中）
  - 买卖单位：沪市1手=10张=1000元面值, 深市最小10张=1000元面值
"""
from dataclasses import dataclass


@dataclass
class CBCostModel:
    """可转债成本模型"""
    commission_rate: float = 0.0001  # 万1 (取中值)
    min_commission: float = 0.0      # 最低佣金(转债通常免5)
    stamp_tax_rate: float = 0.0      # 无印花税
    slippage_rate: float = 0.0005    # 滑点万5

    def buy_cost(self, price: float, shares_or_zhang: int,
                 is_sh: bool = True) -> float:
        """买入成本
        Args:
            price: 每张价格（元）
            shares_or_zhang: 张数（深市）或折算后的张数（沪市10张=1手）
            is_sh: 是否上交所（沪市）
        """
        amount = price * shares_or_zhang
        commission = max(amount * self.commission_rate, self.min_commission)
        slippage = amount * self.slippage_rate
        return commission + slippage

    def sell_cost(self, price: float, shares_or_zhang: int,
                  is_sh: bool = True) -> float:
        """卖出成本（转债无印花税，与买入对称）"""
        return self.buy_cost(price, shares_or_zhang, is_sh)

    def round_shares(self, budget: float, price: float,
                     is_sh: bool = True) -> int:
        """根据预算计算可买张数（按面值100元/张）
        Args:
            is_sh: True=沪市(1手=10张), False=深市(最小10张)
        """
        max_zhang = int(budget / price)
        if is_sh:
            # 沪市: 必须是10的整数倍(1手=10张)
            return max(10, (max_zhang // 10) * 10)
        else:
            # 深市: 最小申报10张，可以10的整数倍
            return max(10, (max_zhang // 10) * 10)

    def amount_from_zhang(self, price: float, zhang: int) -> float:
        """张数 → 金额"""
        return price * zhang
