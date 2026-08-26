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
    用北京时间判断: 15:30前运行时剔除当日未完成K线, 只用完整交易日数据
    (Actions服务器是UTC时区, 不能直接用服务器时间)
    """
    import datetime
    # 北京时间 = UTC+8 (Actions服务器时间转北京)
    bjt_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    is_intraday = bjt_now.hour < 15 or (bjt_now.hour == 15 and bjt_now.minute < 30)

    mode = "盘中-剔除当日K线" if is_intraday else "收盘后-含当日"
    print(f"拉取 {len(codes)} 支... ({mode}, 北京{bjt_now:%H:%M})")
    data = {}
    fail_count = 0
    for i, code in enumerate(codes):
        df = None
        # 重试2次 (AKShare偶发限流)
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_daily(symbol=_format_code(code), adjust="qfq")
                if df is not None and len(df) > 50:
                    break
            except Exception:
                df = None
                time.sleep(0.5)
        if df is None or len(df) <= 50:
            fail_count += 1
        else:
            df = df.rename(columns={
                "date": "date", "open": "open", "high": "high",
                "low": "low", "close": "close", "volume": "volume",
            })
            if "amount" not in df.columns:
                df["amount"] = df["close"] * df["volume"]
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            # [修复] 盘中运行(北京时间)时剔除当日未完成K线
            if (is_intraday and len(df) > 0
                    and df.index[-1].date() == bjt_now.date()):
                df = df.iloc[:-1]
            if len(df) > 50:
                data[code] = df
            else:
                fail_count += 1
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(codes)} 失败{fail_count}", flush=True)
        time.sleep(0.05)
    print(f"拉取完成: 成功{len(data)} 失败{fail_count}")
    return data


def fundamental_filter(results: list) -> list:
    """
    基本面三关过滤 (AKShare PIT版 — 与回测验证配置一致, Actions可用)
    失败/缺失时放行, 不让连通性问题误杀推荐
    """
    try:
        from data.fundamental import pit_check
        import pandas as pd
        import datetime
        # 北京时间 (Actions是UTC)
        as_of = (pd.Timestamp.utcnow() + pd.Timedelta(hours=8)).normalize()
        kept = []
        rejects = []
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
                rejects.append(f"{r['name']}({r['code']}): {reason}")
                print(f"  ❌ 过滤 {r['name']}({r['code']}): {reason}")

        print(f"  基本面过滤: {len(results)}→{len(kept)} 支")
        return kept, rejects
    except Exception as e:
        print(f"  [WARN] 基本面过滤不可用: {e} (跳过)")
        return results, []


def check_market_regime(data: dict) -> bool:
    """大盘 regime 检查 (真实沪深300指数 vs MA20, 回测同款逻辑)
    Returns: True=弱势(应空仓), False=正常
    优先真实指数(1次请求不受个股限流影响), 失败才退回合成指数
    """
    try:
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if idx is not None and len(idx) > 25:
            idx["ma20"] = idx["close"].rolling(20).mean()
            last = idx.iloc[-1]
            pct = (last["close"] / last["ma20"] - 1) * 100
            print(f"  大盘(沪深300): {last['close']:.0f} vs MA20({last['ma20']:.0f}) "
                  f"= {pct:+.1f}% [{last['date']}]")
            return float(last["close"]) < float(last["ma20"])
    except Exception as e:
        print(f"  [WARN] 真实指数获取失败: {str(e)[:60]}, 退回合成指数")

    # 兜底: 合成指数等权
    if len(data) < 10:
        return False
    closes = [df["close"] for df in data.values()]
    all_dates = sorted(set().union(*[c.index for c in closes]))
    synth = pd.DataFrame(index=all_dates)
    synth["close"] = sum(c.reindex(all_dates).ffill().fillna(0) for c in closes) / len(closes)
    ma20 = synth["close"].rolling(20).mean()
    if len(ma20.dropna()) < 1:
        return False
    last = synth["close"].iloc[-1]
    ma = ma20.iloc[-1]
    pct = (last / ma - 1) * 100
    print(f"  大盘(合成): {last:.2f} vs MA20({ma:.2f}) = {pct:+.1f}%")
    return last < ma


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

    # 数据完整性: <200支说明拉取被限流, 样本有偏, 结果仅供参考
    data_incomplete = len(data) < 200

    # 2. 大盘 regime 检查 (合成指数等权, 回测同款方法)
    market_weak = check_market_regime(data)

    # 3. 过滤 + 策略 (大盘弱势 → 空仓不推票)
    filtered = stock_pool_filter(data, names_map=names)
    if market_weak:
        results = []
        print("  大盘弱势(合成指数跌破MA20): 今日空仓")
    else:
        results = run_trend_analysis(filtered, names_map=names,
                                     top_n=params.get("top_n", 5), params=params)
    cand_count = len(results)
    print(f"策略候选: {cand_count} 支")

    # 4. 基本面三关过滤 [新]
    results, fund_rejects = fundamental_filter(results)

    # 5. AI 增强
    if results:
        results = enhance_with_sentiment(results)
        for r in results:
            print(f"  ✅ {r['name']} ¥{r['entry_price']} 评分{r.get('score','')}")

    # 6. 保存 + 推送
    save_recommendations(results)
    add_predictions(results)
    stats = check_predictions(data)

    # 诊断信息 (附带进消息, 便于排查空推荐)
    diag = {
        "数据支数": len(data),
        "池子过滤后": len(filtered),
        "策略候选": cand_count,
        "基本面拦截": fund_rejects,
        "最终推荐": len(results),
        "大盘状态": "弱势空仓" if market_weak else "正常",
        "数据完整": "否(限流,仅供参考)" if data_incomplete else "是",
    }
    send_to_feishu(results, stats, diag=diag)
    print("推送完成")


if __name__ == "__main__":
    main()
