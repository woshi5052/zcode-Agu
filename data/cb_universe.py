"""
可转债 Point-in-Time 股票池
—— 防生存偏差：历史上已强赎/退市的转债必须包含在当年池子里
"""
import os
import pandas as pd
from pathlib import Path

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cb_cache")

# 需剔除的转债（北京所定转，无溢价率数据）
EXCLUDE_PREFIXES = ("81",)  # 810xxx 系列


def get_cb_universe(as_of: str = None, min_days: int = 60) -> list[str]:
    """
    获取 as_of 日期存在的可转债池。
    
    Args:
        as_of: 截止日期 'YYYY-MM-DD'，None=全部
        min_days: 最低数据天数要求
    
    Returns:
        转债代码列表
    """
    if not os.path.exists(CACHE_DIR):
        return []

    codes = []
    for f in os.listdir(CACHE_DIR):
        if not f.endswith(".csv"):
            continue
        code = f.replace(".csv", "")
        # 剔除北京所定转
        if code.startswith(EXCLUDE_PREFIXES):
            continue
        codes.append(code)

    if as_of is None:
        return sorted(codes)

    # Point-in-time 过滤: 转债在 as_of 时已上市且未退市
    valid = []
    as_of_dt = pd.Timestamp(as_of)
    for code in codes:
        fpath = os.path.join(CACHE_DIR, f"{code}.csv")
        try:
            df = pd.read_csv(fpath, parse_dates=["日期"])
            if len(df) < min_days:
                continue
            first_date = df["日期"].min()
            last_date = df["日期"].max()
            if first_date <= as_of_dt <= last_date:
                valid.append(code)
        except:
            continue
    return sorted(valid)


def get_cb_info(code: str) -> dict | None:
    """获取单只转债的基本信息（含强赎数据）"""
    try:
        import akshare as ak
        info = ak.bond_zh_cov_info(symbol=code)
        if info is None or len(info) == 0:
            return None
        row = info.iloc[0]
        return {
            "code": code,
            "name": row.get("SECURITY_NAME_ABBR", ""),
            "list_date": row.get("LISTING_DATE"),
            "delist_date": row.get("DELIST_DATE"),
            "is_redeem": row.get("IS_REDEEM") == "是",
            "redeem_notice_date": row.get("NOTICE_DATE_SH"),
            "redeem_price": row.get("EXECUTE_PRICE_SH"),
            "redeem_start_date": row.get("EXECUTE_START_DATESH"),
            "maturity_date": row.get("EXPIRE_DATE"),
            "convert_price": row.get("CONVERT_STOCK_PRICE"),
            "stock_code": row.get("CONVERT_STOCK_CODE"),
        }
    except:
        return None


def get_cb_data(code: str) -> pd.DataFrame | None:
    """获取单只转债的历史溢价率数据"""
    fpath = os.path.join(CACHE_DIR, f"{code}.csv")
    if not os.path.exists(fpath):
        return None
    try:
        df = pd.read_csv(fpath, parse_dates=["日期"], index_col="日期")
        if "转股溢价率" not in df.columns:
            return None
        return df.sort_index()
    except:
        return None
