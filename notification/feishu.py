"""
飞书推送 —— 支持 Webhook 和 Bot API 两种模式
Bot API: App ID + App Secret → 获取 tenant_access_token → 发送群消息
"""

import json
import time
import requests
from datetime import datetime

from config.settings import (
    FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_CHAT_ID,
    FEISHU_WEBHOOK, FEISHU_ENABLED,
)

# ============================================
# Token 管理 (Bot API 模式)
# ============================================

_token_cache: dict = {"token": None, "expires_at": 0}


def _get_tenant_token() -> str | None:
    """获取 tenant_access_token，自动缓存"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return None

    # 缓存未过期
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            _token_cache["token"] = data["tenant_access_token"]
            _token_cache["expires_at"] = time.time() + data.get("expire", 7200)
            print("[OK] 飞书 Token 获取成功")
            return _token_cache["token"]
        else:
            print(f"[ERROR] 飞书 Token 获取失败: {data}")
            return None
    except Exception as e:
        print(f"[ERROR] 飞书 Token 请求异常: {e}")
        return None


# ============================================
# 消息构建
# ============================================

def build_message(recommendations: list[dict], stats: dict = None,
                  diag: dict = None) -> str:
    """构建飞书文本消息"""
    import datetime
    bjt = datetime.datetime.utcnow() + datetime.timedelta(hours=8)

    lines = [
        "📊 A股量化日报",
        f"分析日期: {bjt.strftime('%Y-%m-%d %H:%M')} (北京) | 数据: AKShare",
        f"入选: {len(recommendations)}支 | v2.1 趋势+DeepSeek AI",
        "━━━━━━━━━━━━━━━━━━━",
    ]

    if recommendations:
        for i, r in enumerate(recommendations, 1):
            conf_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
            emoji = conf_emoji.get(r.get("confidence", "LOW"), "🔴")
            ai_tag = r.get("ai_sentiment", "")

            # 四要素格式
            hold = r.get("holding_days", 20)
            tp = r.get("target_price", 0)
            tp_pct = r.get("target_pct", 0)

            lines.append(
                f"\n{emoji} #{i} {r['name']} ({r['code']}) | {r['confidence']} 信心 {ai_tag}\n"
                f"  买入价格: ¥{r['entry_price']}\n"
                f"  卖出价格: ¥{tp} (+{tp_pct}%) 参考\n"
                f"  止损价格: ¥{r['stop_loss']} (-{r['stop_pct']}%)\n"
                f"  预判持有: {hold}天 | 止损方式: ATR移动止损\n"
                f"  信号: {'+'.join(r.get('signals', [])[:2])}"
            )
    else:
        lines.append("\n  📭 今日无符合条件的推荐")

    # 诊断信息 (排查空推荐用)
    if diag:
        lines.append("")
        market = diag.get("大盘状态", "?")
        if market == "弱势空仓":
            lines.append("🌧️ 大盘弱势(合成指数跌破MA20) → 今日空仓, 不推票")
        complete = diag.get("数据完整", "是")
        if complete != "是":
            lines.append(f"⚠️ 数据不完整({diag.get('数据支数','?')}支): 拉取限流, 结果仅供参考")
        lines.append(f"🔍 诊断: 数据{diag.get('数据支数','?')}支 | "
                     f"池子{diag.get('池子过滤后','?')}支 | "
                     f"候选{diag.get('策略候选','?')}支 → 最终{diag.get('最终推荐','?')}支")
        for rej in (diag.get("基本面拦截") or [])[:5]:
            lines.append(f"  🚫 {rej}")

    lines.append("━━━━━━━━━━━━━━━━━━━")

    if stats and stats.get("resolved", 0) > 0:
        lines.append(
            f"📈 历史统计: 已结算 {stats['resolved']}笔 | "
            f"胜率 {stats.get('wr', 0)}% | PF {stats.get('pf', 0)}"
        )
    else:
        lines.append("📈 历史统计: 暂无结算记录")

    lines.append("🤖 A-Share Quant Platform")

    return "\n".join(lines)


# ============================================
# 发送方式一：Webhook（简单但需预配置）
# ============================================

def _send_via_webhook(text: str) -> bool:
    """通过 Webhook 发送"""
    if not FEISHU_WEBHOOK:
        return False

    payload = {"msg_type": "text", "content": {"text": text}}
    try:
        resp = requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        result = resp.json()
        ok = result.get("code") == 0 or result.get("StatusCode") == 0
        return ok
    except Exception as e:
        print(f"[ERROR] Webhook 发送失败: {e}")
        return False


# ============================================
# 发送方式二：Bot API（App ID + Secret）
# ============================================

def _send_via_bot_api(text: str) -> bool:
    """通过 Bot API 发送到指定群聊"""
    token = _get_tenant_token()
    if not token:
        return False

    if not FEISHU_CHAT_ID:
        print("[ERROR] 未配置 FEISHU_CHAT_ID")
        return False

    payload = {
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "text",
        "content": json.dumps({"text": text}),
    }

    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return True
        else:
            print(f"[ERROR] Bot API 发送失败: {data}")
            # 常见错误提示
            if data.get("code") == 230001:
                print("  → 机器人不在群聊中，请先将机器人添加到群")
            elif data.get("code") == 999916:
                print("  → 机器人权限不足，需在飞书开放平台开启 im:message 权限")
            return False
    except Exception as e:
        print(f"[ERROR] Bot API 请求异常: {e}")
        return False


# ============================================
# 统一发送入口
# ============================================

def send_to_feishu(recommendations: list[dict], stats: dict = None,
                   diag: dict = None) -> bool:
    """
    发送消息到飞书
    优先级: Bot API > Webhook
    """
    if not FEISHU_ENABLED:
        print("[INFO] 飞书推送未启用")
        return False

    text = build_message(recommendations, stats, diag=diag)

    # 优先 Bot API
    if FEISHU_APP_ID and FEISHU_APP_SECRET:
        if _send_via_bot_api(text):
            print("[OK] 飞书 Bot API 推送成功")
            return True

    # 降级 Webhook
    if FEISHU_WEBHOOK:
        if _send_via_webhook(text):
            print("[OK] 飞书 Webhook 推送成功")
            return True

    print("[ERROR] 所有飞书推送方式均失败")
    return False
