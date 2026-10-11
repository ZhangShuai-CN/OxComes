"""FastAPI 入口：OxComes 本地平台。

Phase 1 只挂 /backtest；Phase 2 起挂每日消息、板块、调度任务。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.config import settings
from app.api import backtest as backtest_api
from app.api import p2 as p2_api
from app.api import admin as admin_api
from app.api import crypto as crypto_api

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO if not settings.notify_enabled else logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

templates = Jinja2Templates(directory=str(settings.base_dir / "templates"))

_scheduler = None


def _scheduler_ref():
    return _scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    settings.ensure_dirs()
    db.init_db()
    scheduler = None
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        _register_daily_jobs(scheduler)
        scheduler.start()
        _scheduler = scheduler
        logger.info("APScheduler 已启动")
    except Exception as e:  # 调度失败不阻塞启动
        logger.warning("APScheduler 启动失败(不影响页面使用): %s", e)
    yield
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
    _scheduler = None


def _register_daily_jobs(scheduler) -> None:
    """Phase 2：行情增量 + 事件扫描两个每日任务。"""
    from apscheduler.triggers.cron import CronTrigger
    from app.data.universe import CORE_UNIVERSE

    def _daily_sync():
        from app.data.loaders import yfinance_loader
        from app.data import store
        # 核心行情增量（行情同步是所有功能的地基）
        # 2026-10-10 事故教训:上游频控常致单日部分标的失败,失败若无重试→数据
        # 陈旧→热力图"最近交易日"跨标的错日(用户实测 SPY 停在 10-08/XLB 10-09)。
        # 失败标的记录 pending,08:43 重试任务专拉。
        import json
        fails = []
        for t in CORE_UNIVERSE:
            try:
                ok, _ = store.sync_ticker(t, yfinance_loader.fetch_single)
                if not ok:
                    fails.append(t)
            except Exception as e:
                fails.append(t)
                logger.warning("同步 %s 失败: %s", t, e)
        db.meta_set("pending_sync_tickers", json.dumps(fails) if fails else "")
        if fails:
            logger.warning("今日同步失败 %d 个标的(08:43 自动重试): %s",
                           len(fails), ",".join(fails))

    def _daily_sync_retry():
        """清晨同步失败标的的延迟重试(上游频控多为小时级冷却,隔 90 分钟通常可过)。"""
        import json
        from app.data.loaders import yfinance_loader
        from app.data import store
        raw = db.meta_get("pending_sync_tickers")
        if not raw:
            return
        try:
            pending = json.loads(raw)
        except Exception:
            pending = []
        if not pending:
            return
        still = []
        for t in pending:
            try:
                ok, _ = store.sync_ticker(t, yfinance_loader.fetch_single)
                if not ok:
                    still.append(t)
            except Exception as e:
                still.append(t)
                logger.warning("重试同步 %s 失败: %s", t, e)
        db.meta_set("pending_sync_tickers", json.dumps(still) if still else "")
        logger.info("行情同步重试: %d 成功 / %d 仍失败", len(pending) - len(still), len(still))

    def _daily_events():
        from app.events import calendar as ev_cal
        try:
            result = ev_cal.update_events(days=7)
            logger.info("事件扫描: %s", result)
        except Exception as e:
            logger.warning("事件扫描失败: %s", e)

    def _daily_push():
        if not settings.notify_enabled:
            return
        from app.notify import brief as brief_mod
        result = brief_mod.push_daily_brief()
        logger.info("每日推送: %s", result)

    scheduler.add_job(_daily_sync, CronTrigger(hour=7, minute=13, timezone="Asia/Shanghai"),
                      id="daily_quotes_sync", replace_existing=True)
    scheduler.add_job(_daily_sync_retry, CronTrigger(hour=8, minute=43, timezone="Asia/Shanghai"),
                      id="daily_quotes_sync_retry", replace_existing=True)
    scheduler.add_job(_daily_events, CronTrigger(hour=6, minute=41, timezone="Asia/Shanghai"),
                      id="daily_events_scan", replace_existing=True)
    scheduler.add_job(_daily_push, CronTrigger(hour=7, minute=53, timezone="Asia/Shanghai"),
                      id="daily_brief_push", replace_existing=True)

    def _weekly_backfill():
        # 每日消息历史窗口兜底(幂等):中断运行/冷启动丢掉过去 30 天时周日补齐
        from app.events import calendar as ev_cal
        try:
            result = ev_cal.backfill_events(days_back=30)
            logger.info("事件历史回填(周度兜底): %s", result)
        except Exception as e:
            logger.warning("事件回填失败: %s", e)

    scheduler.add_job(_weekly_backfill, CronTrigger(day_of_week="sun", hour=6, minute=55,
                          timezone="Asia/Shanghai"),
                      id="weekly_events_backfill", replace_existing=True)

    def _biweekly_13f():
        # 13F 披露集中在每季结束后 45 天内（2/5/8/11 月），双周核查足够
        from app.data.loaders import sec_loader
        try:
            sec_loader.sync_all()
            logger.info("13F 双周核查完成")
        except Exception as e:
            logger.warning("13F 核查失败: %s", e)

    def _monthly_tqqq_verify():
        from app.data import synthetic
        import json as _json
        try:
            build = synthetic.build_pure_synthetic()
            res = synthetic.verify() if build is not None else synthetic.VerifyResult()
            db.meta_set("tqqq_verify_result", _json.dumps({
                "ok": res.ok, "corr": res.corr, "drift": res.annualized_drift,
                "at": __import__("datetime").date.today().isoformat()}))
            logger.info("TQQQ 月度对账: ok=%s", res.ok)
        except Exception as e:
            logger.warning("TQQQ 对账失败: %s", e)

    scheduler.add_job(_biweekly_13f,
                      CronTrigger(day_of_week="sun", hour=9, minute=11,
                                  timezone="Asia/Shanghai"),
                      id="biweekly_13f_sync", replace_existing=True)
    scheduler.add_job(_monthly_tqqq_verify,
                      CronTrigger(day=1, hour=9, minute=33, timezone="Asia/Shanghai"),
                      id="monthly_tqqq_verify", replace_existing=True)


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.middleware("http")
async def _static_cache(request, call_next):
    """本地分析工具:静态资源禁用启发式缓存(每次改动 JS/CSS 立即生效,ETag 仍省带宽)。
    例外 favicon:URL 自带 ?v=N 版本号,换图标改版本号即可,长缓存避免每次页面加载都 304 协商。"""
    resp = await call_next(request)
    if request.url.path == "/static/favicon.ico":
        resp.headers["Cache-Control"] = "public, max-age=86400"
    elif request.url.path.startswith("/static"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


app.mount("/static", StaticFiles(directory=str(settings.base_dir / "static")), name="static")
app.include_router(backtest_api.router)
app.include_router(p2_api.router)
app.include_router(admin_api.router)
app.include_router(crypto_api.router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """浏览器默认请求根路径 /favicon.ico(静态目录挂在 /static 下,需单独路由)。"""
    return FileResponse(
        settings.base_dir / "static" / "favicon.ico",
        media_type="image/x-icon",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/")
async def index(request: Request):
    context = dict(request=request, app_name=settings.app_name, active="brief")
    return templates.TemplateResponse(request, "brief.html", context)


@app.get("/backtest")
async def backtest_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="backtest",
    )
    return templates.TemplateResponse(request, "backtest.html", context)


@app.get("/sectors")
async def sectors_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="sectors",
    )
    return templates.TemplateResponse(request, "sectors.html", context)


@app.get("/valuation")
async def valuation_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="valuation",
    )
    return templates.TemplateResponse(request, "valuation.html", context)


@app.get("/admin")
async def admin_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="admin",
    )
    return templates.TemplateResponse(request, "admin.html", context)


@app.get("/funds")
async def funds_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="funds",
    )
    return templates.TemplateResponse(request, "funds.html", context)


@app.get("/market")
async def market_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="market",
    )
    return templates.TemplateResponse(request, "market.html", context)


@app.get("/rates")
async def rates_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="rates",
    )
    return templates.TemplateResponse(request, "rates.html", context)


@app.get("/crypto")
async def crypto_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="crypto",
    )
    return templates.TemplateResponse(request, "crypto.html", context)


@app.get("/crypto/backtest")
async def crypto_backtest_page(request: Request):
    context = dict(
        request=request,
        app_name=settings.app_name,
        active="crypto-bt",
    )
    return templates.TemplateResponse(request, "crypto_bt.html", context)
