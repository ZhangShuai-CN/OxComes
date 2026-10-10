"""回测 API：策略清单 + 同步执行回测（向量化回测 <1s，无需任务队列）。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backtest import runner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestRequest(BaseModel):
    strategy_id: str = Field(alias="strategy")
    tickers: list[str] = Field(alias="tickers")
    params: dict = Field(default_factory=dict)
    start: str | None = None
    end: str | None = None
    benchmarks: list[str] = Field(default_factory=list)
    initial_capital: float = 10_000
    rebalance_cost_bps: float = 2.0
    use_synth_tqqq: str = "auto"     # auto | always | never
    allow_leverage: bool = False
    execution_price: str = "next_open"   # close | next_open（t+1 开盘成交，外站口径）

    model_config = {"populate_by_name": True}


@router.get("/strategies")
async def strategies():
    return runner.strategy_catalog()


@router.get("/range")
async def data_range(tickers: str = "TQQQ"):
    """本地数据实际可用范围（回测页默认日期回填）。

    TQQQ 的默认起点取 max(拼接序列实际起点, 1999-03-10=QQQ 上市日)：
    1991-1999 段是「NDX 指数桥接 × 3x 模型」的双重合成（置信度较低），
    默认窗口从 QQQ 真实数据存在的日期起 —— 需要泡沫期压力测试时用户可手动改早。
    """
    from app.data import store
    from app.data import synthetic
    ts = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ts:
        raise HTTPException(400, "tickers 不能为空")
    starts, ends = [], []
    for t in ts:
        src = t
        if t == "TQQQ":
            # 与 auto 口径一致的最早覆盖;拼接文件不健康(末端缺失/NaN/溢出)时回落真实段
            from app.backtest import runner as _runner
            if _runner._synth_health_ok():
                src = synthetic.SYNTH_SPY_ID
        df = store.read_ohlcv(src)
        if df is None:
            df = store.read_ohlcv(t)
        if df is None or df.empty:
            raise HTTPException(404, f"{t} 无本地数据")
        first = df.index[0]
        if t == "TQQQ":
            first = max(first, synthetic.QQQ_LISTING)   # 默认从 QQQ 上市日起
        starts.append(first)
        ends.append(df.index[-1])
    # 多标的取交集：所有标的都有数据的窗口
    return {"start": str(max(starts).date()), "end": str(min(ends).date()),
            "per_ticker": {t: {"start": str(s.date()), "end": str(e.date())}
                           for t, s, e in zip(ts, starts, ends)}}


@router.post("/run")
async def run(req: BacktestRequest):
    if req.use_synth_tqqq not in ("auto", "always", "never"):
        raise HTTPException(400, "use_synth_tqqq 取值: auto | always | never")
    if req.execution_price not in ("close", "next_open"):
        raise HTTPException(400, "execution_price 取值: close | next_open")
    result = runner.run_backtest(
        strategy_id=req.strategy_id,
        params=req.params,
        tickers=req.tickers,
        start=req.start,
        end=req.end,
        benchmarks=req.benchmarks,
        initial_capital=req.initial_capital,
        rebalance_cost_bps=req.rebalance_cost_bps,
        use_synth_tqqq=req.use_synth_tqqq,
        allow_leverage=req.allow_leverage,
        execution_price=req.execution_price,
    )
    return result.to_api_payload()


class ScanRequest(BaseModel):
    strategy: str
    tickers: list[str]
    grid: dict = Field(default_factory=dict)        # {param: [v1, v2...]}，未给的用 spec.grid
    start: str | None = None
    end: str | None = None
    use_synth_tqqq: str = "auto"
    initial_capital: float = 10_000
    rebalance_cost_bps: float = 2.0
    max_combos: int = 2000


@router.post("/scan")
async def scan(req: ScanRequest):
    """参数网格扫描（同步：向量化单次 <100ms，2000 组合 ≈ 2 分钟内）。"""
    from app.backtest import params as params_mod
    from app.backtest.strategies import get_strategy
    from itertools import product

    try:
        strategy = get_strategy(req.strategy)
    except KeyError as e:
        raise HTTPException(404, str(e))
    combos = params_mod.build_grid(strategy, req.grid or None)
    if len(combos) > req.max_combos:
        raise HTTPException(400, f"组合数 {len(combos)} 超限 {req.max_combos}（缩小 grid）")
    rows = []
    for combo in combos:
        result = runner.run_backtest(
            strategy_id=req.strategy, params=combo, tickers=req.tickers,
            start=req.start, end=req.end, benchmarks=[],
            initial_capital=req.initial_capital,
            rebalance_cost_bps=req.rebalance_cost_bps,
            use_synth_tqqq=req.use_synth_tqqq)
        if result.ok and result.perf:
            rows.append({"params": {k: v for k, v in combo.items()},
                         "cagr": result.perf.get("cagr"),
                         "mdd": result.perf.get("max_drawdown"),
                         "sharpe": result.perf.get("sharpe"),
                         "calmar": result.perf.get("calmar"),
                         "n_rebalance_days": result.n_rebalance_days})
    rows.sort(key=lambda r: (r.get("sharpe") if r.get("sharpe") is not None else -99),
                          reverse=True)
    return {"ok": True, "n": len(combos), "rows": rows[:50]}
