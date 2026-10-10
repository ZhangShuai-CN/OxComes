"""板块轮动计算：11 板块动量矩阵、当日涨跌、RS vs SPY、轮动象限。

数据周期约定：动量的时间尺度按可观测交易日层级近似（1d/5d≈1w/21d≈1m/
63d≈3m/126d≈6m/252d≈12m）；缺数据的窗口自动收紧。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

from app.data import store
from app.data.universe import SECTOR_ETFS, get_sector_color, get_sector_name

logger = logging.getLogger(__name__)

MOM_WINDOWS = {  # 窗口名 -> 交易日数
    "1d": 1, "1w": 5, "1m": 21, "3m": 63, "6m": 126, "12m": 252,
}


def _load_closes(tickers) -> pd.DataFrame:
    cols = {}
    for t in tickers:
        df = store.read_ohlcv(t)
        if df is not None:
            cols[t] = df["Close"]
    if not cols:
        return pd.DataFrame()
    px = pd.DataFrame(cols).ffill()
    return px[px.index <= pd.Timestamp.now().normalize()]


def rotation_snapshot(as_of: Optional[str] = None) -> Dict:
    """一次取齐 /sectors 页所需的全部数据（当日+动量矩阵+RS 曲线+列表元数据）。"""
    tickers = list(SECTOR_ETFS.keys())
    px = _load_closes(tickers + ["SPY"])
    if px.empty or len(px) < 40:
        return {"ok": False, "error": "板块/SPY 行情数据不足（先运行 cli.py sync）"}

    if as_of:
        px = px[px.index <= pd.to_datetime(as_of)]
    last_date = px.index[-1]

    spy = px["SPY"] if "SPY" in px.columns else None

    # 当日与前收盘
    out_sectors = []
    momentum_rows = {}
    for t in tickers:
        if t not in px.columns:
            continue
        s = px[t].dropna()
        if len(s) < 30:
            continue
        last = s.iloc[-1]
        chg = (last / s.iloc[-2] - 1) if len(s) >= 2 else 0.0
        mom = {}
        for label, w in MOM_WINDOWS.items():
            mom[label] = float(last / s.iloc[-(w + 1)] - 1) if len(s) > w else None
        out_sectors.append({
            "ticker": t,
            "name": get_sector_name(t),
            "color": get_sector_color(t),
            "close": round(float(last), 2),
            "change_pct": round(float(chg) * 100, 2),
            **{f"mom_{k}": (round(v * 100, 2) if v is not None else None)
               for k, v in mom.items()},
        })
        momentum_rows[t] = mom

    # RS vs SPY 归一化曲线（近 252 交易日）
    rs_lines = {}
    if spy is not None:
        spy_norm = spy / spy.iloc[0]
        for t in tickers:
            if t in px.columns:
                r = (px[t] / spy_norm).dropna().tail(252)
                rs_lines[t] = {"dates": [str(d.date()) for d in r.index],
                               "values": [round(float(v), 4) for v in r.values]}

    # 轮动象限（x=1m动量, y=12m-1m 动量差; 相对时代感 RRG 简化版）
    quadrant = []
    for t, mom in momentum_rows.items():
        m1 = mom.get("1m")
        m12 = mom.get("12m")
        if m1 is None or m12 is None:
            continue
        quadrant.append({"ticker": t, "name": get_sector_name(t),
                         "x": round(m1 * 100, 2), "y": round((m12 - m1) * 100, 2)})

    return {
        "ok": True,
        "as_of": str(last_date.date()),
        "sectors": out_sectors,
        "momentum_labels": list(MOM_WINDOWS.keys()),
        "rs_lines": rs_lines,
        "quadrant": quadrant,
        "note": ("板块行情为新浪未复权口径：相对动量/RS/轮动排序不受���响，"
                 "绝对点位略低于复权值（分红未计）。"),
    }


def save_daily_rows(snapshot: Dict) -> None:
    """把当日板块涨跌与动量写入 sector_daily 表（每日一次幂等）。"""
    from app import db
    if not snapshot.get("ok"):
        return
    rows = [(snapshot["as_of"], s["ticker"], s["close"], s["change_pct"],
             s.get("mom_1w"), s.get("mom_1m"), s.get("mom_3m"), s.get("mom_6m"),
             s.get("mom_12m"))
            for s in snapshot["sectors"]]
    db.executemany(
        "INSERT INTO sector_daily(date, sector, close, change_pct, mom_5d, mom_21d, "
        "mom_63d, mom_126d, mom_252d) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(date, sector) DO UPDATE SET close=excluded.close, "
        "change_pct=excluded.change_pct, mom_5d=excluded.mom_5d, "
        "mom_21d=excluded.mom_21d, mom_63d=excluded.mom_63d, "
        "mom_126d=excluded.mom_126d, mom_252d=excluded.mom_252d",
        rows)


def rotation_history(days: int = 252) -> "pd.DataFrame | None":
    """读 sector_daily 表的历史（回看轮动持续的均值回复现象）。"""
    from app import db
    rows = db.query_all(
        "SELECT date, sector, change_pct, mom_21d, mom_63d FROM sector_daily "
        "ORDER BY date DESC LIMIT ?", (days * 11,))
    if not rows:
        return None
    df = pd.DataFrame(rows)
    return df.pivot(index="date", columns="sector", values="change_pct").sort_index()
