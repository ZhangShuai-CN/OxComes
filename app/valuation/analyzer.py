"""估值编排：symbol → {multiples, rule40, dcf 三情景, 同行对比, 行业参考}。"""
from __future__ import annotations

import logging
from typing import Optional

from app.valuation import dcf as dcf_mod
from app.valuation import fmp_client, metrics_calc, sector_ref

logger = logging.getLogger(__name__)


def analyze(symbol: str, fcf_growth_override: Optional[float] = None,
            with_peers: bool = True) -> dict:
    symbol = symbol.upper().strip()
    stmts = fmp_client.get_statements(symbol)
    if stmts is None:
        return {"ok": False,
                "error": "财务数据不可用：请确认 .env 已配置有效的 FMP_API_KEY（免费注册 site.financialmodelingprep.com）"}

    profile = stmts.get("profile") or {}
    price = _f(profile.get("price"))
    mcap = _f(profile.get("marketCap")) or _f(profile.get("mktCap"))
    # stable profile 无股本字段 → 市值/价格 恒等推导（ADR 1 股 = 1 ADS 情况下精确）
    shares = _f(profile.get("sharesOutstanding")) or _f(profile.get("shares")) \
        or (mcap / price if mcap and price else None)
    stmts["price"], stmts["shares"], stmts["mktCap"] = price, shares, mcap
    cash = _f((stmts["balance"][0].get("cashAndCashEquivalents")
               if stmts.get("balance") else None))
    debt = _f((stmts["balance"][0].get("totalDebt") if stmts.get("balance") else None))

    multiples = metrics_calc.compute_multiples(stmts)
    rule_score = metrics_calc.rule40(stmts)

    # DCF 三情景（3×3 r×g 敏感性 + 基准列）
    history_fcf = [row.get("freeCashFlow") for row in (stmts.get("cashflow") or [])[:3]]
    projected = dcf_mod.project_fcf(history_fcf, multiples.get("revenue_yoy"),
                                   fcf_growth_override)
    dcf_block = {"projected_fcf": [round(v, 0) for v in projected],
                 "growth_used": None}
    if projected:
        r0, g0 = 0.11, 0.025
        base_val = dcf_mod.dcf_value(projected, r0, g0, cash, debt, shares)
        grown = multiples.get("revenue_yoy")
        if fcf_growth_override is not None:
            dcf_block["growth_used"] = f"{fcf_growth_override:.1%}"
        elif grown is not None:
            dcf_block["growth_used"] = f"营收YoY {grown}% ×0.8 保守"
        dcf_block["base"] = {"r": r0, "g": g0, "value_per_share": base_val,
                             **_vs_price(base_val, price)}
        dcf_block["sensitivity"] = dcf_mod.sensitivity_matrix(
            projected, cash, debt, shares)
    else:
        dcf_block["error"] = "FCF 历史缺失，DCF 不可用（新上市/重资本企业常见）"

    industry = profile.get("industry") or ""
    sector = profile.get("sector") or ""
    ref = sector_ref.sector_pe_ref(industry or sector)

    peers_block = None
    if with_peers and (industry or sector):
        def _peer_multiples(sym: str):
            s = fmp_client.get_statements(sym)
            if s is None:
                return None
            prof = s.get("profile") or {}
            s["mktCap"] = _f(prof.get("marketCap")) or _f(prof.get("mktCap"))
            p = _f(prof.get("price"))
            s["price"] = p
            s["shares"] = (_f(prof.get("sharesOutstanding"))
                           or (s["mktCap"] / p if s["mktCap"] and p else None))
            return metrics_calc.compute_multiples(s)
        try:
            peers_block = sector_ref.sector_distribution(industry or sector, _peer_multiples)
        except Exception as e:
            logger.warning("同行分布失败 %s: %s", symbol, e)

    return {
        "ok": True,
        "symbol": symbol,
        "company": profile.get("companyName") or symbol,
        "industry": industry, "sector": sector,
        "price": price,
        "multiples": multiples,
        "rule40": rule_score,
        "dcf": dcf_block,
        "sector_ref": ref,
        "peers": peers_block,
        "notes": _notes(multiples, rule_score, ref, peers_block),
    }


def _notes(m, r40, ref, peers) -> list:
    notes = []
    if m.get("pe") and ref:
        if m["pe"] > ref["pe_high"]:
            notes.append(f"PE {m['pe']} 高于行业参考区间 [{ref['pe_low']},{ref['pe_high']}]——成长溢价或高估，用 DCF/Rule40 交叉检验")
        elif m["pe"] < ref["pe_low"]:
            notes.append(f"PE {m['pe']} 低于行业参考区间 [{ref['pe_low']},{ref['pe_high']}]——价值区间或盈利质量差，交叉核查")
    if r40.get("score") is not None:
        notes.append(f"Rule of 40 = {r40['score']}（{r40.get('verdict','')}）")
    if peers and peers.get("pe_dist"):
        d = peers["pe_dist"]
        notes.append(f"同行 PE 分布：P25={d['p25']} 中位={d['p50']} P75={d['p75']}（n={d['n']}）")
    return notes


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _vs_price(value, price) -> dict:
    if value is None or not price:
        return {}
    upside = value / price - 1
    return {"upside_pct": round(upside * 100, 1),
            "verdict": "低于现价" if upside < 0 else "高于现价"}
