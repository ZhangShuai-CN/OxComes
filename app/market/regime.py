"""市场体制与位置指标(2026-10-12 美股总览页)。

对标美银 Bull&Bear / 高盛 FCI 的**分量级公开可复现版**:它们是多变量加权
合成的专有模型(CTA 配置/信贷利差等数据不公开),但每个分量内部的标准做法
=「当前值在过去 N 年经验分布中的分位」。我们按同一口径做单指标 + 10 年分位:

  1. 实现波动率(Realized Vol,20日/63日):日收益标准差×√252 —— VIX 分量
     的公开代理(VIX 是隐含波动,历史本地缺,用 realized 同口径位置)
  2. 市场宽度 10 年分位(RSP/SPY 等权-加权比,见 breadth.py)
  3. 牛形体制(震荡牛/单边牛/熊)——本公司 2026-10-11 regime 研究方法论
     的滚动版:
       R²(252)  log 价格线性拟合优度(≈1=直线单边)
       dips(252) 年内 ≥10% 校正次数(运行峰值回落口径)
       ER(252)   Kaufman 趋势效率比 |p_t−p_{t−n}| / Σ|Δp|(单边度)
     判定:252日涨幅≥10% + R²高校正少=单边牛;/校正多或R²低=震荡牛;
     ≤-10%=熊;其余=温和市。
  4. 历史同位年份:寻找指标向量(R²、dips、252日涨幅)最近的历史整年
     ——给"当前像哪年"的直觉锚(欧氏距离,指标先各自标准化)。

无假数据铁律:数据缺失的面板不渲染;10 年窗口不足时按实际窗口披露。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import numpy as np
import pandas as pd

from app.data import store

logger = logging.getLogger(__name__)

DAYS_10Y = 2520          # 10 年 ≈ 2520 个交易日
WINDOW = 252             # 体制/牛形判定窗口(1 年)


def _close(ticker: str) -> Optional[pd.Series]:
    df = store.read_ohlcv(ticker)
    if df is None or df.empty:
        return None
    return df["Adj Close"].dropna()


def _rolling_rv(px: pd.Series, span: int) -> pd.Series:
    """年化实现波动率序列(日收益 std×√252)。"""
    return px.pct_change().rolling(span).std() * np.sqrt(252)


def _window_metrics(px: pd.Series, n: int = WINDOW) -> Optional[Dict]:
    """近 n 日的牛形指标:涨幅/R²/dips/ER。整年样本 ~251 根也放行(取可得尾部)。"""
    if len(px) < 200:
        return None
    sub = px.iloc[-(min(n + 1, len(px))):]
    ret = float(sub.iloc[-1] / sub.iloc[0] - 1)
    lg = np.log(sub)
    t = np.arange(len(lg))
    k, b = np.polyfit(t, lg, 1)
    ss_res = float(((lg - (k * t + b)) ** 2).sum())
    ss_tot = float(((lg - lg.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot else float("nan")
    # 10% 校正(运行峰值回落口径,同年度 regime 研究)
    peak, dips, in_dip = sub.iloc[0], 0, False
    for v in sub:
        peak = max(peak, v)
        if not in_dip and v / peak - 1 <= -0.10:
            in_dip, dips = True, dips + 1
        if v >= peak:
            in_dip = False
    er = float(abs(sub.iloc[-1] - sub.iloc[0]) / sub.diff().abs().sum())
    return {"ret_252": ret, "r2": r2, "dips": dips, "er": er}


def _regime_label(m: Dict) -> Dict:
    if m["ret_252"] >= 0.10:
        if m["r2"] >= 0.75 and m["dips"] <= 1:
            label, desc = "单边牛", "路径直、校正少——趋势策略舒适区,择时摩擦小"
        else:
            label, desc = "震荡牛", "颠簸上行——回调深、反弹快,高抛低吸类占优"
    elif m["ret_252"] <= -0.10:
        label, desc = "熊市", "趋势向下,防守优先"
    else:
        label, desc = "温和市", "低斜率或横盘,方向不明"
    return {"label": label, "desc": desc}


def _nearest_year(px: pd.Series) -> Optional[Dict]:
    """当前 252 日指标向量在历史整年中找最近邻(欧氏距离,指标标准化)。"""
    if len(px) < DAYS_10Y:
        return None   # 历史年份池要 10 年长度才有意义
    cur = _window_metrics(px)
    if cur is None:
        return None
    rows = {}
    for y in range(px.index[0].year + 1, px.index[-1].year):   # 只取完整年
        sub = px[px.index.year == y]
        m = _window_metrics(sub) if len(sub) > 200 else None
        if m:
            rows[y] = m
    if len(rows) < 5:
        return None
    keys = ("ret_252", "r2", "dips")
    allv = np.array([[r[k] for k in keys] for r in rows.values()])
    mu, sd = allv.mean(0), allv.std(0) + 1e-9
    curv = np.array([cur[k] for k in keys])
    dist = {y: float(np.linalg.norm((np.array([r[k] for k in keys]) - curv) / sd))
            for y, r in rows.items()}
    best = min(dist, key=dist.get)
    return {"year": best, "label_desc": f"最接近 {best} 年的指标形态(R²/dips/涨幅)",
            "metrics": rows[best]}


def _dispersion() -> Dict:
    """成份股分化的板块级近似(9 大 SPDR 横截面日收益 std,63 日平滑年化)。

    指数稳 + 分化高 = 窄基结构市(权重股护盘、筐内两极分化);个股级口径
    与 BofA/高盛 dispersion 报告同思想,成份按公开可得数据用 9 大板块。"""
    import pandas as pd
    nine = ["XLK", "XLY", "XLP", "XLE", "XLF", "XLI", "XLV", "XLB", "XLU"]
    frames = {}
    for t in nine:
        df = store.read_ohlcv(t)
        if df is None or df.empty:
            return {"available": False, "note": f"板块 ETF 数据缺(先同步 {'/'.join(nine)})"}
        frames[t] = df["Adj Close"].dropna()
    px = pd.DataFrame(frames).dropna()          # 9 只齐全交集(2005 起)
    ret = px.pct_change()
    disp = (ret.std(axis=1, ddof=1) * np.sqrt(252)).dropna()
    if len(disp) < 70:
        return {"available": False, "note": "板块交集样本不足"}
    disp = disp.rolling(63).mean().dropna()    # 单日横截面噪声大,63 日平滑
    win = disp.tail(DAYS_10Y)
    cur = float(disp.iloc[-1])
    pct = round(float((win < cur).mean() * 100), 1)
    # 颜色分档(返回给前端直接染):分位刻度而非绝对值刻度——分化度绝对数值
    # 的时间分布右偏长尾(平静年 ~7%、危机年 20%+),分位才是可比坐标
    if pct >= 90:
        level, cls = "极高", "neg"
    elif pct >= 70:
        level, cls = "高", "warn"
    elif pct >= 30:
        level, cls = "中", ""
    else:
        level, cls = "低", "pos"
    return {
        "available": True,
        "as_of": str(disp.index[-1].date()),
        "value": round(cur, 4),
        "pctile_10y": pct,
        "level": level,            # 颜色标识档位(极高neg红/高warn黄/中灰/低pos绿)
        "cls": cls,
        "range_10y": {"mean": round(float(win.mean()), 4),
                      "min": round(float(win.min()), 4),
                      "max": round(float(win.max()), 4)},
        "window": f"{win.index[0].date()} → {win.index[-1].date()} ({len(win)} 日)",
        "note": "9 大 SPDR 板块当日收益横截面离散(63 日均值,年化)——成份股分化的板块级近似",
    }


def indicators() -> Dict:
    """波动率分位 + 牛形体制,一次返回(SPY+QQQ)。"""
    out = {"ok": True, "vol": {}, "regime": {}, "vix": {"note": "^VIX 本地无历史,分位待数据补齐"}}
    asofs = []

    # 板块分化度(2026-10-12 用户定名):9 大 SPDR 板块(1998 系列,成份 25 年稳定)
    # 日收益的横截面标准差(63 日平滑、年化)——成份股分化的板块级近似(个股级
    # dispersion 需 ~500 只成分日线,大陆频控不可行)。与指数时序波动是两个
    # 正交维度:指数稳+分化高 = 头部集中护盘、内部两极分化的窄基结构市。
    out["dispersion"] = _dispersion()

    for tk in ("SPY", "QQQ"):
        px = _close(tk)
        if px is None or len(px) < WINDOW + 1:
            out["vol"][tk] = {"error": f"{tk} 数据不足"}
            continue
        asofs.append(str(px.index[-1].date()))
        entry = {"as_of": str(px.index[-1].date())}
        for span, name in ((20, "rv20"), (63, "rv63")):
            rv = _rolling_rv(px, span).dropna()
            win = rv.tail(DAYS_10Y)
            if len(win) < 250:
                entry[name] = {"value": round(float(rv.iloc[-1]), 4), "pctile_10y": None,
                               "note": f"历史样本 {len(win)} 日不足 10 年,分位缺"}
                continue
            entry[name] = {
                "value": round(float(rv.iloc[-1]), 4),
                "pctile_10y": round(float((win < rv.iloc[-1]).mean() * 100), 1),
                "window": f"{win.index[0].date()} → {win.index[-1].date()} ({len(win)} 日)",
            }
        m = _window_metrics(px)
        if m:
            lbl = _regime_label(m)
            m.update(lbl)
            m["nearest"] = _nearest_year(px)
            m["er"] = round(m["er"], 3)
            m["r2"] = round(m["r2"], 3)
            m["ret_252"] = round(m["ret_252"], 4)
            out["regime"][tk] = m
        out["vol"][tk] = entry

    out["as_of"] = max(asofs) if asofs else None
    if not out["vol"] and not out["regime"]:
        out["ok"] = False
        out["error"] = "SPY/QQQ 本地数据缺失"
    return out
