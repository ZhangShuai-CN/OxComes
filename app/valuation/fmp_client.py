"""FMP API 客户端（stable 新版端点）：三表/profile + 配额管理 + 长缓存。

适配 2024+ 的 stable API（经典 /api/v3/* 已对新用户停用）：
    GET /stable/profile?symbol=X
    GET /stable/income-statement?symbol=X&limit=N
    GET /stable/balance-sheet-statement?symbol=X&limit=N
    GET /stable/cash-flow-statement?symbol=X&limit=N
非 USD 报告货币（如 NOK 的 EUR 财报）经 /stable/forex 汇率换算为 USD，
避免「美元市值 ÷ 欧元利润」的跨币种错误。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date
from typing import Optional

import requests

from app import db
from app.config import settings

logger = logging.getLogger(__name__)

BASE = "https://financialmodelingprep.com/stable"

_ENDPOINTS = {  # endpoint → (path, 是否list)
    "profile":   ("/profile", True),
    "income":    ("/income-statement", True),
    "balance":   ("/balance-sheet-statement", True),
    "cashflow":  ("/cash-flow-statement", True),
}

_CURRENCY_FIELDS = {  # statements 报告货币字段
    "reportedCurrency",
}


def quota_used_today() -> int:
    key = f"fmp_quota_{date.today().isoformat()}"
    return int(db.meta_get(key) or "0")


def _count_request() -> None:
    key = f"fmp_quota_{date.today().isoformat()}"
    db.meta_set(key, str(quota_used_today() + 1))


def _http_json(path: str, params: dict) -> Optional[list | dict]:
    key = settings.fmp_api_key
    if not key:
        logger.error("FMP_API_KEY 未配置 —— 估值功能需要免费 Key")
        return None
    for attempt in range(2):
        try:
            r = requests.get(BASE + path, params={"apikey": key, **params}, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code in (200,):
                payload = r.json()
                _count_request()
                return payload
            if r.status_code in (401, 403):
                logger.error("FMP 拒绝(可能 key/权限): %s %s", r.status_code, r.text[:120])
                return None
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            logger.warning("FMP %s HTTP %s: %s", path, r.status_code, r.text[:60])
            return None
        except Exception as e:
            logger.warning("FMP %s 请求失败(第%d次): %s", path, attempt + 1, e)
            time.sleep(2)
    return None


def fetch(symbol: str, endpoint: str, params: Optional[dict] = None,
          force: bool = False) -> Optional[list | dict]:
    """带 7 天缓存的端点读取。"""
    if endpoint not in _ENDPOINTS:
        raise ValueError(f"未知 FMP 端点: {endpoint}")
    symbol = symbol.upper()
    cache_method = f"fmp_{endpoint}"
    row = db.query_one(
        "SELECT detail_json, as_of FROM valuation_snapshots WHERE symbol=? AND method=?",
        (symbol, cache_method))
    if not force and row:
        try:
            if (date.today() - date.fromisoformat(row["as_of"])).days < 7:
                return json.loads(row["detail_json"])
        except Exception:
            pass
    if quota_used_today() >= 245:
        logger.warning("FMP 当日配额接近上限，%s/%s 仅走缓存", symbol, endpoint)
        return json.loads(row["detail_json"]) if row else None

    path, _ = _ENDPOINTS[endpoint]
    q = {"symbol": symbol}
    # 免费档不允许 limit/period 参数（402 Premium Query Parameter）——不传，返回端点默认
    payload = _http_json(path, q)
    if payload is not None:
        db.execute(
            "INSERT INTO valuation_snapshots(symbol, as_of, method, value, detail_json) "
            "VALUES(?,?,?,?,?) ON CONFLICT(symbol, as_of, method) DO UPDATE SET "
            "value=excluded.value, detail_json=excluded.detail_json",
            (symbol, date.today().isoformat(), cache_method, 1,
             json.dumps(payload)[:400000]))
    return payload


def forex_rate(currency: str) -> Optional[float]:
    """非 USD 报告货币 → USD 的换算率（如 EUR→ 约1.08）。缓存 7 天。"""
    currency = (currency or "USD").upper()
    if currency == "USD":
        return 1.0
    cache_method = "forex_" + currency
    row = db.query_one(
        "SELECT detail_json, as_of FROM valuation_snapshots WHERE symbol=? AND method=?",
        (currency, cache_method))
    if row:
        try:
            if (date.today() - date.fromisoformat(row["as_of"])).days < 7:
                return float(json.loads(row["detail_json"])["rate"])
        except Exception:
            pass
    sym = f"{currency}USD"
    payload = _http_json("/forex", {"symbol": sym})
    rate = None
    if isinstance(payload, list) and payload:
        try:
            rate = float(payload[-1].get("close") or payload[-1].get("end")) / float(
                payload[-1].get("open") or 1)
        except Exception:
            rate = None
        if rate is None:
            rate = float(payload[-1].get("close") or 0) or None
    if rate is None:
        # 兜底：华尔街见闻/硬编码近似
        approx = {"EUR": 1.08, "GBP": 1.27, "JPY": 0.0067, "CNY": 0.14,
                  "KRW": 0.00073, "CAD": 0.73, "CHF": 1.13, "SEK": 0.095}
        if currency in approx:
            rate = approx[currency]
            logger.info("FMP forex 无数据，%s 用近似汇率 %s", currency, rate)
    if rate is not None:
        db.execute(
            "INSERT INTO valuation_snapshots(symbol, as_of, method, value, detail_json) "
            "VALUES(?,?,?,?,?) ON CONFLICT(symbol, as_of, method) DO UPDATE SET "
            "detail_json=excluded.detail_json",
            (currency, date.today().isoformat(), cache_method, rate,
             json.dumps({"rate": rate})))
    return rate


def get_statements(symbol: str) -> Optional[dict]:
    """三表 + profile + 报告货币校准（非 USD 财务换算为 USD）。"""
    income = fetch(symbol, "income", {"limit": 6})
    balance = fetch(symbol, "balance", {"limit": 6})
    cashflow = fetch(symbol, "cashflow", {"limit": 6})
    profile = fetch(symbol, "profile")
    if not income or not balance or not cashflow:
        return None
    profile_obj = profile[0] if isinstance(profile, list) and profile else {}

    reported_ccy = None
    for stmt in (income, balance, cashflow):
        if stmt and isinstance(stmt, list) and stmt:
            reported_ccy = stmt[0].get("reportedCurrency")
            break
    rate = forex_rate(reported_ccy or "USD")

    def _adj(rows):
        if not rows or not isinstance(rows, list):
            return rows
        if rate and rate != 1.0:
            out = []
            skip = {"date", "symbol", "reportedCurrency", "cik", "fillingDate",
                    "acceptedDate", "calendarYear", "period", "link", "finalLink",
                    "fiscalYear", "fiscalPeriod", "quarter"}
            for row in rows:
                out.append({k: (v * rate if isinstance(v, (int, float)) and k not in skip
                                and k not in skip else v) for k, v in row.items()})
            return out
        return rows

    return {
        "income": _adj(income), "balance": _adj(balance), "cashflow": _adj(cashflow),
        "profile": profile_obj,
        "currency_note": (f"财报货币 {reported_ccy} → USD 按 {rate} 换算"
                          if rate and rate != 1.0 else None),
        "fx_rate": rate,
    }


def quota_status() -> dict:
    return {"used_today": quota_used_today(), "limit": 250}
