"""DCF 引擎：5 年 FCF 预测 + Gordon 终值（方法论文档口径）。

模型（指南 3.2.1）：
    V = Σ FCF_t / (1+r)^t  +  TV / (1+r)^5 ,   TV = FCF_5 × (1+g) / (r-g)
    每股价值 = (V + 现金 - 总债务) / 流通股本
三情景联动（页面 3×3 矩阵）：r ∈ {10, 11, 12}% × g ∈ {2, 2.5, 3}%
FCF 预测增速默认取 min(max(营收YoY,0), 0.25) 的保守年化，前端可调。
"""
from __future__ import annotations

from typing import Optional


def _num(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def project_fcf(history_fcf: list, revenue_yoy: Optional[float],
               explicit_growth: Optional[float] = None) -> list[float]:
    """未来 5 年 FCF 预测：增速取显式参数或营收增速（截到 [0, 25%] 年化）。"""
    if not history_fcf or history_fcf[0] is None:
        return []
    base = history_fcf[0]     # 最近年度 FCF
    if explicit_growth is not None:
        g = max(-0.30, min(explicit_growth, 0.40))
    elif revenue_yoy is not None:
        g = max(0.0, min(revenue_yoy / 100 * 0.8, 0.25))   # 营收增速×0.8 保守折
    else:
        g = 0.03
    return [base * (1 + g) ** t for t in range(1, 6)]


def dcf_value(projected: list[float], r: float, g_term: float,
              cash: Optional[float], debt: Optional[float],
              shares: Optional[float]) -> Optional[float]:
    """Gordon 终值 DCF；返回每股价值。"""
    if not projected or not shares or shares <= 0:
        return None
    r = max(r, g_term + 0.02) * 1.0     # 安全：r 需显著大于 g
    r, g_term = max(r, 0.06), max(g_term, 0.0)
    if r <= g_term + 0.01:
        r = g_term + 0.02
    pv_sum = sum(fcf / (1 + r) ** t for t, fcf in enumerate(projected, start=1))
    tv = projected[-1] * (1 + g_term) / (r - g_term)
    enterprise = pv_sum + tv / (1 + r) ** len(projected)
    net_debt = (debt or 0) - (cash or 0)
    equity_value = enterprise - net_debt
    if equity_value <= 0:
        return None
    return equity_value / shares


def sensitivity_matrix(projected: list, cash, debt, shares,
                       r_values=(0.10, 0.11, 0.12),
                       g_values=(0.02, 0.025, 0.03)) -> dict:
    """3×3 情景矩阵（悲观→乐观：g 高估更乐观念约等于upside）。"""
    rows = []
    for r in r_values:
        row = []
        for g in g_values:
            v = dcf_value(projected, r, g, cash, debt, shares)
            row.append(round(v, 2) if v is not None else None)
        rows.append(row)
    return {"r_values": list(r_values), "g_values": list(g_values), "matrix": rows}
