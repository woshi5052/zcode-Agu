"""
基本面三关过滤 v1.0 — 相当于 WorkBuddy 专家的本地实现

关1 风险诊断: 财务健康 (资产负债率/流动比率/EPS正负)
关2 估值过滤: PE/PB 极端值否决
关3 资金确认: 股东人数下降(筹码集中) + 主力动向(可选)

数据源: eltdx (通达信) FinanceBatch
"""
from dataclasses import dataclass
from typing import Optional

from eltdx import TdxClient


@dataclass
class FundamentalResult:
    code: str
    passed: bool
    gates: dict      # 各关结果
    metrics: dict    # 关键指标
    reject_reason: str = ""


class FundamentalFilter:
    """基本面过滤 — 入场前最后一道关"""

    def __init__(self, client: TdxClient = None):
        self.client = client or TdxClient()
        self._cache: dict[str, dict] = {}

        # 阈值 (保守值, 后续可调)
        self.max_debt_ratio = 0.70      # 资产负债率上限 (银行股PB单独看)
        self.min_current_ratio = 0.8    # 流动比率下限
        self.max_pe = 80                # PE上限 (亏损股除外)
        self.min_pe = 0                 # PE下限 (负PE=亏损)
        self.max_pb = 8                 # PB上限
        self.min_holders = 5000         # 股东人数下限 (流动性)
        self.max_holders = 2000000      # 股东人数上限 (放宽: 大蓝筹正常)

    # ==========================================
    # 数据获取
    # ==========================================

    def _get_finance(self, code: str) -> Optional[dict]:
        """拉取财务数据 (带缓存)"""
        if code in self._cache:
            return self._cache[code]
        try:
            batch = self.client.get_finance_batch([code])
            if not batch.records:
                self._cache[code] = None
                return None
            rec = batch.records[0]
            # 单位换算: 通达信金额单位为"元"级别需确认, 这里保留原始值用于比率计算
            fin = {
                "eps": rec.eps_raw,                          # 每股收益
                "total_assets": rec.zong_zi_chan_raw_float,  # 总资产
                "current_assets": rec.liu_dong_zi_chan_raw_float,  # 流动资产
                "fixed_assets": rec.gu_ding_zi_chan_raw_float,
                "current_liab": rec.liu_dong_fu_zhai_raw_float,    # 流动负债
                "long_liab": rec.chang_qi_fu_zhai_raw_float,       # 长期负债
                "net_assets": rec.jing_zi_chan_raw_float,          # 净资产
                "revenue": rec.zhu_ying_shou_ru_raw_float,         # 主营收入
                "operating_profit": rec.ying_ye_li_run_raw_float,  # 营业利润
                "net_profit": rec.jing_li_run_raw_float,           # 净利润
                "bps": rec.mei_gu_jing_zi_chan_raw_float,          # 每股净资产
                "holders": rec.gu_dong_ren_shu_raw_float,          # 股东人数
                "ipo_date": rec.ipo_date,
                "industry": rec.industry_raw,
            }
            self._cache[code] = fin
            return fin
        except Exception:
            self._cache[code] = None
            return None

    # ==========================================
    # 三关检查
    # ==========================================

    def check(self, code: str, price: float) -> FundamentalResult:
        """执行三关过滤"""
        fin = self._get_finance(code)
        if fin is None:
            # [修复] 数据缺失 → 放行, 不让连通性问题误杀全部推荐
            return FundamentalResult(
                code=code, passed=True, gates={},
                metrics={}, reject_reason="数据缺失(放行)")

        gates = {}
        metrics = {}

        # ---- 关1: 风险诊断 (财务健康) ----
        total_liab = fin["current_liab"] + fin["long_liab"]
        # 银行/金融股负债字段为0(口径特殊), 负债率按缺失处理 → 用PB把关
        debt_ratio = (total_liab / fin["total_assets"]
                      if fin["total_assets"] > 0 and total_liab > 0 else None)
        current_ratio = (fin["current_assets"] / fin["current_liab"]
                         if fin["current_liab"] > 0 else None)
        eps_positive = fin["eps"] > 0

        metrics["debt_ratio"] = round(debt_ratio, 3) if debt_ratio else None
        metrics["current_ratio"] = round(current_ratio, 2) if current_ratio else None
        metrics["eps"] = round(fin["eps"], 3)
        metrics["holders"] = int(fin["holders"])
        metrics["net_profit"] = fin["net_profit"]

        # 负债率缺失(金融股) → 不因负债率否决, 由PB关把关
        risk_ok = True
        if debt_ratio is not None and debt_ratio > self.max_debt_ratio:
            risk_ok = False
        if current_ratio is not None and current_ratio < self.min_current_ratio:
            risk_ok = False
        gates["risk"] = risk_ok

        # ---- 关2: 估值过滤 ----
        pe = price / fin["eps"] if fin["eps"] > 0 else None
        pb = price / fin["bps"] if fin["bps"] > 0 else None
        metrics["pe"] = round(pe, 1) if pe else None
        metrics["pb"] = round(pb, 1) if pb else None

        # 亏损股(负EPS)直接否决 — 专家版"风险诊断"核心
        valuation_ok = False
        if eps_positive and pe is not None and pb is not None:
            valuation_ok = (self.min_pe < pe <= self.max_pe
                            and pb <= self.max_pb)
        gates["valuation"] = valuation_ok

        # ---- 关3: 资金确认 (简化版: 股东人数合理性) ----
        # 股东人数过少(<5000)可能流动性差; 过多(>200万)筹码过度分散
        holders_ok = self.min_holders <= fin["holders"] <= self.max_holders
        gates["holders"] = holders_ok

        # ---- 汇总 ----
        passed = risk_ok and valuation_ok and holders_ok

        reasons = []
        if not risk_ok:
            reasons.append(f"财务风险(负债率{debt_ratio:.0%})")
        if not valuation_ok:
            reasons.append(f"估值否决(PE={pe}, PB={pb})")
        if not holders_ok:
            reasons.append(f"筹码异常(股东{fin['holders']:.0f})")

        return FundamentalResult(
            code=code, passed=passed, gates=gates,
            metrics=metrics,
            reject_reason=" | ".join(reasons) if reasons else "")


# ==========================================
# 快速自检
# ==========================================
if __name__ == "__main__":
    f = FundamentalFilter()
    # 测试几个典型股票
    tests = [
        ("600519", "贵州茅台(优质)"),
        ("000630", "铜陵有色(中性)"),
        ("000001", "平安银行(金融)"),
    ]
    from eltdx import TdxClient
    c = TdxClient()
    print(f"{'代码':<8}{'名称':<14}{'通过':<6}{'PE':>8}{'PB':>8}{'负债率':>8}{'EPS':>8}")
    for code, name in tests:
        q = c.get_quote(code)
        price = q[0].last_price if q else 0
        r = f.check(code, price)
        m = r.metrics
        dr = m.get('debt_ratio')
        dr_str = f"{dr*100:.0f}%" if dr is not None else "N/A"
        print(f"{code:<8}{name:<14}{'✅' if r.passed else '❌':<6}"
              f"{str(m.get('pe')):>8}{str(m.get('pb')):>8}"
              f"{dr_str:>8}{m.get('eps',0):>8.2f}")
        if not r.passed:
            print(f"{'':<8}{'':<14}  拒绝: {r.reject_reason}")
