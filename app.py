"""
A股量化平台 —— Streamlit 可视化面板
部署到 ModelScope Spaces 或本地运行: streamlit run app.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import REPORTS_DIR
from tracker.predictor import load_predictions, calc_stats

st.set_page_config(
    page_title="A股量化平台",
    page_icon="📊",
    layout="wide",
)

st.title("📊 A-Share Quant Platform")
st.caption(f"数据更新: {datetime.now().strftime('%Y-%m-%d %H:%M')} | 股票池: 沪深300")


# ================================================
# 加载数据
# ================================================

@st.cache_data(ttl=300)
def load_data():
    predictions = load_predictions()
    stats = calc_stats(predictions)

    recs = []
    rec_file = REPORTS_DIR / "recommendations.json"
    if rec_file.exists():
        try:
            with open(rec_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                recs = data.get("recommendations", [])
        except Exception:
            pass

    return predictions, stats, recs


predictions, stats, recommendations = load_data()

# ================================================
# 指标卡片
# ================================================

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.metric("总预测", stats.get("total", 0))
with col2:
    st.metric("已结算", stats.get("resolved", 0))
with col3:
    st.metric("胜率", f"{stats.get('wr', 0)}%")
with col4:
    st.metric("Profit Factor", stats.get("pf", 0))
with col5:
    st.metric("待结算", stats.get("pending", 0))

# ================================================
# 今日推荐
# ================================================

st.subheader("📈 今日推荐")

if recommendations:
    rec_df = pd.DataFrame(recommendations)
    cols_show = ["code", "name", "entry_price", "take_profit", "stop_loss",
                 "target_pct", "stop_pct", "rr_ratio", "score", "confidence",
                 "signals", "ai_sentiment"]
    cols_available = [c for c in cols_show if c in rec_df.columns]
    st.dataframe(
        rec_df[cols_available],
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("今日暂无推荐数据")

# ================================================
# 胜率曲线
# ================================================

st.subheader("📉 累计胜率走势")

if predictions:
    resolved = [p for p in predictions if p["status"] != "pending"]
    resolved.sort(key=lambda x: x.get("date", ""))

    if resolved:
        dates = []
        wr_cum = []
        wins = 0
        for i, p in enumerate(resolved, 1):
            dates.append(p.get("closed_date", p.get("date", "")))
            if p["status"] == "hit_target":
                wins += 1
            wr_cum.append(round(wins / i * 100, 1))

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates, y=wr_cum,
            mode="lines+markers",
            name="累计胜率",
            line=dict(color="#00C853", width=2),
        ))
        fig.add_hline(y=50, line_dash="dash", line_color="gray",
                      annotation_text="50%基准线")
        fig.update_layout(
            height=350,
            margin=dict(l=20, r=20, t=20, b=20),
            yaxis_title="胜率 %",
        )
        st.plotly_chart(fig, use_container_width=True)

# ================================================
# PnL 分布
# ================================================

st.subheader("💰 盈亏分布")

if predictions:
    resolved = [p for p in predictions if p["status"] != "pending" and p.get("pnl_pct") is not None]
    if resolved:
        pnl_data = [p["pnl_pct"] for p in resolved]
        labels = ["盈利" if x > 0 else "亏损" for x in pnl_data]
        colors = ["#00C853" if x > 0 else "#FF1744" for x in pnl_data]

        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=list(range(len(pnl_data))),
            y=pnl_data,
            marker_color=colors,
            text=[f"{x:.1f}%" for x in pnl_data],
            textposition="outside",
        ))
        fig2.add_hline(y=0, line_color="white")
        fig2.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="交易序号",
            yaxis_title="盈亏 %",
        )
        st.plotly_chart(fig2, use_container_width=True)

# ================================================
# 最近结算记录
# ================================================

st.subheader("📋 最近结算记录")

if predictions:
    resolved = [p for p in predictions if p["status"] != "pending"]
    resolved.sort(key=lambda x: x.get("closed_date", x.get("date", "")), reverse=True)

    if resolved:
        history_df = pd.DataFrame(resolved[:20])
        cols_show = ["date", "code", "name", "entry_price", "target_price",
                     "stop_loss", "pnl_pct", "status", "closed_date"]
        cols_available = [c for c in cols_show if c in history_df.columns]
        st.dataframe(
            history_df[cols_available],
            use_container_width=True,
            hide_index=True,
        )

st.caption("🤖 A-Share Quant Platform | Powered by ModelScope + GitHub Actions")
