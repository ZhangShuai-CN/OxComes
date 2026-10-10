"""回测表现指标。

依据已有方法论文档口径：CAGR/年化波动/Sharpe(rf 可选 ^IRX)/Sortino/MDD/Calmar/
胜率/年度收益表/回撤期分析（Top5 + 最长恢复期）。

术语口径（与外站对齐）：外部回测站（Portfolio Visualizer / testfol.io 等）显示的
"IRR" 在无外部现金流（一次性投入）时与 CAGR 数学等价：(期末/期初)^(1/年数)-1。
本模块的 cagr 字段就是这个口径 —— 名字不同、数值必然一致；两者出现差异时，
差异一定来自数据/仓位，而不是公式。定投（DCA）等含现金流场景本模块用
money-weighted 近似（期末/总投入的年化），与逐笔现金流的 XIRR 略有出入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class DrawdownPeriod:
    peak_date: object        # 前高日期
    trough_date: object      # 谷底日期
    recovery_date: Optional[object]  # 恢复前高的日期（None=未恢复）
    depth: float             # 最大回撤深度（负数）
    length_days: int         # 从 peak 到 trough 的交易日数
    recovery_days: Optional[int]  # peak→recovery 总交易日数


@dataclass
class Performance:
    cagr: float = 0.0
    ann_vol: float = 0.0
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    max_drawdown: float = 0.0
    calmar: float = 0.0
    total_return: float = 0.0
    win_rate_daily: float = 0.0
    win_rate_monthly: float = 0.0
    best_year: float = 0.0
    worst_year: float = 0.0
    years: float = 0.0
    annual_returns: dict = field(default_factory=dict)
    drawdown_top5: list = field(default_factory=list)   # list[dict] 便于 JSON 化
    longest_recovery_days: Optional[int] = None
    invested: Optional[float] = None       # 总投入(含期初;仅现金流场景非空)
    xirr: Optional[float] = None           # 真钱权年化(XIRR,二分求解;现金流场景的外站对齐口径)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["annual_returns"] = self.annual_returns
        d["drawdown_top5"] = self.drawdown_top5
        return d


def _xirr(nav: pd.Series, cashflows: Optional[pd.Series],
          invested: Optional[float]) -> Optional[float]:
    """真钱权年化(XIRR):解 r 使 期初(1+r)^T + Σ定投(1+r)^持有期 = 期末值。

    与外部网站 DCA 显示的 "IRR / Money-Weighted Return" 同口径。
    cashflows 为引擎口径的定投序列(不含期初资本,期初资本在 nav.index[0] 注入)。
    二分求解(r 对 FV 单调增);不收敛返回 None。
    """
    if cashflows is None or invested is None or invested <= 0:
        return None
    cf = cashflows.reindex(nav.index).fillna(0.0)
    T = nav.index[-1]
    years_total = (T - nav.index[0]).days / 365.25
    initial = max(invested - float(cf.sum()), 0.0)      # 期初资本
    hold_years = ((T - cf.index).days) / 365.25          # 各笔投入的持有年数

    def fv(r: float) -> float:
        return (initial * (1 + r) ** years_total
                + float((cf * (1 + r) ** hold_years).sum()))

    target = float(nav.iloc[-1])
    lo, hi = -0.99, 5.0
    if fv(lo) > target or fv(hi) < target:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if fv(mid) > target:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def compute(
    nav: pd.Series,
    returns: pd.Series,
    rf_annual: float = 0.0,
    invested: Optional[float] = None,
    cashflows: Optional[pd.Series] = None,
) -> Performance:
    p = Performance()
    if nav is None or len(nav) < 2:
        return p
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    p.years = years

    total_mult = nav.iloc[-1] / (invested if invested and invested > 0 else nav.iloc[0])
    # DCA 场景以「总投入→期末值」口径算 total_return；纯策略以首末净值
    p.total_return = total_mult - 1.0 if invested else nav.iloc[-1] / nav.iloc[0] - 1.0
    if invested:
        # 现金流场景 CAGR 用 money-weighted 近似：期末/总投入的年化(等效一次性,
        # 低估真钱权收益;真钱权见 xirr 字段)
        p.invested = float(invested)
        p.cagr = (nav.iloc[-1] / invested) ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
        p.xirr = _xirr(nav, cashflows, invested)
    else:
        p.cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0

    rets = returns.dropna()
    if len(rets) > 1:
        p.ann_vol = rets.std() * np.sqrt(TRADING_DAYS)
        rf_daily = rf_annual / TRADING_DAYS
        excess = rets - rf_daily
        if excess.std() > 0:
            p.sharpe = excess.mean() / excess.std() * np.sqrt(TRADING_DAYS)
        downside = excess[excess < 0]
        if len(downside) > 0 and downside.std() > 0:
            p.sortino = excess.mean() / downside.std() * np.sqrt(TRADING_DAYS)
        # 胜率以非平盘日为分母（涨天数 / 有变动天数），平盘不计
        nz = rets[rets != 0]
        p.win_rate_daily = (nz > 0).mean() if len(nz) else 0.0

    roll = nav.cummax()
    dd = nav / roll - 1.0
    p.max_drawdown = dd.min()

    if p.max_drawdown < 0:
        p.calmar = p.cagr / abs(p.max_drawdown)

    # 月度胜率
    monthly = nav.resample("ME").last().dropna()
    if len(monthly) > 1:
        mr = monthly.pct_change().dropna()
        p.win_rate_monthly = (mr > 0).mean()

    # 年度收益表：以回测起始日净值为基点，年末值逐段计算变动
    annual = nav.resample("YE").last().dropna()
    if len(annual) > 0:
        combined = pd.concat([pd.Series([nav.iloc[0]], index=[nav.index[0]]), annual])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        ar = combined.pct_change().dropna()
        p.annual_returns = {str(k.year): float(v) for k, v in ar.items()}
        if len(ar) > 0:
            p.best_year = ar.max()
            p.worst_year = ar.min()

    # 回撤期分析
    periods = _drawdown_periods(dd)
    p.drawdown_top5 = [
        {
            "peak_date": str(d.peak_date.date()),
            "trough_date": str(d.trough_date.date()),
            "recovery_date": str(d.recovery_date.date()) if d.recovery_date is not None else None,
            "depth": float(d.depth),
            "length_days": d.length_days,
            "recovery_days": d.recovery_days,
        }
        for d in periods[:5]
    ]
    recovered = [d.recovery_days for d in periods if d.recovery_days is not None]
    p.longest_recovery_days = max(recovered) if recovered else None
    return p


def _drawdown_periods(dd: pd.Series) -> list:
    """水下→分段的回撤区间，按深度排序。"""
    periods = []
    underwater = dd < -1e-12
    if not underwater.any():
        return periods
    groups = (underwater != underwater.shift()).cumsum()
    for _, idx in dd[underwater].groupby(groups[underwater]).groups.items():
        seg = dd.loc[idx]
        trough_i = seg.idxmin()
        # 峰值日期：水下首日的前一交易日（即最后一个创新高日）
        loc_before = dd.index.get_loc(idx[0]) - 1
        peak_i = dd.index[loc_before] if loc_before >= 0 else idx[0]
        depth = seg.min()
        # 恢复日：水下段之后首个回到前高的日期
        after = dd.loc[idx[-1]:]
        recovery_rows = after[after >= 0]
        recovery_date = recovery_rows.index[0] if len(recovery_rows) > 0 else None
        rec_days = None
        if recovery_date is not None:
            rec_days = dd.index.get_loc(recovery_date) - dd.index.get_loc(idx[0]) + 1
        periods.append(DrawdownPeriod(
            peak_date=peak_i,
            trough_date=trough_i,
            recovery_date=recovery_date,
            depth=float(depth),
            length_days=len(idx),
            recovery_days=rec_days,
        ))
    periods.sort(key=lambda d: d.depth)
    return periods
