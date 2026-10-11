"""板块轮动与宏观看板 API（Phase 2）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.market import breadth as breadth_mod
from app.market import macro as macro_mod
from app.market import sectors as sectors_mod

logger = logging.getLogger(__name__)
router = APIRouter(tags=["p2"])


@router.get("/api/sectors/daily")
async def sectors_daily(as_of: str | None = None):
    snap = sectors_mod.rotation_snapshot(as_of)
    if snap.get("ok"):
        try:
            sectors_mod.save_daily_rows(snap)
        except Exception as e:
            logger.warning("sector_daily 落库失败(不影响页面): %s", e)
    return snap


@router.get("/api/sectors/rotation")
async def sectors_rotation():
    """轮动象限散点 + RS 曲线（daily 已含，此端点保留作语义别名）。"""
    return sectors_mod.rotation_snapshot()


@router.get("/api/macro/yields")
async def macro_yields(history: int = Query(252, ge=30, le=5000)):
    return macro_mod.get_yields(history)


@router.get("/api/macro/panels")
async def macro_panels(days: int = Query(1000, ge=60, le=10000)):
    """宏观页走势图序列:美元指数/WTI/裂解价差/汇率(含倒数方向)/黄金/BTC。
    某品种本地无数据 = 该面板前端隐藏(无假数据铁律)。"""
    return macro_mod.get_macro_panels(days)


@router.get("/api/macro/breadth")
async def macro_breadth(days: int = Query(252, ge=30, le=5000)):
    return breadth_mod.get_breadth(days)


@router.get("/api/macro/vix")
async def macro_vix():
    return macro_mod.get_vix()


@router.get("/api/events")
async def events(history: int = 30, future: int = 7):
    """每日消息中心:过去 N 天 + 未来 M 天的入库事件(分类展示在前端做)。
    优先本地表;未来部分无数据时现场拉;历史需显式回填(POST /api/events/backfill)。"""
    from datetime import date, timedelta
    from app.events import calendar as ev_cal

    today = date.today()
    start = (today - timedelta(days=history)).isoformat()
    end = (today + timedelta(days=future)).isoformat()
    items = ev_cal.query_events(start, end)
    has_past = any(i["event_date"] < today.isoformat() for i in items)
    if not any(i["event_date"] >= today.isoformat() for i in items):
        try:
            ev_cal.update_events(days=future)
            items = ev_cal.query_events(start, end)
        except Exception as e:
            logger.warning("事件拉取失败: %s", e)
            return {"ok": False, "error": f"财报/IPO 源拉取失败: {e}"}
    return {"ok": True, "items": items, "window": {"start": start, "end": end},
            "need_backfill": not has_past, "source": "nasdaq",
            "note": "财报按关注清单过滤（app/events/calendar.py DEFAULT_WATCHLIST）"}


@router.post("/api/events/backfill")
async def events_backfill(days_back: int = 30):
    """历史回填:过去 N 天财报+IPO(幂等,重复跑只补增量)。约 10-30 秒。"""
    from app.events import calendar as ev_cal
    try:
        return ev_cal.backfill_events(days_back=min(days_back, 90))
    except Exception as e:
        logger.warning("事件回填失败: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/api/notify/daily-brief")
async def push_brief():
    """手动触发每日消息推送（受 .env NOTIFY_ENABLED 开关控制）。"""
    from app.notify import brief as brief_mod
    return brief_mod.push_daily_brief()


@router.get("/api/notify/test")
async def notify_test():
    from app.notify.brief import build_markdown
    title, content = build_markdown()
    return {"ok": True, "title": title, "markdown": content}


@router.get("/api/valuation/{symbol}")
async def valuation(symbol: str, fcf_growth: float | None = None,
                    with_peers: bool = True):
    """个股估值全包。fcf_growth 如 0.1=10%（覆盖自动增速）。"""
    from app.valuation import analyzer, fmp_client
    result = analyzer.analyze(symbol, fcf_growth_override=fcf_growth,
                              with_peers=with_peers)
    result["quota"] = fmp_client.quota_status()
    return result


# ── 美股总览:美股热力图(treemap,参考 tepeiya/stock-heatmap 与 coinglass) ──

_HEAT_BROAD = [("SPY", "标普500", 2.6), ("QQQ", "纳指100", 2.2), ("RSP", "等权标普", 1.4),
               ("TQQQ", "纳指3x", 1.0), ("SQQQ", "纳指反向3x", 0.8)]
_HEAT_PERIODS = [("1d", 1), ("1w", 5), ("1m", 21), ("3m", 63), ("1y", 252)]


@router.get("/api/market/heatmap")
async def market_heatmap():
    """美股 treemap:11 板块 ETF + 宽基。块大小=固定示意权重(Phase 2 可接入
    ETF 规模),色=选定周期涨跌幅。本地 parquet 即时计算,无外部请求。

    日期对齐(2026-10-10 修复"今日显示昨日"事故):全市场基准日 as_of=所有标的
    最后 K 线的最大值;"1d"(最近交易日)仅对数据已达 as_of 的块计算,陈旧块
    无 1d 值(前端在最近交易日视图隐藏并披露数量)。其余周期(1w~1y)历史段
    完整不受影响。"""
    from app.data import store, universe

    # 第一轮:取全部标的的收盘序列,确定基准日 as_of
    specs = [(t, n, "宽基", w) for t, n, w in _HEAT_BROAD] + \
            [(t, cfg.sector, "板块", 1.0) for t, cfg in universe.SECTOR_ETFS.items()]
    series = {}
    for t, _, _, _ in specs:
        df = store.read_ohlcv(t)
        if df is None or df.empty:
            continue
        px = df["Adj Close"].dropna()
        if len(px):
            series[t] = px
    if not series:
        return {"ok": False, "error": "无本地行情(先运行 cli.py sync)"}
    as_of = max(px.index[-1] for px in series.values())

    # 第二轮:逐块计算周期涨跌;1d 只认 as_of 当根 K 线
    blocks, stale = [], []
    for t, name, group, w in specs:
        px = series.get(t)
        if px is None:
            continue
        changes = {}
        for key, n in _HEAT_PERIODS:
            if key == "1d":
                if px.index[-1] == as_of and len(px) > 1:
                    changes[key] = float(px.iloc[-1] / px.iloc[-2] - 1.0)
            elif len(px) > n:
                changes[key] = float(px.iloc[-1] / px.iloc[-1 - n] - 1.0)
        if not changes:
            continue
        last = str(px.index[-1].date())
        if px.index[-1] < as_of:
            stale.append({"id": t, "last": last})
        blocks.append({"id": t, "name": name, "group": group,
                       "weight": w, "changes": changes,
                       "close": float(px.iloc[-1]),
                       "last": last})
    return {"ok": True, "as_of": str(as_of.date()),
            "periods": [k for k, _ in _HEAT_PERIODS],
            "blocks": blocks, "stale": stale,
            "note": "块大小为示意权重,非流通市值(市值版待接 ETF AUM 数据)"}
