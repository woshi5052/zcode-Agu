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
    """拉取全量K线 (AKShare东财)
    收盘前(15:30前)运行时, 剔除当日未完成K线, 只用完整交易日数据
    """
    import datetime
    now = datetime.datetime.now()
    is_intraday = now.hour < 15 or (now.hour == 15 and now.minute < 30)

    mode = "盘中-剔除当日K线" if is_intraday else "收盘后-含当日"
    print(f"拉取 {len(codes)} 支... ({mode})")
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
                # [修复] 盘中运行时剔除当日未完成K线
                if is_intraday and len(df) > 0 and df.index[-1].date() == now.date():
                    df = df.iloc[:-1]
                if len(df) > 50:
                    data[code] = df
        except Exception:
            pass
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(codes)}", flush=True)
        time.sleep(0.05)
    return data


def fundamental_filter(results: list) -> list:
    """
    基本面三关过滤 (AKShare PIT版 — 与回测验证配置一致, Actions可用)
    失败/缺失时放行, 不让连通性问题误杀推荐
    """
    try:
        from data.fundamental import pit_check
        import pandas as pd
        as_of = pd.Timestamp.now().normalize()
        kept = []
        for r in results:
            price = float(r.get("entry_price", 0) or r.get("current_price", 0))
            if price <= 0:
                kept.append(r)
                continue
            try:
                ok, reason = pit_check(r["code"], as_of, price)
            except Exception as e:
                print(f"  ⚠️ {r['name']}({r['code']}) 检查异常({str(e)[:40]}): 放行")
                kept.append(r)
                continue
            if ok:
                kept.append(r)
            else:
                print(f"  ❌ 过滤 {r['name']}({r['code']}): {reason}")
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
