"""管理端 API：数据新鲜度 / 调度任务 / 配额 / 手动同步 / 13F。"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from app import db
from app.config import settings
from app.data import store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


def _jobs_info() -> list:
    """读取 APScheduler 任务的下一次运行时间（进程内 scheduling 可用）。"""
    try:
        from app.main import _scheduler_ref
        sched = _scheduler_ref()
        if sched is None:
            return []
        out = []
        for job in sched.get_jobs():
            out.append({"id": job.id, "next_run": str(job.next_run_time)
                        if job.next_run_time else None})
        return out
    except Exception as e:
        logger.info("调度信息不可用: %s", e)
        return []


@router.get("/status")
async def status():
    from app.data.universe import CORE_UNIVERSE
    fresh = store.freshness(CORE_UNIVERSE)
    extra = store.freshness(["SYNTH_TQQQ", "SYNTH_TQQQ_PURE", "RSP"])
    fresh.update(extra)
    quota = db.meta_get(f"fmp_quota_{date.today().isoformat()}")
    # TQQQ 校验状态灯
    last_verify = db.meta_get("tqqq_verify_result")
    sync_rows = db.query_all("SELECT dataset, status, finished_at, message "
                             "FROM sync_log ORDER BY id DESC LIMIT 5")
    return {
        "ok": True,
        "freshness": fresh,
        "fmp_quota": {"used": int(quota or 0), "limit": 250},
        "jobs": _jobs_info(),
        "tqqq_verify": last_verify,
        "sync_log": sync_rows,
        "notify_enabled": settings.notify_enabled,
        "db_path": str(settings.db_path),
    }


class SyncRequest(BaseModel):
    dataset: str                 # quotes | events | fund_holdings | tqqq_verify | all


@router.post("/sync")
async def manual_sync(req: SyncRequest):
    import threading

    def _run():
        try:
            if req.dataset in ("quotes", "all"):
                from app.data.universe import CORE_UNIVERSE
                from app.data.loaders import yfinance_loader
                from datetime import datetime
                started = datetime.now().isoformat(timespec="seconds")
                ok = 0
                fails = []
                for t in CORE_UNIVERSE:
                    suc, msg = store.sync_ticker(t, yfinance_loader.fetch_single)
                    if suc:
                        ok += 1
                    else:
                        fails.append(t)
                db.execute(
                    "INSERT INTO sync_log(dataset, status, started_at, finished_at, message) "
                    "VALUES(?,?,?,?,?)",
                    ("daily_quotes", "ok" if not fails else "partial", started,
                     datetime.now().isoformat(timespec="seconds"),
                     f"ok={ok} failed={len(fails)}"))
            if req.dataset in ("events", "all"):
                from app.events import calendar as ev_cal
                ev_cal.update_events(days=7)
            if req.dataset in ("fund_holdings", "all"):
                from app.data.loaders import sec_loader
                results = sec_loader.sync_all()
                summary = "; ".join(
                    f"{r.get('manager','?')}:{'OK' if r.get('ok') else r.get('error')}"
                    for r in results)
                db.execute(
                    "INSERT INTO sync_log(dataset, status, finished_at, message) "
                    "VALUES('fund_holdings', ?, ?, ?)",
                    ("ok" if all(r.get("ok") for r in results) else "partial",
                     date.today().isoformat(), summary[:400]))
            if req.dataset in ("tqqq_verify", "all"):
                from app.data import synthetic
                import json
                res = synthetic.VerifyResult()
                try:
                    synthetic.build_pure_synthetic()
                    res = synthetic.verify()
                except Exception as e:
                    res.details.append(str(e))
                db.meta_set("tqqq_verify_result", json.dumps({
                    "ok": res.ok, "corr": res.corr, "drift": res.annualized_drift,
                    "at": date.today().isoformat()}))
        except Exception as e:
            logger.exception("手动同步失败")

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "started": req.dataset,
            "note": "后台执行中，稍后刷新 status 查看"}


@router.get("/fund-holdings")
async def fund_holdings(per_person: int = 10):
    """机构持仓页:按人物(巴菲特/段永平/德鲁肯米勒)返回各自最新季度重仓 top N。
    佩洛西/Trump 非 13F 主体,由前端静态说明(待接入),不进本 API。"""
    from app.data.loaders import sec_loader
    return {"ok": True, "persons": sec_loader.person_snapshot(per_person)}


@router.post("/fund-holdings/sync")
async def fund_holdings_sync():
    from app.data.loaders import sec_loader
    results = sec_loader.sync_all()
    return {"ok": True, "results": results}


# ── 数据源管理(2026-10-09):每个功能的数据来自哪里 + 优先链手动配置 ──

@router.get("/sources")
async def data_sources():
    from app.data import sources
    return sources.snapshot()


class SourceChainRequest(BaseModel):
    domain: str            # us_stock | rates | btc
    chain: list[str]      # 按优先级排序的 provider id 列表


@router.post("/sources")
async def set_source_chain(req: SourceChainRequest):
    from app.data import sources
    try:
        saved = sources.set_chain(req.domain, req.chain)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(400, str(e))
    return {"ok": True, "domain": req.domain, "chain": saved,
            "note": "优先链已保存,下次同步即时生效(无需重启)"}
