"""
预测追踪系统 —— 记录推荐、追踪止盈止损命中
"""

import json
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

from config.settings import REPORTS_DIR


PREDICTIONS_FILE = REPORTS_DIR / "predictions.json"
RECOMMENDATIONS_FILE = REPORTS_DIR / "recommendations.json"


def load_predictions() -> list[dict]:
    """加载历史预测记录"""
    if not PREDICTIONS_FILE.exists():
        return []
    try:
        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_predictions(predictions: list[dict]):
    """保存预测记录"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PREDICTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(predictions, f, ensure_ascii=False, indent=2)


def save_recommendations(recommendations: list[dict]):
    """保存当日推荐"""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "generated_at": datetime.now().isoformat(),
        "count": len(recommendations),
        "recommendations": recommendations,
    }
    with open(RECOMMENDATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_predictions(recommendations: list[dict], holding_days: int = 5):
    """将新推荐追加到预测记录"""
    predictions = load_predictions()
    today = datetime.now().strftime("%Y-%m-%d")

    for r in recommendations:
        pred = {
            "id": f"{r['code']}_{today}",
            "date": today,
            "code": r["code"],
            "name": r.get("name", ""),
            "entry_price": r["entry_price"],
            "stop_loss": r["stop_loss"],
            "stop_pct": r.get("stop_pct", 0),
            "confidence": r.get("confidence", "LOW"),
            "signals": r.get("signals", []),
            "status": "pending",
            "pnl_pct": None,
            "closed_date": None,
        }
        predictions.append(pred)

    save_predictions(predictions)
    print(f"  记录新预测: {len(recommendations)} 笔")
    return predictions


def check_predictions(data_dict: dict) -> dict:
    """
    检查所有 pending 预测是否命中止盈/止损
    返回统计摘要
    """
    predictions = load_predictions()
    today = datetime.now().strftime("%Y-%m-%d")
    updated = False

    for pred in predictions:
        if pred["status"] != "pending":
            continue

        code = pred["code"]
        if code not in data_dict:
            continue

        df = data_dict[code]
        if df is None or len(df) < 1:
            continue

        current_price = df["close"].iloc[-1]

        # 检查止损（v2：移动止损，这里用初始止损近似）
        if current_price <= pred["stop_loss"]:
            pred["status"] = "hit_stop"
            pred["pnl_pct"] = round(
                (pred["stop_loss"] - pred["entry_price"]) / pred["entry_price"] * 100, 2
            )
            pred["closed_date"] = today
            updated = True

        # 检查是否超过持仓天数（到期平仓）
        else:
            try:
                entry_date = datetime.strptime(pred["date"], "%Y-%m-%d")
                max_hold = 20  # v2 最大持仓
                if datetime.now() - entry_date > timedelta(days=max_hold):
                    pred["status"] = "expired"
                    pred["pnl_pct"] = round(
                        (current_price - pred["entry_price"]) / pred["entry_price"] * 100, 2
                    )
                    pred["closed_date"] = today
                    updated = True
            except Exception:
                pass

    if updated:
        save_predictions(predictions)
        print(f"  预测状态已更新")

    return calc_stats(predictions)


def calc_stats(predictions: list[dict]) -> dict:
    """计算统计数据"""
    resolved = [p for p in predictions if p["status"] != "pending"]
    wins = [p for p in resolved if p.get("pnl_pct", 0) > 0]
    losses = [p for p in resolved if p.get("pnl_pct", 0) <= 0]

    total_win = sum(p.get("pnl_pct", 0) for p in wins) if wins else 0
    total_loss = abs(sum(p.get("pnl_pct", 0) for p in losses)) if losses else 0

    return {
        "total": len(predictions),
        "resolved": len(resolved),
        "pending": len(predictions) - len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "wr": round(len(wins) / len(resolved) * 100, 1) if resolved else 0,
        "avg_win": round(total_win / len(wins), 2) if wins else 0,
        "avg_loss": round(total_loss / len(losses), 2) if losses else 0,
        "pf": round(total_win / total_loss, 2) if total_loss > 0 else 0,
    }
