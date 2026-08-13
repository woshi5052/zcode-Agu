"""
历史基本面数据 — point-in-time (PIT)
数据源: AKShare 东财接口 (免费)
关键: 按公告日期(NOTICE_DATE)对齐, 防止未来函数

缓存格式: data/fundamental_cache/{code}.csv
列: report_date, notice_date, eps, bps, debt_ratio, current_ratio, roe
"""
import os
import time
import pandas as pd

CACHE_DIR = os.path.join(os.path.dirname(__file__), "fundamental_cache")


def fetch_pit_fundamental(code: str, refresh: bool = False) -> pd.DataFrame | None:
    """
    拉取一只股票的历史财务指标 (含公告日期)
    Returns DataFrame 或 None
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_file = os.path.join(CACHE_DIR, f"{code}.csv")

    if not refresh and os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        if len(df) > 4:
            return df

    import akshare as ak

    # 1. 财务指标 (按报告期, 含 ROE/负债率/EPS/流动比率/每股净资产)
    for attempt in range(3):
        try:
            ind = ak.stock_financial_analysis_indicator(symbol=code, start_year="2019")
            break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2)

    if ind is None or len(ind) == 0:
        return None

    # 2. 资产负债表 (拿公告日期 NOTICE_DATE)
    try:
        # 东财格式: sh600519 / sz000001
        prefix = "sh" if code.startswith(("6", "9")) else "sz"
        bs = ak.stock_balance_sheet_by_report_em(symbol=f"{prefix.upper()}{code}")
        notice_map = dict(zip(bs["REPORT_DATE"], bs["NOTICE_DATE"]))
    except Exception:
        notice_map = {}

    # 3. 组装 (EPS/每股净资产用年报=静态口径; 负债率/流动比率用最新季报)
    ind["日期"] = pd.to_datetime(ind["日期"])
    rows = []
    # 年报行 (12-31) → eps/bps 基准
    annual = ind[ind["日期"].dt.month == 12].copy()
    annual_map = {}
    for _, r in annual.iterrows():
        year = r["日期"].year
        annual_map[year] = {
            "eps": r.get("摊薄每股收益(元)", None),
            "bps": r.get("每股净资产_调整前(元)", None),
        }

    for _, r in ind.iterrows():
        report_date = r["日期"]
        notice = notice_map.get(report_date, report_date + pd.Timedelta(days=45))
        year = report_date.year
        a = annual_map.get(year, {})
        rows.append({
            "report_date": report_date,
            "notice_date": pd.Timestamp(notice),
            # 静态口径: 用当年年报(即使未出, 占位None, PIT查询时回退上一年)
            "eps_annual": a.get("eps"),
            "bps_annual": a.get("bps"),
            "debt_ratio": r.get("资产负债率(%)", None),
            "current_ratio": r.get("流动比率", None),
            "roe": r.get("净资产收益率(%)", None),
        })

    df = pd.DataFrame(rows).sort_values("notice_date")
    df.to_csv(cache_file, index=False)
    return df


def get_pit_snapshot(code: str, as_of: pd.Timestamp) -> dict | None:
    """
    获取 as_of 日期时点已知的最新财务数据 (公告日期 ≤ as_of)
    - eps/bps: 最新已公告年报 (静态口径, 标准做法)
    - debt_ratio/current_ratio: 最新已公告季报
    """
    df = fetch_pit_fundamental(code)
    if df is None or len(df) == 0:
        return None

    df["notice_date"] = pd.to_datetime(df["notice_date"])
    known = df[df["notice_date"] <= as_of]
    if len(known) == 0:
        return None

    # 最新已公告年报的 EPS/BPS (从最近行往前找非空)
    eps = bps = None
    for _, r in known.iloc[::-1].iterrows():
        if eps is None and not pd.isna(r.get("eps_annual")):
            eps = r["eps_annual"]
        if bps is None and not pd.isna(r.get("bps_annual")):
            bps = r["bps_annual"]
        if eps is not None and bps is not None:
            break

    latest = known.iloc[-1]
    return {
        "eps": eps,
        "bps": bps,
        "debt_ratio": latest.get("debt_ratio"),
        "current_ratio": latest.get("current_ratio"),
        "roe": latest.get("roe"),
        "notice_date": latest["notice_date"],
    }


def pit_check(code: str, as_of: pd.Timestamp, price: float,
              max_debt=70, min_current=0.8, max_pe=80, max_pb=8) -> tuple[bool, str]:
    """
    PIT 时点三关过滤 (与 fundamental_filter.py 同阈值)
    Returns: (passed, reason)
    """
    snap = get_pit_snapshot(code, as_of)
    if snap is None:
        return True, "财务数据缺失(放行)"

    eps = snap["eps"]
    bps = snap["bps"]
    debt = snap["debt_ratio"]
    cur = snap["current_ratio"]

    # 关1: 风险诊断
    if debt is not None and not pd.isna(debt) and debt > max_debt:
        return False, f"负债率{debt:.0f}%"
    if cur is not None and not pd.isna(cur) and cur < min_current:
        return False, f"流动比率{cur:.2f}"

    # 关2: 估值 (用公告时点EPS/BPS算历史PE/PB)
    if eps is not None and not pd.isna(eps):
        if eps <= 0:
            return False, "亏损"
        pe = price / eps
        if pe > max_pe:
            return False, f"PE={pe:.0f}"
    if bps is not None and not pd.isna(bps):
        if bps > 0:
            pb = price / bps
            if pb > max_pb:
                return False, f"PB={pb:.0f}"

    return True, ""


if __name__ == "__main__":
    # 自检: 茅台 2023-06-30 时点的可见财报
    t = pd.Timestamp("2023-06-30")
    snap = get_pit_snapshot("600519", t)
    print(f"2023-06-30 时点可见财报:")
    for k, v in snap.items():
        print(f"  {k}: {v}")
    # 该时点Q1财报应已公告, 年报也应已公告
    ok, reason = pit_check("600519", t, 1700.0)
    print(f"过滤: {'✅通过' if ok else '❌' + reason}")
