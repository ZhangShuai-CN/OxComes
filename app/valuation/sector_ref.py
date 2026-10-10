"""行业参考：GICS 11 行业静态 PE 参考表（估值指南附录口径）+ 同行倍数分布。

参考表（指南口径，区间为常见范围——非硬标准）：
    科技 25-35x · 半导体 20-30x · 消费 15-30x · 工业 15-25x
    金融 8-12x · 医疗 15-25x · 能源 8-13x · 公用 10-15x
同行分布走 FMP 批量（每票 4 请求），结果缓存在 valuation_snapshots 90 天。
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Optional

from app import db

logger = logging.getLogger(__name__)

# 指南口径的行业 PE 参考区间（指南附录）
SECTOR_PE_REF = {
    "Technology": (25, 35), "Semiconductors": (20, 30),
    "Consumer Electronics": (25, 35),
    "Consumer Cyclical": (20, 30), "Consumer Defensive": (15, 22),
    "Industrials": (15, 25), "Financial Services": (8, 12),
    "Healthcare": (15, 25), "Energy": (8, 13),
    "Utilities": (10, 15), "Basic Materials": (10, 16),
    "Real Estate": (15, 20), "Communication Services": (18, 28),
}

# 同行清单（每行业 8-10 只龙头；FMP 免费档批量算分布；按需可增）
PEERS = {
    "Technology": ["AAPL", "MSFT", "ORCL", "CRM", "ADBE", "NOW", "INTC", "IBM", "SAP"],
    # 美股纯消费电子标的稀少（GRMN/GPRO 小而怪），映射到大型科技可比组
    "Consumer Electronics": ["MSFT", "ORCL", "CRM", "ADBE", "NOW", "IBM", "SAP", "INTC"],
    "Semiconductors": ["NVDA", "AMD", "AVGO", "QCOM", "TXN", "MU", "MRVL", "ADI"],
    "Communication Equipment": ["CSCO", "ERIC", "MSI", "FFIV", "HPE", "ADTN", "AIRG"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX"],
    "Consumer Defensive": ["WMT", "PG", "KO", "PEP", "COST", "MO", "MDLZ", "CL"],
    "Industrials": ["HON", "UNP", "UPS", "CAT", "BA", "GE", "MMM", "LMT"],
    "Financial Services": ["JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "AXP"],
    "Healthcare": ["JNJ", "UNH", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG", "PXD", "PSX", "VLO"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP", "EXC", "SRE", "XEL"],
    "Basic Materials": ["LIN", "SHW", "APD", "ECL", "NEM", "FCX", "DOW", "NUE"],
    "Communication Services": ["GOOGL", "META", "NFLX", "DIS", "TMUS", "CMCSA", "T", "VZ"],
    "Real Estate": ["PLD", "AMT", "EQIX", "SPG", "O", "PSA", "WELL", "DLR"],
}


def sector_pe_ref(fmp_industry: str) -> Optional[dict]:
    rng = SECTOR_PE_REF.get(fmp_industry)
    if rng:
        return {"industry": fmp_industry, "pe_low": rng[0], "pe_high": rng[1],
                "pe_mid": round(sum(rng) / 2, 1)}
    for ind, r in SECTOR_PE_REF.items():
        if fmp_industry and ind.lower().split()[0] in fmp_industry.lower():
            return {"industry": fmp_industry, "pe_low": r[0], "pe_high": r[1],
                    "pe_mid": round(sum(r) / 2, 1)}
    return None


def peers_for(industry: str) -> list[str]:
    """精确命中优先；再退化到首词模糊匹配（如 'Semiconductors' 命中子类）。"""
    if not industry:
        return []
    il = industry.lower()
    if il in {k.lower() for k in PEERS}:
        return next(v for k, v in PEERS.items() if k.lower() == il)
    # 首词匹配（保留旧行为：从条目找和 industry 首词最具体的）
    first_word = il.split()[0]
    best = None
    for ind, lst in PEERS.items():
        if first_word in ind.lower():
            if best is None or len(ind) > len(best):
                best = ind
    return PEERS.get(best or "", [])


def sector_distribution(fmp_industry: str, metric_fn) -> Optional[dict]:
    """同行分布：metric_fn(symbol) → multiples dict；聚合 P25/中位/P75/均值。

    结果（含样本）写 valuation_snapshots 缓存 90 天，减少 FMP 配额消耗。
    """
    peers = peers_for(fmp_industry)
    if not peers:
        return None
    cache_key = f"sector_avg"
    today = date.today()
    row = db.query_one(
        "SELECT detail_json, as_of FROM valuation_snapshots WHERE symbol=? AND method=?",
        (fmp_industry, cache_key))
    if row:
        try:
            if (today - date.fromisoformat(row["as_of"])).days < 90:
                return json.loads(row["detail_json"])
        except Exception:
            pass

    samples = []
    for p in peers:
        try:
            m = metric_fn(p)
            if m and any(m.get(k) is not None for k in ("pe", "ps", "pb")):
                samples.append({"symbol": p,
                                "pe": m.get("pe"), "ps": m.get("ps"), "pb": m.get("pb")})
        except Exception as e:
            logger.info("peer %s 失败: %s", p, e)

    def _dist(key: str) -> Optional[dict]:
        vals = sorted(s[key] for s in samples
                      if s.get(key) is not None and s[key] > 0)
        if len(vals) < 3:
            return None
        n = len(vals)
        return {
            "p25": round(vals[int(n * 0.25)], 2),
            "p50": round(vals[int(n * 0.50)], 2),
            "p75": round(vals[min(int(n * 0.75), n - 1)], 2),
            "mean": round(sum(vals) / n, 2),
            "n": n,
        }

    result = {
        "industry": fmp_industry,
        "peers": peers,
        "n_ok": len(samples),
        "pe_dist": _dist("pe"),
        "ps_dist": _dist("ps"),
        "pb_dist": _dist("pb"),
        "samples": samples,
        "as_of": today.isoformat(),
    }
    if len(samples) >= 3:
        db.execute(
            "INSERT INTO valuation_snapshots(symbol, as_of, method, value, detail_json) "
            "VALUES(?,?,?,?,?) ON CONFLICT(symbol, as_of, method) DO UPDATE SET "
            "value=excluded.value, detail_json=excluded.detail_json",
            (fmp_industry, today.isoformat(), cache_key, len(samples),
             json.dumps(result)[:400000]))
    return result
