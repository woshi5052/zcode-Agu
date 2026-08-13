"""
双低轮动 v0.1 — 月频轮动 + 强赎强制退出 + T+0资金回转

双低值 = 转债价格(元) + 转股溢价率(%)
选双低值最小的10只等权持仓, 每月底调仓
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

from data.cb_universe import get_cb_universe, get_cb_data, get_cb_info
from execution.cost_cb import CBCostModel

# ==========================================
# v0.1 参数
# ==========================================
N_HOLD = 5            # 持仓数 (1万/5=2000元/只, 可买≤200元转债)
MAX_PRICE = 130       # 价格上限 (避开强赎区)
MIN_PRICE = 70        # [v0.1] 价格下限 (避开30-60元违约债区)
MIN_DATA_DAYS = 60
TRADE_COST_RATE = 0.0006


@dataclass
class CBTrade:
    code: str
    name: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    holding_days: int


@dataclass
class CBResult:
    trades: list[CBTrade] = field(default_factory=list)
    equity_curve: pd.Series = None
    start_date: str = ""
    end_date: str = ""

    @property
    def total_trades(self): return len(self.trades)

    @property
    def win_rate(self):
        if not self.trades: return 0
        return round(sum(1 for t in self.trades if t.pnl_pct > 0) / len(self.trades) * 100, 1)

    @property
    def profit_factor(self):
        wins = [t.pnl_pct for t in self.trades if t.pnl_pct > 0]
        losses = [t.pnl_pct for t in self.trades if t.pnl_pct <= 0]
        tw = sum(wins)
        tl = abs(sum(losses))
        if tl == 0 and tw > 0:
            return None  # 全胜 → N/A (非0!)
        if tw == 0:
            return 0.0
        return round(tw / tl, 2)

    @property
    def avg_holding_days(self):
        if not self.trades: return 0
        return round(np.mean([t.holding_days for t in self.trades]), 1)

    def summary(self) -> dict:
        if not self.trades:
            return {"total_trades": 0, "win_rate": 0, "profit_factor": 0,
                    "annual_return": 0, "max_drawdown": 0, "avg_hold_days": 0,
                    "start": "", "end": ""}

        wins = [t for t in self.trades if t.pnl_pct > 0]
        losses = [t for t in self.trades if t.pnl_pct <= 0]
        days = (pd.Timestamp(self.end_date) - pd.Timestamp(self.start_date)).days
        if self.equity_curve is not None and len(self.equity_curve) > 1:
            final_val = self.equity_curve.iloc[-1]
            annual = round((final_val ** (365.0 / days) - 1) * 100, 2) if days > 1 else 0
            peak = self.equity_curve.expanding().max()
            maxdd = round(((self.equity_curve - peak) / peak).min() * 100, 2)
        else:
            annual = 0
            maxdd = 0

        return {
            "total_trades": len(self.trades),
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor if self.profit_factor is not None else "N/A",
            "annual_return": annual,
            "max_drawdown": maxdd,
            "avg_hold_days": self.avg_holding_days,
            "start": self.start_date,
            "end": self.end_date,
            "avg_win": round(np.mean([t.pnl_pct for t in wins]), 2) if wins else 0,
            "avg_loss": round(np.mean([t.pnl_pct for t in losses]), 2) if losses else 0,
        }


class DualLowEngine:
    """双低轮动引擎 v0.1"""

    def __init__(self, capital: float = 10000.0, n_hold: int = N_HOLD):
        self.capital = capital
        self.n_hold = n_hold
        self.cost = CBCostModel()

        # 运行状态
        self.cash = capital
        self.positions: dict[str, dict] = {}
        self.trades: list[CBTrade] = []
        self.equity_records: list[tuple] = []  # (date, total_value)

        # 强赎缓存 (避免重复API调用)
        self._redeem_cache: dict[str, dict] = {}

    # ==========================================
    # 数据接口
    # ==========================================

    def _get_dblow(self, code: str, date: pd.Timestamp) -> float | None:
        df = get_cb_data(code)
        if df is None:
            return None
        # [BUGFIX] date可能为非交易日, 用最近可用日期的数据
        available = df.index[df.index <= date]
        if len(available) == 0:
            return None
        actual_date = available[-1]
        row = df.loc[actual_date]
        price = row.get("收盘价", None)
        prem = row.get("转股溢价率", None)
        if pd.isna(price) or pd.isna(prem) or price <= 0:
            return None
        return float(price) + float(prem)

    def _get_price(self, code: str, date: pd.Timestamp) -> float | None:
        df = get_cb_data(code)
        if df is None:
            return None
        # [BUGFIX] date可能为非交易日, 用最近可用日期的数据
        available = df.index[df.index <= date]
        if len(available) == 0:
            return None
        p = df.loc[available[-1], "收盘价"]
        return float(p) if not pd.isna(p) else None

    # ==========================================
    # 强赎检测
    # ==========================================

    def _get_redeem_info(self, code: str) -> dict | None:
        """获取强赎信息 (带缓存)"""
        if code in self._redeem_cache:
            return self._redeem_cache[code]
        info = get_cb_info(code)
        if info is None:
            self._redeem_cache[code] = None
            return None
        redeem = {
            "is_redeem": info.get("is_redeem", False),
            "notice_date": info.get("redeem_notice_date"),
            "redeem_price": info.get("redeem_price"),
            "redeem_start_date": info.get("redeem_start_date"),
            "delist_date": info.get("delist_date"),
        }
        self._redeem_cache[code] = redeem
        return redeem

    def _check_redeem(self, code: str, date: pd.Timestamp) -> dict | None:
        """检查转债在 date 是否处于强赎窗口
        Returns: None(正常) 或 dict(需强制退出)
        """
        info = self._get_redeem_info(code)
        if info is None or not info["is_redeem"]:
            return None

        notice = info["notice_date"]
        if notice is None:
            return None

        if isinstance(notice, str):
            notice = pd.Timestamp(notice)

        # 公告日之后 → 强赎窗口
        if date >= notice:
            return {
                "reason": "强赎公告",
                "redeem_price": info["redeem_price"],
                "notice_date": notice,
            }
        return None

    # ==========================================
    # 排序选股
    # ==========================================

    def _rank(self, universe: list[str], date: pd.Timestamp) -> list[tuple]:
        """双低排序, 返回 [(code, dblow, price), ...]"""
        scored = []
        for code in universe:
            dblow = self._get_dblow(code, date)
            price = self._get_price(code, date)
            if dblow is None or price is None:
                continue
            if price >= MAX_PRICE:
                continue
            if price < MIN_PRICE:       # [v0.1] 避开违约债价格区间
                continue
            scored.append((code, dblow, price))
        scored.sort(key=lambda x: x[1])
        return scored

    # ==========================================
    # 月度调仓
    # ==========================================

    def rebalance(self, date: pd.Timestamp, universe: list[str]):
        """月度调仓: 卖不在top N的, 买新进top N的 (T+0资金回转)"""
        date_str = date.strftime("%Y-%m-%d")

        # 0. 强赎检查 — 持仓中若有进入强赎窗口的, 加入强制卖出清单
        force_sell = {}
        for code in list(self.positions.keys()):
            redeem = self._check_redeem(code, date)
            if redeem:
                force_sell[code] = redeem

        # 1. 排名
        ranked = self._rank(universe, date)
        target = set(c for c, _, _ in ranked[:self.n_hold])
        current = set(self.positions.keys())

        # 强赎标的强制加入卖出清单(即使还在top N)
        to_sell = (current - target) | set(force_sell.keys())

        # 2. 卖出
        for code in to_sell:
            if code not in self.positions:
                continue
            pos = self.positions[code]
            price = self._get_price(code, date)
            if price is None:
                continue

            # [P0-1] 强赎退出: 使用实际市场价格, 绕过涨跌停检查
            is_redeem = code in force_sell
            exit_reason = force_sell[code]["reason"] if is_redeem else "调仓卖出"

            # 按市价成交 (非 broker, 直接以收盘价执行)
            fill_price = price * (1 - self.cost.slippage_rate)
            fee = self.cost.sell_cost(fill_price, pos["zhang"], is_sh=True)
            proceeds = fill_price * pos["zhang"] - fee
            self.cash += proceeds

            pnl = (fill_price - pos["price"]) / pos["price"] * 100
            pnl -= TRADE_COST_RATE * 100  # 双边摩擦
            holding = (date - pd.Timestamp(pos["entry_date"])).days

            self.trades.append(CBTrade(
                code, code, pos["entry_date"], date_str,
                pos["price"], fill_price, exit_reason,
                round(pnl, 2), holding))
            del self.positions[code]

        # 3. 买入 (T+0: 卖出资金已回笼, 可直接用于买入)
        to_buy = target - set(self.positions.keys())
        if to_buy:
            budget_per = self.cash / max(len(to_buy), 1)

        for code in to_buy:
            price = self._get_price(code, date)
            if price is None:
                continue
            # 买入张数
            zhang = self.cost.round_shares(budget_per, price, is_sh=True)
            if zhang < 10:
                continue

            fill_price = price * (1 + self.cost.slippage_rate)
            fee = self.cost.buy_cost(fill_price, zhang, is_sh=True)
            cost_amount = fill_price * zhang + fee
            if cost_amount > self.cash:
                continue
            self.cash -= cost_amount
            self.positions[code] = {
                "zhang": zhang,
                "price": fill_price,
                "entry_date": date_str,
            }

    # ==========================================
    # 回测
    # ==========================================

    def run(self, start: str, end: str) -> CBResult:
        """运行回测, 返回 CBResult"""
        self._reset()

        start_dt = pd.Timestamp(start)
        end_dt = pd.Timestamp(end)
        self.start_date = start
        self.end_date = end

        # 生成每月最后一个交易日（用 universe 的日期范围）
        all_dates = set()
        for code in get_cb_universe():
            df = get_cb_data(code)
            if df is not None:
                all_dates.update(df.index)
        all_dates = sorted(all_dates)

        rebalance_dates = []
        cursor = start_dt
        while cursor <= end_dt:
            # 找本月最后一个有数据的交易日
            next_month = (cursor.replace(day=28) + pd.Timedelta(days=4)).replace(day=1)
            month_end = next_month - pd.Timedelta(days=1)
            # 从 month_end 往前找最近的交易日
            available = [d for d in all_dates if d <= month_end and d >= start_dt]
            if available and available[-1] >= cursor:
                actual_date = available[-1]
                if actual_date <= end_dt:
                    rebalance_dates.append(actual_date)
                    cursor = actual_date + pd.Timedelta(days=1)
                else:
                    break
            else:
                cursor = next_month

        print(f"  双低轮动: {start} → {end} | {len(rebalance_dates)}次调仓")

        for i, date in enumerate(rebalance_dates):
            universe = get_cb_universe(as_of=date.strftime("%Y-%m-%d"),
                                       min_days=MIN_DATA_DAYS)
            if len(universe) >= self.n_hold:
                self.rebalance(date, universe)

            # 记录净值
            pos_value = 0
            for code, pos in self.positions.items():
                price = self._get_price(code, date)
                if price:
                    pos_value += pos["zhang"] * price
            total = self.cash + pos_value
            self.equity_records.append((date, total))

            if (i + 1) % 12 == 0:
                print(f"    {i+1}/{len(rebalance_dates)} 交易{len(self.trades)}笔 "
                      f"净值{total/self.capital:.3f}")

        # 构建结果
        equity = pd.Series(
            [v / self.capital for _, v in self.equity_records],
            index=[d for d, _ in self.equity_records]
        )

        return CBResult(
            trades=self.trades,
            equity_curve=equity,
            start_date=start,
            end_date=end,
        )

    def _reset(self):
        self.cash = self.capital
        self.positions = {}
        self.trades = []
        self.equity_records = []


# ==========================================
# 快速自检
# ==========================================
if __name__ == "__main__":
    engine = DualLowEngine(capital=10000)
    result = engine.run("2024-01-01", "2024-12-31")
    s = result.summary()
    print(f"\n  自检结果:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"\n  强赎检测: 缓存{len(engine._redeem_cache)}支")
    redeem_trades = [t for t in result.trades if "强赎" in t.exit_reason]
    print(f"  强赎退出: {len(redeem_trades)}笔")
