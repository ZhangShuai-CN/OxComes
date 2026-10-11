"""宏观模块：美债收益率、VIX 恐惧分级（文案移植自老项目）。

无假数据铁律：数据拉不到 → 不返回/前端不显示，绝不给占位数值。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

from app.data import store

logger = logging.getLogger(__name__)


def _series(ticker: str, days: int) -> Optional[pd.Series]:
    df = store.read_ohlcv(ticker)
    if df is None or df.empty:
        return None
    s = df["Close"].dropna()
    return s.tail(days) if days else s


def _rate_source(ticker: str) -> Dict:
    """利率卡数据源徽标:展示最近一次实际提供数据的源名(真实源徽标)。
    无假数据兜底:rates_fallback 已下线,拉不到真源时前端不渲染。"""
    import json
    from app import db
    from app.data import sources
    raw = db.meta_get(f"source:{ticker}")
    if not raw:
        return {"source": "", "note": ""}
    try:
        provider = json.loads(raw).get("provider", "")
    except Exception:
        provider = ""
    name = (sources.PROVIDERS.get(provider) or {}).get("name", provider)
    return {"source": name, "note": ""}


def get_yields(days: int = 252) -> Dict:
    """美债三档卡(2Y/10Y/30Y)+ 2s10s 利差(2026-10-11 宏观页改版)。

    13W 卡已从页面退役(^IRX 仍保留在调度里,合成器融资成本口径不变)。"""
    cards = {}
    series = {}
    for label, ticker, name in (("2Y", "^UST2Y", "2年期国债收益率"),
                                ("10Y", "^TNX", "10年期国债收益率"),
                                ("30Y", "^TYX", "30年期国债收益率")):
        src_badge = _rate_source(ticker)
        s = _series(ticker, days)
        if s is None:
            continue
        chg_d = float(s.iloc[-1] - s.iloc[-2]) if len(s) > 1 else 0.0
        cards[label] = {
            "name": name,
            "value": round(float(s.iloc[-1]), 2),
            "chg": round(chg_d, 2),
            "as_of": str(s.index[-1].date()),
            **src_badge,
        }
        series[label] = {"dates": [str(d.date()) for d in s.index],
                         "values": [round(float(v), 2) for v in s.values]}
    spread = None
    if cards.get("10Y", {}).get("value") is not None and cards.get("2Y", {}).get("value") is not None:
        spread = round(cards["10Y"]["value"] - cards["2Y"]["value"], 2)
    return {"ok": True, "cards": cards, "series": series, "spread_10Y_2Y": spread}


# 宏观页走势图品种(2026-10-11):ticker 之外的图表定义。
# key → {label, ticker, unit, digits, code(比价/裂解公式)}
# 无假数据铁律:某品种本地无数据 = 该面板隐藏,不显示占位。
_PANELS: list[dict] = [
    {"key": "dxy", "label": "美元指数 DXY", "ticker": "^DXY", "unit": "", "digits": 2},
    {"key": "cl", "label": "WTI 原油", "ticker": "^CL", "unit": "$/桶", "digits": 2},
    {"key": "usdjpy", "label": "美元 / 日元", "ticker": "^USDJPY", "unit": "JPY", "digits": 2,
     "pair": ("USDJPY", 1.0)},
    {"key": "jpyusd", "label": "日元 / 美元", "ticker": "^USDJPY", "unit": "USD", "digits": 4,
     "pair": ("USDJPY", -1.0)},        # 倒数序列(同一数据源,展示另一市场方向)
    {"key": "usdcny", "label": "美元 / 人民币(在岸)", "ticker": "^USDCNY", "unit": "CNY", "digits": 4},
    {"key": "cnyusd", "label": "人民币(在岸) / 美元", "ticker": "^USDCNY", "unit": "USD", "digits": 4,
     "pair": ("USDCNY", -1.0)},
    {"key": "gold", "label": "COMEX 黄金", "ticker": "^GC", "unit": "$/盎司", "digits": 1},
    {"key": "btc", "label": "BTC / USD", "ticker": "BTC-USD", "unit": "$", "digits": 0},
]


def _panel_series(tk: str, days: int) -> Optional[pd.Series]:
    """面板序列:取 Close 列(BTC 是 Adj Close 也一致,币安无复权概念)。"""
    df = store.read_ohlcv(tk)
    if df is None or df.empty:
        return None
    col = "Close" if tk != "BTC-USD" else "Adj Close"
    s = df[col].dropna()
    return s.tail(days) if days else s


def get_macro_panels(days: int = 1000) -> Dict:
    """宏观页走势图序列(原油/裂解/美元指数/汇率/黄金/BTC)。

    裂解价差 = 3-2-1 裂解:3 桶原油 → 2 桶汽油 + 1 桶取暖油,即
        crack($/桶) = 42 × (2×RBOB + 1×HO) / 3 − WTI
    (RB/HO 以 $/加仑 报价,42 加仑/桶)。三序列按交易日交集对齐。
    汇率倒数序列(JPY/USD、人民币/USD)由同一源倒数生成,非独立数据。"""
    charts: Dict[str, dict] = {}
    for p in _PANELS:
        s = _panel_series(p["ticker"], days)
        if s is None or len(s) < 30:
            continue
        if p.get("pair") and p["pair"][1] < 0:      # 倒数方向
            s = 1.0 / s
        d = p["digits"]
        charts[p["key"]] = {
            "label": p["label"], "unit": p["unit"], "ticker": p["ticker"],
            "as_of": str(s.index[-1].date()),
            "last": round(float(s.iloc[-1]), d),
            "dates": [str(dt.date()) for dt in s.index],
            "values": [round(float(v), d) for v in s.values],
        }

    # 3-2-1 裂解价差(RB/HO/CL 三序列交集对齐)
    rb, ho, cl = (_panel_series(t, days) for t in ("^RB", "^HO", "^CL"))
    if rb is not None and ho is not None and cl is not None:
        df = pd.concat({"rb": rb, "ho": ho, "cl": cl}, axis=1).dropna()
        if len(df) >= 30:
            crack = 42.0 * (2.0 * df["rb"] + df["ho"]) / 3.0 - df["cl"]
            charts["crack"] = {
                "label": "原油 3-2-1 裂解价差", "unit": "$/桶", "ticker": "^RB+^HO−^CL",
                "as_of": str(crack.index[-1].date()),
                "last": round(float(crack.iloc[-1]), 2),
                "dates": [str(dt.date()) for dt in crack.index],
                "values": [round(float(v), 2) for v in crack.values],
                "note": "3 桶原油炼 2 桶汽油 + 1 桶取暖油的炼油毛利:"
                        "42 × (2×RBOB + HO) / 3 − WTI",
            }
    return {"ok": True, "charts": charts}


def get_vix() -> Dict:
    s = _series("^VIX", 252)
    if s is None:
        # 当日快照兜底：东财 push2 轻量接口
        v = _vix_snapshot_em()
        if v is None:
            return {"ok": False, "error": "^VIX 数据缺失（东财解封后 cli.py sync --tickers ^VIX 补历史）"}
        return {"ok": True, "value": v, "regime": vix_regime(v), "series": None,
                "note": "仅当日快照（历史待补）"}
    v = float(s.iloc[-1])
    return {
        "ok": True,
        "value": round(v, 2),
        "chg": round(v - float(s.iloc[-2]), 2) if len(s) > 1 else 0.0,
        "regime": vix_regime(v),
        "series": {"dates": [str(d.date()) for d in s.index],
                   "values": [round(float(x), 2) for x in s.values]},
    }


def vix_regime(v: float) -> Dict:
    """VIX 恐惧分级（阈值与文案移植自老项目 us_market_monitor）。"""
    if v >= 40:
        return {"level": 5, "label": "极度恐惧", "emoji": "🔴",
                "desc": "危机模式：历史上此区域常是长期买点，但接刀需纪律"}
    if v >= 30:
        return {"level": 4, "label": "高度恐慌", "emoji": "🟠",
                "desc": "大幅波动，趋势策略空仓期、定投 resumed 区间"}
    if v >= 20:
        return {"level": 3, "label": "偏紧张", "emoji": "🟡",
                "desc": "正常偏波动状态，留意关键信号位"}
    if v >= 15:
        return {"level": 2, "label": "中性偏稳", "emoji": "🟢",
                "desc": "市场平稳，动能策略的舒适区"}
    return {"level": 1, "label": "极度低波", "emoji": "🔵",
            "desc": "低波动掩护下的贪婪区间，谨防突发降波反弹后的挤压"}


def _vix_snapshot_em() -> Optional[float]:
    import requests
    try:
        r = requests.get("https://push2.eastmoney.com/api/qt/ulist.np/get",
                         params={"fltt": 2, "secids": "100.VIX", "fields": "f2,f3"},
                         timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        data = (r.json().get("data") or {}).get("diff") or {}
        for row in (data if isinstance(data, list) else data.values()):
            if str(row.get("f2", "")).replace(".", "", 1).replace("-", "", 1).isdigit():
                return float(row["f2"])
    except Exception as e:
        logger.warning("东财 VIX 快照失败: %s", e)
    return None

