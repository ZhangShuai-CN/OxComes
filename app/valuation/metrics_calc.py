"""估值倍数与 Rule of 40 计算（纯函数，输入为 FMP statements dict）。

口径说明（依据项目估值方法论文档）：
- PE = 市值 / 净利润(TTM≈最近年报)；亏损或负权益时标 None 并给提示字段
- PS = 市值 / 营收（亏损/高成长股主口径）
- PB = 市值 / 股东权益（银行/重资产用）
- PEG = PE / 营收增速（近似口径；严格定义用 EPS 增速）
- Rule of 40 = 营收 YoY 增速% + FCF margin%（SaaS/成长股）；FCF 缺失时退 EBITDA margin
"""
from __future__ import annotations

from typing import Optional


def _last(rows: list, field: str):
    if not rows:
        return None
    try:
        return rows[0].get(field)
    except (AttributeError, IndexError):
        return None


def _prev(rows: list, field: str, years: int = 1):
    if len(rows) <= years:
        return None
    try:
        return rows[years].get(field)
    except (AttributeError, IndexError):
        return None


def compute_multiples(stmts: dict) -> dict:
    income = stmts.get("income") or []
    balance = stmts.get("balance") or []
    mcap = stmts.get("price") * stmts.get("shares") if (
        stmts.get("price") and stmts.get("shares")) else stmts.get("mktCap")

    revenue = _last(income, "revenue")
    net_income = _last(income, "netIncome")
    ebitda = _last(income, "ebitda")
    equity = _last(balance, "totalStockholdersEquity")

    out = {
        "market_cap": mcap,
        "revenue": revenue,
        "net_income": net_income,
        "ebitda": ebitda,
        "equity": equity,
    }
    if mcap and revenue and revenue > 0:
        out["ps"] = round(mcap / revenue, 2)
    if mcap and net_income and net_income > 0:
        out["pe"] = round(mcap / net_income, 2)
    else:
        out["pe"] = None
        out["pe_note"] = "亏损中，PE 无意义 → 用 PS/Rule 40 口径" if net_income is not None else None
    if mcap and equity and equity > 0:
        out["pb"] = round(mcap / equity, 2)
    rev_prev = _prev(income, "revenue")
    if revenue and rev_prev and rev_prev > 0:
        yoy = revenue / rev_prev - 1
        out["revenue_yoy"] = round(yoy * 100, 2)
        if out.get("pe"):
            out["peg"] = round(out["pe"] / (yoy * 100), 2) if yoy > 0 else None
    cashflow = stmts.get("cashflow") or []
    fcf = _last(cashflow, "freeCashFlow")
    out["fcf"] = fcf
    if fcf is not None and revenue:
        out["fcf_margin"] = round(fcf / revenue * 100, 2)
    if ebitda is not None and revenue:
        out["ebitda_margin"] = round(ebitda / revenue * 100, 2)
    return out


def rule40(stmts: dict) -> dict:
    """Rule of 40 = 营收增速% + 盈利质量%（FCF margin 优先，EBITDA margin 兜底）。"""
    income = stmts.get("income") or []
    cashflow = stmts.get("cashflow") or []
    revenue = _last(income, "revenue")
    rev_prev = _prev(income, "revenue")
    growth = (revenue / rev_prev - 1) * 100 if (revenue and rev_prev and rev_prev > 0) else None

    fcf = _last(cashflow, "freeCashFlow")
    ebitda = _last(income, "ebitda")
    margin = None
    margin_kind = None
    if fcf is not None and revenue:
        margin, margin_kind = fcf / revenue * 100, "FCF margin"
    elif ebitda is not None and revenue:
        margin, margin_kind = ebitda / revenue * 100, "EBITDA margin"

    score = round(growth + margin, 1) if (growth is not None and margin is not None) else None
    verdict = None
    if score is not None:
        if score >= 40:
            verdict = "✅ 达标（≥40）"
        elif score >= 30:
            verdict = "🟡 接近（30-40）"
        else:
            verdict = "❌ 未达标（<30）"
    return {
        "growth_pct": round(growth, 2) if growth is not None else None,
        "margin_pct": round(margin, 2) if margin is not None else None,
        "margin_kind": margin_kind,
        "score": score,
        "verdict": verdict,
        "and_line": 40,
        "definition": "营收 YoY 增速% + FCF margin%（缺失时用 EBITDA margin）",
    }
