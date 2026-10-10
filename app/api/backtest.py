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


@router.get("/ma_signal")
async def ma_signal():
    """当日策略提醒:ma200_panic 两个形态(过热增强 on/off)的最新信号快照。

    语义 = 每日收盘后的执行指令(引擎 next_open 口径):t 收盘信号 → t+1 开盘
    成交。空仓跟随者按「在场」信号于次一交易日开盘买入。

    数据只取本地真实 TQQQ 序列(腾讯源,2011 起,当前价=实际市价 81.28 级),
    坚决不用 SYNTH 拼接序列(其绝对值是拼接常数缩放的连续口径,≠市价,用户
    无法在券商端核对)。信号不变性:SYNTH=真实×常数 → rolling 均线同比缩放,
    px/MA 比值与信号判定两口径逐位一致(2026-10-10 实测差 0.000000)。
    无真实数据直接报错,不降级不占位。
    """
    import pandas as pd

    from app.backtest.strategies import get_strategy
    from app.data import store

    df = store.read_ohlcv("TQQQ")
    if df is None or df.empty:
        return {"ok": False, "error": "无本地真实 TQQQ 数据——先运行同步;提醒面板不使用拼接序列"}
    if len(df) < 501:
        return {"ok": False, "error": f"本地 TQQQ 仅 {len(df)} 根,不足 500 日均线(需要 501 根)"}

    px_series = df["Adj Close"]
    ma200 = px_series.rolling(200, min_periods=200).mean()
    ma500 = px_series.rolling(500, min_periods=500).mean()
    last = df.index[-1]
    px, m2, m5 = float(px_series.iloc[-1]), float(ma200.iloc[-1]), float(ma500.iloc[-1])
    px_prev = float(px_series.iloc[-2])
    oh_sell, oh_rebuy = m2 * 1.5, m2 * 1.2

    stg = get_strategy("ma200_panic")()
    prices = px_series.to_frame("TQQQ")
    base_params = {"signal_ticker": "TQQQ", "holding_ticker": "TQQQ"}

    def _dist(action: str, overheat: bool) -> list[dict]:
        """按「当前价格位置 × 策略状态」计算关键距离(pct 为 到达该线还差的价格变动%)。"""
        d = []

        def add(label: str, target: float, note: str = ""):
            d.append({"label": label, "pct": round((target / px - 1) * 100, 1),
                      "target": round(target, 2), "note": note})

        in_panic = px < m5
        if action in ("hold", "buy"):          # 在场(或新进场):上行防过热,下行防破位
            add("涨到过热止盈线(200MA+50%)卖出", oh_sell)
            if overheat:
                add("跌到回落买回线(200MA+20%)", oh_rebuy, "仅过热止盈卖出后生效")
            add("跌到 200MA 破位卖出线", m2)
        elif action == "wait" and px > oh_sell:  # 过热等待:等回落到买回带
            add("跌到回落买回线(200MA+20%)买回", oh_rebuy)
            add("跌回 200MA(等待作废,恢复普通空仓)", m2)
        elif action == "wait":                  # 空仓观望带:两条进场路
            add("涨到 200MA 上方买入", m2)
            add("跌到 500MA 恐慌线买入", m5)
        elif action == "sell" and px > oh_sell:  # 过热止盈卖出:等回落接回
            add("跌到回落买回线(200MA+20%)买回", oh_rebuy)
            add("跌回 200MA(等待作废,恢复普通空仓)", m2)
        elif action == "sell":                  # 破 200MA 卖出:两条重新进场路
            add("涨回 200MA 上方买入", m2)
            if not in_panic:
                add("跌到 500MA 恐慌线买入", m5)
        return d

    def _snapshot(overheat: bool):
        out = stg.build(prices, {**base_params, "overheat": "on" if overheat else "off"})
        w = out.weights["TQQQ"].round(6)
        w_now, w_prev = bool(w.iloc[-1] > 0), bool(w.iloc[-2] > 0)
        # 最近一次仓位翻转(日期与去向);w 序列起点翻转无意义(状态机暖机)
        chg = (w != w.shift(1)) & w.shift(1).notna()
        flips = w.index[chg]
        last_flip = (str(flips[-1].date()), "在场" if w.iloc[-1] > 0 else "空仓") \
            if len(flips) else None
        # 动作分类:对「空仓跟随者」与「已在场持有者」分别给出次一开盘指令
        if w_now and not w_prev:
            action, text = "buy", "进场信号已确认 → 次一交易日开盘买入(在场者忽略)"
        elif w_now and w_prev:
            action, text = "hold", "持有,无需动作"
        elif w_now is False and w_prev:
            if px > oh_sell:
                action, text = "sell", "过热止盈信号(收盘>200MA+50%)→ 次一交易日开盘卖出,等回落买回"
            else:
                action, text = "sell", "离场信号(收盘跌破200MA)→ 次一交易日开盘卖出"
        elif px > oh_sell:
            action, text = "wait", "过热等待中,等回落到 200MA+20%(线上)买回"
        else:
            action, text = "wait", "空仓观望(价格在 200MA 与 500MA 之间,等站上 200MA 或跌破 500MA 恐慌线)"
        return {
            "id": "enhanced" if overheat else "plain",
            "name": ("200MA趋势 + 500MA恐慌抄底 + (收盘 > 200MA + 50%止盈，回落200MA + 20%买回)"
                     if overheat else "200MA趋势 + 500MA恐慌抄底"),
            "action": action, "action_text": text,
            "distances": _dist(action, overheat),
            "last_flip": last_flip,
        }

    today = pd.Timestamp.today().normalize()
    return {
        "ok": True,
        "ticker": "TQQQ",
        "as_of": str(last.date()),
        # 美股周末+短假 → >4 个自然日覆盖不过周五满周,超阈值即视为陈旧(频控事故哲学:披露不静默)
        "stale_days": int((today - last.normalize()).days),
        "px": round(px, 2),
        "px_chg_pct": round((px / px_prev - 1) * 100, 2),   # 昨日涨跌幅
        "ma200": round(m2, 2), "ma500": round(m5, 2),
        "px_ratio": round(px / m2, 2) if m2 else None,
        "overheat_sell_px": round(oh_sell, 2),
        "overheat_rebuy_px": round(oh_rebuy, 2),
        "strategies": [_snapshot(False), _snapshot(True)],
    }
