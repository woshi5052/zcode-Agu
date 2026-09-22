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
from data.universe import get_universe, STOCK_NAMES
from strategies.filters import stock_pool_filter
from strategies.scoring import run_trend_analysis
from ai.sentiment import enhance_with_sentiment
from notification.feishu import send_to_feishu
from tracker.predictor import add_predictions, check_predictions, save_recommendations

# 资金约束: 1万本金, A股1手=100股, 最高可买股价
INITIAL_CAPITAL = 10000.0
MAX_AFFORDABLE_PRICE = INITIAL_CAPITAL / 100  # ¥100


def get_names_map(codes: list) -> dict:
    """股票代码→名称映射
    [修复 2026-09-22] 优先用内置静态表 (零网络依赖)——Actions 上
    stock_info_a_code_name 接口偶发 ConnectionReset, 消息里曾退化为纯代码显示
    """
    names = {c: STOCK_NAMES[c] for c in codes if c in STOCK_NAMES}
    missing = [c for c in codes if c not in names]
    if missing:
        print(f"  {len(missing)} 支不在静态名称表, 在线补齐...")
        try:
            info = ak.stock_info_a_code_name()
            m = dict(zip(info["code"].astype(str).str.zfill(6), info["name"]))
            for c in missing:
                names[c] = m.get(c, c)
        except Exception as e:
            print(f"  [WARN] 名称列表获取失败: {str(e)[:60]}, 未覆盖代码用代码显示")
            for c in missing:
                names[c] = c
    return names


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
        # [修复 2026-09-22] pandas 3.x 的 utcnow() 返回 tz-aware,
        # 与 naive 的公告日期比较会抛 Invalid comparison, 导致三关过滤全部"异常放行"
        as_of = (pd.Timestamp.utcnow() + pd.Timedelta(hours=8)).tz_localize(None).normalize()
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


def check_market_regime(data: dict) -> str:
    """大盘 regime 三态检查 (回测验证过的设计, 与 params.json 阈值一致)
    Returns: "bull" / "sideways" / "bear"
      bull:     指数 > MA20×1.02  → 正常推票(最多3支)
      sideways: MA20×0.98~1.02   → 震荡, 只推1支
      bear:     指数 < MA20×0.98  → 空仓
    优先真实沪深300指数, 失败才退回合成指数
    """
    try:
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if idx is not None and len(idx) > 25:
            idx["ma20"] = idx["close"].rolling(20).mean()
            last = idx.iloc[-1]
            ratio = float(last["close"]) / float(last["ma20"])
            pct = (ratio - 1) * 100
            state = "bull" if ratio > 1.02 else ("bear" if ratio < 0.98 else "sideways")
            print(f"  大盘(沪深300): {last['close']:.0f} vs MA20({last['ma20']:.0f}) "
                  f"= {pct:+.1f}% [{last['date']}] → {state}")
            return state
    except Exception as e:
        print(f"  [WARN] 真实指数获取失败: {str(e)[:60]}, 退回合成指数")

    # 兜底: 合成指数等权
    if len(data) < 10:
        return "sideways"
    closes = [df["close"] for df in data.values()]
    all_dates = sorted(set().union(*[c.index for c in closes]))
    synth = pd.DataFrame(index=all_dates)
    synth["close"] = sum(c.reindex(all_dates).ffill().fillna(0) for c in closes) / len(closes)
    ma20 = synth["close"].rolling(20).mean()
    if len(ma20.dropna()) < 1:
        return "sideways"
    ratio = float(synth["close"].iloc[-1]) / float(ma20.iloc[-1])
    state = "bull" if ratio > 1.02 else ("bear" if ratio < 0.98 else "sideways")
    print(f"  大盘(合成): {ratio:+.2f} vs MA20 → {state}")
    return state


def main():
    with open("config/params.json") as f:
        params = json.load(f)

    print(f"\n{'='*50}")
    print(f"  每日推荐 v2.0 (含基本面过滤+大盘过滤)")
    print(f"{'='*50}")

    # 1. 数据 — [修复 2026-09-22] 改用49支低价股池(与回测/模拟盘一致),
    # 此前扫沪深300导致"推的票从未被模拟盘验证", 两套体系平行脱节
    codes = get_universe()[:50]
    names = get_names_map(codes)
    data = fetch_data(codes)
    print(f"获取: {len(data)}/{len(codes)} 支 (低价股池)")

    if len(data) < 10:
        print("数据不足，跳过")
        return

    # 数据完整性: 49支池拉到<40支说明被限流, 样本有偏, 结果仅供参考
    data_incomplete = len(data) < 40

    # 2. 大盘 regime 三态检查 (回测验证设计: 牛3/震荡1/熊0)
    regime = check_market_regime(data)

    # 3. 过滤 + 策略
    # [修复 2026-09-22] 顺序改为: 策略出候选 → 资金约束 → 基本面过滤 → 再按regime截取,
    # 此前震荡市先截top1再过滤, top1买不起当天就空推荐 (09-22上午实际发生)
    filtered = stock_pool_filter(data, names_map=names)
    if regime == "bear":
        results = []
        fund_rejects = []
        print("  大盘bear(沪深300<MA20×0.98): 空仓, 跳过选股")
    else:
        results = run_trend_analysis(filtered, names_map=names,
                                     top_n=params.get("top_n", 5), params=params)
        # [资金约束] 1万本金, 1手(100股)成本必须买得起: 股价≤100元
        before = len(results)
        results = [r for r in results
                   if float(r.get("entry_price", 0)) <= MAX_AFFORDABLE_PRICE]
        if len(results) < before:
            print(f"  价格过滤(>¥{MAX_AFFORDABLE_PRICE}买不起1手): {before}→{len(results)}")
        # 基本面三关过滤
        results, fund_rejects = fundamental_filter(results)
        if regime == "sideways":
            results = results[:1]
            print("  大盘sideways(±2%区间): 过滤后取评分最高1支")
        else:
            print("  大盘bull(>MA20×1.02): 正常推票")
    cand_count = len(results)

    # 5. AI 增强 (记录真实状态)
    ai_used = False
    if results:
        from ai.deepseek_client import is_available as _ds_ok
        if _ds_ok():
            results = enhance_with_sentiment(results)
            # 只有真调了DeepSeek才算"在用" (error/disabled不算)
            ai_used = any(r.get("ai_source") == "DeepSeek" for r in results)
        else:
            print("  [INFO] AI情绪未启用 (无DEEPSEEK_API_KEY)")
        for r in results:
            print(f"  ✅ {r['name']} ¥{r['entry_price']} 评分{r.get('score','')}")

    # 6. 保存 + 推送
    save_recommendations(results)
    add_predictions(results)
    stats = check_predictions(data)

    # 诊断信息 (附带进消息, 便于排查空推荐)
    regime_label = {"bull": "☀️牛(正常推票)", "sideways": "🌤️震荡(推1支)", "bear": "🌧️熊(空仓)"}
    diag = {
        "数据支数": len(data),
        "池子过滤后": len(filtered),
        "策略候选": "跳过(空仓)" if regime == "bear" else cand_count,
        "基本面拦截": fund_rejects,
        "最终推荐": len(results),
        "大盘状态": regime_label.get(regime, regime),
        "数据完整": "否(限流,仅供参考)" if data_incomplete else "是",
    }
    ok = send_to_feishu(results, stats, diag=diag)

    # 推送结果落盘 (供远程排查: workflow 会 commit 此文件)
    import datetime as _dt
    push_log = {
        "time_bjt": (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S"),
        "feishu_ok": bool(ok),
        "final_count": len(results),
        "data_count": len(data),
        "regime": regime,
        "ai_used": bool(ai_used),
    }
    import json as _json
    with open("position/push_log.json", "w") as f:
        _json.dump(push_log, f, ensure_ascii=False, indent=2)

    if not ok:
        print("[ERROR] 飞书推送失败!")
        sys.exit(1)  # 让 workflow 步骤报红, 不再静默
    print("推送完成")


if __name__ == "__main__":
    main()
