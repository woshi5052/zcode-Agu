# -*- coding: utf-8 -*-
"""
飞书执行单格式真数据演练 (2026-09-29)
目的: 15:30 GitHub Actions 正式推送前, 用真实数据全链路验证执行单渲染。
边界: 只构建消息并打印 —— 不发送飞书、不写 reports/*、不调用 check_predictions。
运行: cd /d/ashare-quant && python scripts/dryrun_feishu_format.py
"""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 相对路径按仓库根解析

import daily_push as dp
from notification.feishu import build_message


def main():
    with open("config/params.json", encoding="utf-8") as f:
        params = json.load(f)

    print("=" * 50)
    print("  [演练] 飞书执行单格式真数据验证 (不发送/不落盘)")
    print("=" * 50)

    # 1. 数据 (与 daily_push.main 完全同源)
    codes = dp.get_universe()[:50]
    print(f"  股票池: {len(codes)} 支 (修复后应为49)")
    names = dp.get_names_map(codes)
    data = dp.fetch_data(codes)
    print(f"获取: {len(data)}/{len(codes)} 支")
    if len(data) < 10:
        print("[ABORT] 数据不足")
        sys.exit(1)

    # 2. regime + 情绪 (只读)
    regime = dp.check_market_regime(data)
    senti_score, senti_level = None, "获取失败"
    try:
        from strategies.sentiment_cycle import get_market_sentiment
        senti = get_market_sentiment()
        senti_score, senti_level = senti.get("score"), senti.get("level")
        print(f"  市场情绪: {senti_score} ({senti_level})")
        if senti_score is not None and senti_score < 25 and regime == "sideways":
            regime = "bear"
            print("  情绪冰点: 震荡市推荐被压制(演练同样生效)")
    except Exception as e:
        print(f"  [WARN] 情绪获取失败: {str(e)[:50]}")

    # 3. 策略 → 过滤 (与 main 同序)
    filtered = dp.stock_pool_filter(data, names_map=names)
    results, fund_rejects, cooled = [], [], 0
    if regime != "bear":
        results = dp.run_trend_analysis(filtered, names_map=names,
                                        top_n=params.get("top_n", 5), params=params)
        before = len(results)
        results = [r for r in results
                   if float(r.get("entry_price", 0)) <= dp.MAX_AFFORDABLE_PRICE]
        results, fund_rejects = dp.fundamental_filter(results)
        results = dp.apply_fund_flow(results)
        # 推送冷却 (只读 predictions.json)
        try:
            import datetime as _dtp
            with open("reports/predictions.json", encoding="utf-8") as f:
                _preds = json.load(f)
            _cut = (_dtp.datetime.utcnow() + _dtp.timedelta(hours=8)
                    - _dtp.timedelta(days=3)).strftime("%Y-%m-%d")
            recent = {p["code"] for p in _preds if str(p.get("date", "")) >= _cut}
            cooled = len(results)
            results = [r for r in results if r.get("code") not in recent]
            cooled -= len(results)
            if cooled:
                print(f"  推送冷却: 剔除{cooled}支(近3日已推荐)")
        except Exception:
            pass
        if regime == "sideways":
            if results and results[0].get("fund_flag") == "OUT":
                print("  震荡市宁缺勿滥: 唯一候选主力净流出 → 空推")
                results = []
            else:
                results = results[:1]

    # [执行单] 新功能: 信号→可执行计划
    results = dp.build_trade_plans(results, data)

    # 4. 构建消息 (不发送)
    regime_label = {"bull": "☀️牛(正常推票)", "sideways": "🌤️震荡(推1支)", "bear": "🌧️熊(空仓)"}
    diag = {
        "数据支数": len(data),
        "池子过滤后": len(filtered),
        "策略候选": "0(空仓跳过)" if regime == "bear" else len(results),
        "基本面拦截": fund_rejects,
        "最终推荐": len(results),
        "大盘状态": regime_label.get(regime, regime),
        "数据完整": "是",
        "市场情绪": f"{senti_score}({senti_level})",
        "推送冷却": f"剔除{cooled}支" if cooled else "无",
        "震荡市宁缺勿滥": "见日志",
    }
    msg = build_message(results, None, diag=diag)

    print("\n" + "=" * 50)
    print("  [演练] 构建消息如下 (未发送):")
    print("=" * 50)
    print(msg)

    # 5. 结构自检 (真实字段下的执行单完整性)
    plans = [r for r in results if r.get("trade_plan")]
    print(f"\n[检查] 推荐 {len(results)} 支, 执行单 {len(plans)} 张 (AI情绪已跳过,演练不含ai_tag)")
    for r in results:
        tp = r.get("trade_plan")
        if tp:
            assert tp.get("tp1") and tp.get("stop_loss"), f"{r['name']} 执行单字段缺失: {tp}"
            if tp.get("mode") in ("split", "single"):
                assert tp.get("first_shares") and tp["first_shares"] % 100 == 0, \
                    f"{r['name']} 首仓股数异常: {tp.get('first_shares')}"
    print("[OK] 执行单结构校验通过 (真实字段)")


if __name__ == "__main__":
    main()
