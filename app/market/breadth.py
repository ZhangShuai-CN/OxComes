"""市场宽度（大陆环境的最小可行代理方案）。

主用 RSP(等权 S&P500) / SPY(市值加权) 比值作发散度代理：
    比值↑ = 等权跑赢 = 广度改善（健康的普涨）；比值↓ = 头部集中（窄基行情）。
逐股 advance/decline 统计（老方案）需要 ~500 只成分股批量日线，在当前
大陆数据源频控下不可行 —— 代理数据对轮动/情绪判断已足够灵敏。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

from app.data import store

logger = logging.getLogger(__name__)


def get_breadth(days: int = 252) -> Dict:
    rsp_df = store.read_ohlcv("RSP")
    spy_df = store.read_ohlcv("SPY")
    if rsp_df is None or spy_df is None:
        return {"ok": False, "error": "RSP/SPY 数据缺失（先 cli.py sync --tickers RSP,SPY）"}
    joined = pd.DataFrame({"RSP": rsp_df["Close"], "SPY": spy_df["Close"]}).ffill().dropna()
    if len(joined) < 60:
        return {"ok": False, "error": f"RSP/SPY 交集样本不足: {len(joined)}"}
    ratio = (joined["RSP"] / joined["SPY"]).tail(days)
    last = ratio.iloc[-1]
    chg_5d = float(last / ratio.iloc[-6] - 1) if len(ratio) > 6 else None
    chg_21d = float(last / ratio.iloc[-22] - 1) if len(ratio) > 22 else None
    pctile = float((ratio < last).mean() * 100)

    if chg_21d is None:
        state, state_desc = "unknown", "样本不足"
    elif chg_21d > 0.01:
        state, state_desc = "improving", "等权跑赢：广度改善（普涨行情）"
    elif chg_21d < -0.01:
        state, state_desc = "narrowing", "市值加权领涨：行情向头部集中（窄基）"
    else:
        state, state_desc = "neutral", "广度持平"

    return {
        "ok": True,
        "proxy": "RSP/SPY 等权-市值加权比",
        "as_of": str(ratio.index[-1].date()),
        "ratio": round(float(last), 4),
        "chg_5d": round(chg_5d * 100, 2) if chg_5d is not None else None,
        "chg_21d": round(chg_21d * 100, 2) if chg_21d is not None else None,
        "pctile_52w": round(pctile, 1),
        "state": state,
        "state_desc": state_desc,
        "series": {"dates": [str(d.date()) for d in ratio.index],
                   "values": [round(float(v), 4) for v in ratio.values]},
    }
