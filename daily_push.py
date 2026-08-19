"""
每日推荐推送 v2.0 — 含基本面三关过滤
本地可测: python daily_push.py
GitHub Actions 每日 15:30 自动调用
"""
import sys, json, time
sys.path.insert(0, "." if not __file__.startswith(".") else __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

import pandas as pd
import akshare as ak

from data.akshare_fetcher import get_hs300_stocks, _format_code
from strategies.filters import stock_pool_filter
from strategies.scoring import run_trend_analysis
from ai.sentiment import enhance_with_sentiment
from notification.feishu import send_to_feishu
from tracker.predictor import add_predictions, check_predictions, save_recommendations


def fetch_data(codes: list, max_stocks: int = 300) -> dict:
    """拉取全量K线 (AKShare东财)"""
    print(f"拉取 {len(codes)} 支...")
    data = {}
    for i, code in enumerate(codes):
        try:
            df = ak.stock_zh_a_daily(symbol=_format_code(code), adjust="qfq")
            if df is not None and len(df) > 50:
                df = df.rename(columns={
                    "date": "date", "open": "open", "high": "high",
                    "low": "low", "close": "close", "volume": "volume",
                })
                if "amount" not in df.columns:
                    df["amount"] = df["close"] * df["volume"]
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                data[code] = df
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(codes)}")
        time.sleep(0.15)
    return data


def fundamental_filter(results: list) -> list:
    """基本面三关过滤 (通达信实时数据, 失败时放行)"""
    try:
        from strategies.fundamental_filter import FundamentalFilter
        ff = FundamentalFilter()
        kept = []
        for r in results:
            price = float(r.get("entry_price", 0) or r.get("current_price", 0))
            if price <= 0:
                kept.append(r)
                continue
            try:
                check = ff.check(r["code"], price)
            except Exception:
                kept.append(r)  # 单只失败放行
                continue
            if check.passed:
                kept.append(r)
            else:
                print(f"  ❌ 过滤 {r['name']}({r['code']}): {check.reject_reason}")
        print(f"  基本面过滤: {len(results)}→{len(kept)} 支")
        return kept
    except Exception as e:
        print(f"  [WARN] 基本面过滤不可用: {e} (跳过)")
        return results


def main():
    with open("config/params.json") as f:
        params = json.load(f)

    print(f"\n{'='*50}")
    print(f"  每日推荐 v2.0 (含基本面过滤)")
    print(f"{'='*50}")

    # 1. 数据
    stocks = get_hs300_stocks()
    codes = stocks["code"].tolist()
    names = dict(zip(stocks["code"], stocks["name"]))
    data = fetch_data(codes)
    print(f"获取: {len(data)} 支")

    if len(data) < 10:
        print("数据不足，跳过")
        return

    # 2. 过滤 + 策略
    filtered = stock_pool_filter(data, names_map=names)
    results = run_trend_analysis(filtered, names_map=names,
                                 top_n=params.get("top_n", 5), params=params)
    print(f"策略候选: {len(results)} 支")

    # 3. 基本面三关过滤 [新]
    results = fundamental_filter(results)

    # 4. AI 增强
    if results:
        results = enhance_with_sentiment(results)
        for r in results:
            print(f"  ✅ {r['name']} ¥{r['entry_price']} 评分{r.get('score','')}")

    # 5. 保存 + 推送
    save_recommendations(results)
    add_predictions(results)
    stats = check_predictions(data)
    send_to_feishu(results, stats)
    print("推送完成")


if __name__ == "__main__":
    main()
