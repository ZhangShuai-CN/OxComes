"""指数估值位置(2026-10-12 美股总览页):S&P500 PE(TTM)/EPS(TTM) 的当前值
与 10 年分位。对标买方报告"估值分位"分量的 percentile-of-history 口径。

数据:multpl.com 月度全史(1871 起,MULTPL_SPXPE/MULTPL_SPXEPS,本地 parquet)。
QQQ 侧指数级 PE/EPS 历史无大陆可达免费源(multpl 仅覆盖 S&P500;Invesco 官页
JS 盾)——不造数据,端点返回 note,接入真源后前端自动出卡。

口径注意:EPS(TTM) 长期趋势上行,"10 年分位"常年偏高(盈利新高=分位新高),
其含义是盈利水平的位置而非贵贱;判断贵贱看 PE 分位(EPS 稳态下≈价格分位)。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

from app.data import store

logger = logging.getLogger(__name__)

MONTHS_10Y = 120          # 10 年月度窗口


def _px_level_color(pct: Optional[float]) -> Dict:
    """分位→档位/色(与板块分化度同一套刻度语义:高=贵=警)。"""
    if pct is None:
        return {"level": None, "cls": ""}
    if pct >= 90:
        return {"level": "极高", "cls": "neg"}
    if pct >= 70:
        return {"level": "高", "cls": "warn"}
    if pct <= 30:
        return {"level": "低", "cls": "pos"}
    return {"level": "中", "cls": ""}


def _series_view(ticker: str) -> Optional[Dict]:
    df = store.read_ohlcv(ticker)
    if df is None or df.empty:
        return None
    s = df["Close"].dropna()
    win = s.tail(MONTHS_10Y)
    cur = float(s.iloc[-1])
    pct = float((win < cur).mean() * 100) if len(win) >= 60 else None
    return {
        "as_of": str(s.index[-1].date()),
        "value": round(cur, 2),
        "pctile_10y": round(pct, 1) if pct is not None else None,
        "range_10y": {"mean": round(float(win.mean()), 2),
                      "min": round(float(win.min()), 2),
                      "max": round(float(win.max()), 2)} if len(win) >= 60 else None,
        "range_full": f"{s.index[0].date()} → {s.index[-1].date()} ({len(s)} 期)",
    }


def valuation_snapshot() -> Dict:
    """SPY(S&P500)估值位置(PE/EPS/Shiller/ERP)+ QQQ 数据源披露。"""
    pe = _series_view("MULTPL_SPXPE")
    eps = _series_view("MULTPL_SPXEPS")
    out: Dict = {"ok": bool(pe or eps), "spy": None, "qqq": None}
    if pe:
        pe.update(_px_level_color(pe["pctile_10y"]))
        pe["name"] = "S&P500 PE (TTM)"
        out["spy"] = {"pe": pe}
    if eps:
        # EPS 同比(TTM 月度序列相对 12 个月前)
        s = store.read_ohlcv("MULTPL_SPXEPS")["Close"].dropna()
        yoy = None
        if len(s) > 12 and float(s.iloc[-13]) > 0:
            yoy = float(s.iloc[-1] / s.iloc[-13] - 1)
        eps.update({
            "name": "S&P500 EPS (TTM)",
            # EPS 分位高=盈利在高位(增长周期里常态),不染警示色;色随同比正负
            "level": None, "cls": "pos" if (yoy or 0) > 0 else "neg",
            "yoy": round(yoy * 100, 1) if yoy is not None else None,
        })
        out["spy"] = out.get("spy") or {}
        out["spy"]["eps"] = eps

    # Shiller PE(CAPE,2026-10-12 用户问「EPS 怎么衡量贵贱」加):价格 /
    # 通胀调整后 10 年均 EPS —— 抹平盈利周期对 E 项的扭曲,EPS 崩塌年(如
    # 2008)不会把 PE 假性推高。双分位口径:10 年 + 1871 起全史(Shiller 传统)。
    sh = _series_view("MULTPL_SHILLERPE")
    if sh:
        s_full = store.read_ohlcv("MULTPL_SHILLERPE")["Close"].dropna()
        pct_full = float((s_full < s_full.iloc[-1]).mean() * 100)
        c10 = _px_level_color(sh["pctile_10y"])
        sh.update({
            "name": "Shiller PE (CAPE)",
            "level": c10["level"], "cls": c10["cls"],
            "pctile_full": round(pct_full, 1),
            "range_full_note": f"全史 1871 起分位 {pct_full:.1f}%",
        })
        out["spy"] = out.get("spy") or {}
        out["spy"]["shiller"] = sh

    # ERP 股权风险溢价(同轮):盈利收益率 E/P(=1/PE TTM)− 10Y 国债收益率
    # (本地财政部 ^TNX 月末)——「EPS 的机会成本视角」:股票盈利须跑赢无风险
    # 利率才值得冒险。负值 = 债券反超股票。色档反转:分位高=便宜=绿,低=贵=红。
    tnx = store.read_ohlcv("^TNX")
    pe_s = store.read_ohlcv("MULTPL_SPXPE")
    if tnx is not None and pe_s is not None and not tnx.empty:
        pe_m = pe_s["Close"].dropna().groupby([pe_s.index.year, pe_s.index.month]).last()
        tnx_m = tnx["Close"].dropna().resample("M").last()
        idx = pd.MultiIndex.from_tuples(pe_m.index)
        pe_m = pd.Series(pe_m.values, index=pd.to_datetime(["%d-%02d-01" % t[:2] for t in pe_m.index]))
        joined = pd.DataFrame({"ep": 1.0 / pe_m, "r10": tnx_m / 100.0}).ffill().dropna()
        erp = (joined["ep"] - joined["r10"]).dropna()
        if len(erp) > 60:
            cur = float(erp.iloc[-1])
            w10 = erp.tail(MONTHS_10Y)
            pct10 = float((w10 < cur).mean() * 100)
            pctall = float((erp < cur).mean() * 100)
            if pct10 <= 10:
                lv, cls = "极贵", "neg"
            elif pct10 <= 30:
                lv, cls = "贵", "warn"
            elif pct10 >= 70:
                lv, cls = "便宜", "pos"
            else:
                lv, cls = "中性", ""
            out["spy"] = out.get("spy") or {}
            out["spy"]["erp"] = {
                "name": "股权风险溢价 ERP",
                "as_of": str(erp.index[-1].date()),
                "value": round(cur * 100, 2),          # 百分点(可为负)
                "pctile_10y": round(pct10, 1),
                "pctile_full": round(pctall, 1),
                "level": lv, "cls": cls,
                "range_full_note": f"{erp.index[0].date()} 起全史分位 {pctall:.1f}%",
            }

    out["qqq"] = {
        "available": False,
        "note": "QQQ(纳指100)指数级 PE/EPS 历史与当前值均无大陆可达免费源"
                "(multpl 仅覆盖 S&P500,Invesco 官页 JS 盾)——接入真源后此处自动出卡,不造数据。",
    }
    return out
