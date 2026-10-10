"""财报/IPO 事件日历：Nasdaq API 拉取 + SQLite 落库（fingerprint 幂等去重）。

Nasdaq API 已在大陆网络实测可达（headers 移植自老项目验证过的请求方式）。
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests

from app import db
from app.events.models import Event

logger = logging.getLogger(__name__)

NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# 关注清单：财报事件按此过滤（避免每天数百个小票财报把页面刷爆）
DEFAULT_WATCHLIST = [
    "NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD",
    "NFLX", "CRM", "ORCL", "MU", "COIN", "MSTR", "HOOD", "MARA", "RIOT",
    "SMCI", "ARM", "PLTR", "SOFI", "SQ", "SHOP", "SNAP", "UBER", "ABNB",
]


def _nasdaq_get(url: str, params: dict) -> Optional[dict]:
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, headers=NASDAQ_HEADERS, timeout=20)
            if r.status_code != 200:
                logger.info("nasdaq %s HTTP %s", url.rsplit('/', 1)[-1], r.status_code)
                return None
            return r.json()
        except Exception as e:
            logger.warning("nasdaq %s 第%d次失败: %s", url.rsplit('/', 1)[-1], attempt + 1, e)
            time.sleep(1.5)
    return None


def _market_cap_value(mcap_str: str) -> float:
    """'$170,000,000,000' → 1.7e11；解析失败返回 0。"""
    if not mcap_str:
        return 0.0
    digits = "".join(ch for ch in mcap_str if ch.isdigit())
    return float(digits) if digits else 0.0


_TIME_LABELS = {
    "time-pre-market": "盘前", "time-after-market": "盘后",
    "time-not-supplied": "", "time-tbd": "待定",
}


def fetch_earnings(day: date, watchlist=DEFAULT_WATCHLIST) -> list:
    payload = _nasdaq_get("https://api.nasdaq.com/api/calendar/earnings",
                          {"date": day.strftime("%Y-%m-%d")})
    if not payload:
        return []
    rows = (((payload.get("data") or {}).get("rows")) or [])
    out = []
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        if not sym or "." in sym:
            continue
        # 分层准入：关注清单（high）或 市值 ≥ $1000亿（medium，大票财报影响板块）
        in_watch = not watchlist or sym in watchlist
        mcap = _market_cap_value(row.get("marketCap") or "")
        if not in_watch and mcap < 1e11:
            continue
        name = row.get("name") or sym
        when = _TIME_LABELS.get(row.get("time") or "", "")
        eps = row.get("epsForecast") or ""
        desc_parts = [p for p in (when, f"EPS预期 {eps}" if eps else "") if p]
        out.append(Event(
            name=f"{sym} {name}" if name != sym else sym,
            event_date=day.strftime("%Y-%m-%d"),
            category="earnings",
            importance="high" if sym in ("NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA") else "medium",
            description=" · ".join(desc_parts),
            source="nasdaq",
        ))
    return out


def fetch_ipos(day: date) -> list:
    """当天 IPO(内部走月度日历过滤到日,供每日增量用;历史回填用 month 版少请求)。"""
    try:
        month_events = fetch_ipos_month(day.year, day.month)
    except Exception as e:
        logger.warning("ipo 月度拉取失败 %s-%s: %s", day.year, day.month, e)
        return []
    return [ev for ev in month_events if ev.event_date == day.strftime("%Y-%m-%d")]


def fetch_ipos_month(year: int, month: int) -> list:
    """Nasdaq 月度 IPO 日历全月合格事件(老项目验证过的端点: api/ipo/calendar�oday
    api/ipo/calendar?date=YYYY-MM)。入库级过滤:募资 ≥$500M 全收;
    ≥$100M 且含科技关键词收。历史回填按月拉一次,避免逐日重复请求。"""
    url = "https://api.nasdaq.com/api/ipo/calendar"
    payload = _nasdaq_get(url, {"date": f"{year:04d}-{month:02d}"})
    if not payload:
        return []
    data = payload.get("data") or {}
    upcoming = ((data.get("upcoming") or {}).get("upcomingTable") or {}).get("rows") or []
    priced = (data.get("priced") or {}).get("rows") or []
    out = []
    for row in upcoming + priced:
        sym = (row.get("proposedTickerSymbol") or row.get("symbol") or "").upper()
        company = row.get("companyName") or sym
        if not sym or "." in sym:
            continue
        date_str = row.get("expectedPriceDate") or row.get("pricedDate") or ""
        if not date_str:
            continue
        try:
            ipo_date = datetime.strptime(date_str.strip(), "%m/%d/%Y").date()
        except ValueError:
            continue
        # 募资解析："$1,200,000,000" → 1200 (M)
        raw_m = row.get("dollarValueOfSharesOffered") or ""
        digits = "".join(ch for ch in raw_m if ch.isdigit())
        raise_m = float(digits) / 1e6 if digits else 0.0
        tech_kw = ("tech", "software", "cloud", "ai", "robot", "biotech",
                   "pharma", "semiconductor", "data", "cyber", "digital",
                   " bio, ", "therapeutic", "medical", "gene")
        company_l = company.lower()
        if not (raise_m >= 500 or (raise_m >= 100 and any(k in company_l for k in tech_kw))):
            continue
        price = row.get("proposedSharePrice") or row.get("priceOfferedPerShareInUsd") or ""
        exch = row.get("proposedExchange") or ""
        desc_parts = [p for p in (
            f"募资 ${raise_m:,.0f}M" if raise_m else "",
            f"定价 ${price}" if price else "", exch) if p]
        out.append(Event(
            name=f"{sym} {company}"[:60],
            event_date=ipo_date.strftime("%Y-%m-%d"),
            category="ipo",
            importance="high" if raise_m >= 1000 else "medium",
            description=" · ".join(desc_parts),
            source="nasdaq",
        ))
    return out


def _insert_event(ev: Event) -> bool:
    """fingerprint 幂等落库;新插入返回 True(用于统计真实增量)。"""
    try:
        db.execute(
            "INSERT INTO events(fingerprint, name, event_date, category, "
            "importance, description, url, source) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(fingerprint) DO NOTHING",
            ev.to_row())
        return True
    except Exception as e:
        logger.warning("事件落库失败 %s: %s", ev.name, e)
        return False


def backfill_events(days_back: int = 30) -> dict:
    """历史回填:过去 N 天的财报(逐日,周末跳过) + IPO(按月,一次请求/月)。
    fingerprint 幂等,重复跑只补增量。财报日历 Nasdaq 支持过去日期查询;
    IPO 的 priced 表天然含近期已上市公司。"""
    today = date.today()
    total = 0
    months = set()
    for offset in range(1, days_back + 1):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:      # 周末无财报,跳过省请求
            continue
        for ev in fetch_earnings(day):
            if _insert_event(ev):
                total += 1
        time.sleep(0.4)          # Nasdaq 频控防护,与 update_events 同节流
        m = (day.year, day.month)
        if m not in months:
            months.add(m)
            for ev in fetch_ipos_month(m[0], m[1]):
                if _insert_event(ev):
                    total += 1
    # 当月也要覆盖(priced 表)
    m = (today.year, today.month)
    if m not in months:
        for ev in fetch_ipos_month(m[0], m[1]):
            if _insert_event(ev):
                total += 1
    return {"ok": True, "backfilled": total, "days_back": days_back}


def update_events(days: int = 7, include_ipo: bool = True) -> dict:
    """扫描未来 N 天：拉财报（watchlist 过滤）+ IPO，fingerprint 去重落库。"""
    today = date.today()
    total, seen = 0, []
    for offset in range(days):
        day = today + timedelta(days=offset)
        if day.weekday() >= 5:      # 周末无财报/IPO，跳过省请求
            continue
        events = fetch_earnings(day)
        if include_ipo:
            events += fetch_ipos(day)
        for ev in events:
            if _insert_event(ev):
                total += 1
        seen.extend([e.to_dict_like() for e in events])
        time.sleep(0.4)
    return {"ok": True, "fetched": total, "days": days}


def query_events(start: str, end: str) -> list:
    rows = db.query_all(
        "SELECT * FROM events WHERE event_date >= ? AND event_date <= ? "
        "ORDER BY event_date, category, name", (start, end))
    return rows


# 小工具：Event → dict（避免模型层引入 JSON 依赖）
def _to_dict_like(self):
    return {"name": self.name, "date": self.event_date, "category": self.category,
            "importance": self.importance, "description": self.description}
Event.to_dict_like = _to_dict_like
