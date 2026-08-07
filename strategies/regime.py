"""
大盘环境识别 — 牛/熊/震荡判定
"""

from dataclasses import dataclass
import pandas as pd
from strategies.indicators import calc_ma, calc_rsi


@dataclass
class Regime:
    state: str          # "bull" / "bear" / "ranging"
    index_price: float
    ma20: float
    ma60: float
    rsi: float

    @property
    def can_open(self) -> bool:
        """是否允许开新仓"""
        return self.state != "bear"

    @property
    def max_positions(self) -> int:
        if self.state == "bull":
            return 5
        elif self.state == "ranging":
            return 2
        else:
            return 0

    @property
    def score_penalty(self) -> int:
        """评分门槛加成"""
        if self.state == "bull":
            return 0
        elif self.state == "ranging":
            return 5   # 震荡市评分门槛 +5
        else:
            return 999  # 熊市不开仓


def detect_regime(index_df: pd.DataFrame) -> Regime:
    """
    用沪深300日K判断大盘状态

    - 价格 > MA20 且 MA20 > MA60  → bull
    - 价格 < MA20 且 MA20 < MA60  → bear
    - 其余                          → ranging
    """
    if index_df is None or len(index_df) < 60:
        return Regime(state="ranging", index_price=0, ma20=0, ma60=0, rsi=0)

    close = index_df["close"]
    price = float(close.iloc[-1])

    ma20 = calc_ma(close, 20)
    ma60 = calc_ma(close, 60)
    rsi = calc_rsi(close, 14)

    ma20_val = float(ma20.iloc[-1])
    ma60_val = float(ma60.iloc[-1])
    rsi_val = float(rsi.iloc[-1])

    if price > ma20_val and ma20_val > ma60_val:
        state = "bull"
    elif price < ma20_val and ma20_val < ma60_val:
        state = "bear"
    else:
        state = "ranging"

    return Regime(
        state=state,
        index_price=price,
        ma20=ma20_val,
        ma60=ma60_val,
        rsi=rsi_val,
    )


def detect_regime_from_code(code: str = "000300") -> Regime:
    """从AKShare直接获取指数数据并判定"""
    try:
        import akshare as ak
        import pandas as pd

        symbol = "sh000300" if code == "000300" else f"sh{code}"
        df = ak.stock_zh_a_daily(symbol=symbol, adjust="qfq")
        if df is None or len(df) < 60:
            return Regime(state="ranging", index_price=0, ma20=0, ma60=0, rsi=0)

        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        return detect_regime(df)
    except Exception as e:
        print(f"[WARN] 大盘检测失败: {e}")
        return Regime(state="ranging", index_price=0, ma20=0, ma60=0, rsi=0)
