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
    cards = {}
    series = {}
    for label, ticker, name in (("10Y", "^TNX", "10年期国债收益率"),
                                ("30Y", "^TYX", "30年期国债收益率"),
                                ("13W", "^IRX", "13周国库券利率")):
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
    if cards.get("10Y", {}).get("value") is not None and cards.get("13W", {}).get("value") is not None:
        spread = round(cards["10Y"]["value"] - cards["13W"]["value"], 2)
    return {"ok": True, "cards": cards, "series": series, "spread_10Y_13W": spread}


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

