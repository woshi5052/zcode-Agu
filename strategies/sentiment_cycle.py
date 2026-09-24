"""
市场情绪周期指标 v1.0 (2026-09-24)
数据源: AKShare 涨停/炸板/跌停池 (仅保留近30个交易日 → 30天滚动窗口基线)
情绪分 0-100: 涨停家数↑ / 连板高度↑ / 跌停家数↓ / 炸板率↓ 的百分位合成
用法:
  from strategies.sentiment_cycle import get_market_sentiment
  s = get_market_sentiment()  # {"score": 0-100, "level": "冰点/降温/中性/升温/过热", "detail": {...}}
历史归档: reports/sentiment_history.json (每日自动累积, 窗口越滚越厚)
"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import akshare as ak

HISTORY_FILE = os.path.join("reports", "sentiment_history.json")
WINDOW = 30  # 池子接口只保留近30个交易日


def _fetch_day(date_s: str) -> dict | None:
    """拉某日三池指标, 非交易日/无数据返回 None"""
    try:
        zt = ak.stock_zt_pool_em(date=date_s)
        n_zt = 0 if zt is None or zt.empty else len(zt)
        if n_zt == 0:
            return None  # 休市或无数据
        max_lb = int(zt["连板数"].max()) if "连板数" in zt.columns else 0
        n_dt = 0
        try:
            dt = ak.stock_zt_pool_dtgc_em(date=date_s)
            n_dt = 0 if dt is None or dt.empty else len(dt)
        except Exception:
            pass
        n_zb, zb_rate = 0, None
        try:
            zb = ak.stock_zt_pool_zbgc_em(date=date_s)
            n_zb = 0 if zb is None or zb.empty else len(zb)
            if n_zt + n_zb > 0:
                zb_rate = n_zb / (n_zt + n_zb)
        except Exception:
            pass
        return {"date": date_s, "zt": n_zt, "dt": n_dt, "zb": n_zb,
                "max_lb": max_lb, "zb_rate": round(zb_rate, 3) if zb_rate is not None else None}
    except Exception:
        return None


def _load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_history(history: list):
    os.makedirs("reports", exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)


def build_history(max_calendar_days: int = 45) -> list:
    """回溯最近 W 窗口交易日 (从今天往前找), 增量归档"""
    history = _load_history()
    have = {h["date"] for h in history}
    bjt = pd.Timestamp.utcnow() + pd.Timedelta(hours=8)
    fetched = 0
    for offset in range(0, max_calendar_days):
        if fetched >= WINDOW:
            break
        d = (bjt - pd.Timedelta(days=offset)).strftime("%Y%m%d")
        if d in have:
            fetched += 1
            continue
        m = _fetch_day(d)
        if m:
            history.append(m)
            fetched += 1
        time.sleep(0.3)
    history.sort(key=lambda x: x["date"])
    _save_history(history)
    return history


def _pct_rank(val: float, series: list, higher_better: bool) -> float:
    """val 在 series 中的百分位 (0-1)"""
    if not series:
        return 0.5
    below = sum(1 for x in series if x < val)
    eq = sum(1 for x in series if x == val)
    r = (below + eq * 0.5) / len(series)
    return r if higher_better else 1 - r


def get_market_sentiment() -> dict:
    """主入口: 返回今日情绪分 (0-100) 与分档"""
    history = build_history()
    if len(history) < 5:
        return {"score": None, "level": "数据不足", "detail": {"days": len(history)}}
    today = history[-1]
    base = history[:-1] if history[-1]["date"] == max(h["date"] for h in history) else history
    zts = [h["zt"] for h in base]
    lbs = [h["max_lb"] for h in base]
    dts = [h["dt"] for h in base]
    parts = [
        _pct_rank(today["zt"], zts, True) * 0.4,
        _pct_rank(today["max_lb"], lbs, True) * 0.25,
        _pct_rank(today["dt"], dts, False) * 0.25,
    ]
    if today.get("zb_rate") is not None:
        zbs = [h["zb_rate"] for h in base if h.get("zb_rate") is not None]
        if len(zbs) >= 5:
            parts.append(_pct_rank(today["zb_rate"], zbs, False) * 0.1)
    w_sum = (0.4 if len(parts) > 0 else 0) + (0.25 if len(parts) > 1 else 0) + \
            (0.25 if len(parts) > 2 else 0) + (0.1 if len(parts) > 3 else 0)
    score = round(sum(parts) / w_sum * 100) if w_sum else None
    level = ("冰点" if score < 25 else "降温" if score < 45 else
             "中性" if score < 60 else "升温" if score < 75 else "过热")
    return {"score": score, "level": level, "detail": today,
            "window_days": len(history)}


if __name__ == "__main__":
    print(json.dumps(get_market_sentiment(), ensure_ascii=False, indent=1))
