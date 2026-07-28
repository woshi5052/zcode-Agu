"""
全局配置 —— A股量化平台
"""

import os
from pathlib import Path

# ============================================
# 路径
# ============================================
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data" / "cache"
REPORTS_DIR = ROOT_DIR / "reports"
PARAMS_FILE = ROOT_DIR / "config" / "params.json"

# 确保目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ============================================
# 数据源
# ============================================
STOCK_POOL = "hs300"          # hs300 / zz500 / all
LOOKBACK_DAYS = 250           # 日K线回溯天数

# ============================================
# 策略参数（默认值，会被 params.json 覆盖）
# ============================================
DEFAULT_PARAMS = {
    "atr_period": 10,
    "st_multiplier": 3.0,
    "ma_short": 20,
    "ma_long": 60,
    "rsi_period": 14,
    "rsi_threshold": 35,
    "volume_ratio": 0.8,
    "pullback_threshold": 2.0,
    "breakout_days": 20,
    "atr_stop_multiplier": 2.5,
    "max_stop_pct": 5.0,
    "min_stop_pct": 1.5,
    "max_return_pct": 10.0,
    "rr_target": 1.5,
    "top_n": 5,
    "holding_days": 5,
    "min_avg_amount": 5_000_0000,
    "min_list_days": 60,
}

# ============================================
# 飞书推送
# ============================================

# Bot API 模式（推荐）—— App ID + Secret + 群ID
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
FEISHU_CHAT_ID = os.getenv("FEISHU_CHAT_ID", "")

# Webhook 模式（备用）—— 直接 POST
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")

FEISHU_ENABLED = bool(
    (FEISHU_APP_ID and FEISHU_APP_SECRET and FEISHU_CHAT_ID)
    or FEISHU_WEBHOOK
)

# ============================================
# AI增强 —— DeepSeek API
# ============================================
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
AI_ENABLED = bool(DEEPSEEK_API_KEY)

# ============================================
# GitHub Actions 环境检测
# ============================================
IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"
