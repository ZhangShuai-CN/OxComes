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


# 当日策略提醒的标的与策略配置(2026-10-11 双标的 → 同日加 SPY 三标的):
# - TQQQ 跟单形态:经典 200/500 + 独立锚200@1.5/1.1 过热(回调线 2026-10-11
#   由 1.2 改 1.1:分 regime 网格稳健改进,单边牛 +9.7pp/震荡牛 +4.5pp/
#   全窗 74.1%(+3.2pp),MDD 不变、调仓更少;rb=1.1 列系统性高于 1.2 列)
# - QQQ 共识版:牛熊线200 出场 + 恐慌线400 抄底 + 对牛熊线乖离20%止盈/10%回调
#   (2026-10-11 用户终拍:对称乖离档;历史档位参考——×1.3 近零触发=闲置,
#   ×1.18 扫描最优(全窗 15.37%)、×1.1 全窗 13.16%/2010后 18.90%)
# - SPY 共识版(2026-10-11 网格研究):牛熊线200 + 恐慌线850 + 乖离15/10。
#   SPY 拉伸结构比 QQQ 窄一档(历史最大乖离 ×1.205,×1.2 以上仅 5 天),过热
#   线按 15% 定档才有触发深度;恐慌线 850=3.4 年深熊抄底线,增益全在慢熊
#   防守(2008 -26.2% vs 死拿 -39.4%、2022 -7.2% vs -20.0%)。
SIGNAL_CONFIGS = [
    {
        "ticker": "TQQQ", "label": "跟单 · 3x 经典",
        "params": {"ma_trend": 200, "ma_panic": 500, "oh_anchor": 200,
                   "oh_sell_mult": 1.5, "oh_rebuy_mult": 1.1, "overheat": "on"},
    },
    {
        "ticker": "QQQ", "label": "共识版 · 牛熊线200/恐慌线400/乖离20/10",
        "params": {"ma_trend": 200, "ma_panic": 400, "oh_anchor": 200,
                   "oh_sell_mult": 1.2, "oh_rebuy_mult": 1.1, "overheat": "on"},
    },
    {
        "ticker": "SPY", "label": "共识版 · 牛熊线200/恐慌线850/乖离15/10",
        "params": {"ma_trend": 200, "ma_panic": 850, "oh_anchor": 200,
                   "oh_sell_mult": 1.15, "oh_rebuy_mult": 1.10, "overheat": "on"},
    },
]


def _signal_snapshot(cfg: dict) -> dict:
    """单标的策略快照:真实键数据 + 四线计算 + 状态机信号 + 距离文案 + K 线 payload。

    数据只取本地真实序列(腾讯源,当前价=实际市价),坚决不用 SYNTH 拼接序列
    (其绝对值是拼接常数缩放的连续口径,≠市价,用户无法在券商端核对)。
    无真实数据直接报错,不降级不占位。
    """
    import pandas as pd

    from app.backtest.strategies import get_strategy
    from app.data import store

    ticker, p = cfg["ticker"], cfg["params"]
    trend_n, panic_n, anchor_n = int(p["ma_trend"]), int(p["ma_panic"]), int(p["oh_anchor"])
    sell_m, rebuy_m = float(p["oh_sell_mult"]), float(p["oh_rebuy_mult"])

    df = store.read_ohlcv(ticker)
    if df is None or df.empty:
        return {"ok": False, "ticker": ticker, "error": f"无本地真实 {ticker} 数据——先运行同步"}
    need = max(trend_n, panic_n, anchor_n) + 1
    if len(df) < need:
        return {"ok": False, "ticker": ticker,
                "error": f"本地 {ticker} 仅 {len(df)} 根,不足最长均线 {need - 1} 日"}

    px_series = df["Adj Close"]
    ma200 = px_series.rolling(trend_n, min_periods=trend_n).mean()
    ma500 = px_series.rolling(panic_n, min_periods=panic_n).mean()
    ma_anchor = px_series.rolling(anchor_n, min_periods=anchor_n).mean()
    last = df.index[-1]
    px, m2, m5 = float(px_series.iloc[-1]), float(ma200.iloc[-1]), float(ma500.iloc[-1])
    px_prev = float(px_series.iloc[-2])
    m_anchor = float(ma_anchor.iloc[-1])
    oh_sell, oh_rebuy = m_anchor * sell_m, m_anchor * rebuy_m   # 标量(距离文案/信号判断用)

    # 信号状态机(overheat=on):w>0 即「在场」。翻转日 t 收盘确认信号 → t+1 开盘成交。
    # 参数与四线计算同源显式传齐,默认值将来变化不影响提醒面板口径
    stg = get_strategy("ma200_panic")()
    prices = px_series.to_frame(ticker)
    out = stg.build(prices, {"signal_ticker": ticker, "holding_ticker": ticker, **p})
    w = out.weights[ticker].round(6)
    w_now, w_prev = bool(w.iloc[-1] > 0), bool(w.iloc[-2] > 0)
    chg = (w != w.shift(1)) & w.shift(1).notna()
    flips = [(d, "buy" if bool(w.loc[d] > 0) else "sell") for d in w.index[chg]]
    last_flip = (str(flips[-1][0].date()), "在场" if w.iloc[-1] > 0 else "空仓") \
        if flips else None

    # 动作分类:对「空仓跟随者」与「已在场持有者」分别给出次一开盘指令
    if w_now and not w_prev:
        action, text = "buy", "进场信号已确认 → 次一交易日开盘买入(在场者忽略)"
    elif w_now and w_prev:
        action, text = "hold", "持有,无需动作"
    elif w_now is False and w_prev:
        if px > oh_sell:
            action, text = "sell", "过热止盈信号(收盘越过过热线)→ 次一交易日开盘卖出,等回落到过热回调线买回"
        else:
            action, text = "sell", "离场信号(收盘跌破趋势线)→ 次一交易日开盘卖出"
    elif px > oh_sell:
        action, text = "wait", "过热等待中,等回落到过热回调线(趋势线上方)买回"
    else:
        action, text = "wait", "空仓观望(价格在趋势线与恐慌线之间,等站上趋势线或跌破恐慌线抄底)"

    # 关键距离:按「当前价格位置 × 策略状态」列出到各线还差的价格变动%
    distances: list[dict] = []

    def add(label: str, target: float, note: str = ""):
        distances.append({"label": label, "pct": round((target / px - 1) * 100, 1),
                          "target": round(target, 2), "note": note})

    in_panic = px < m5
    if action in ("hold", "buy"):          # 在场(或新进场):上行防过热,下行防破位
        add("涨到过热线止盈卖出", oh_sell)
        add("跌到过热回调线", oh_rebuy, "仅过热止盈卖出后生效")
        add("跌到趋势线破位卖出", m2)
    elif action == "wait" and px > oh_sell:  # 过热等待:等回落到买回带
        add("跌到过热回调线买回", oh_rebuy)
        add("跌回趋势线(等待作废,恢复普通空仓)", m2)
    elif action == "wait":                  # 空仓观望带:两条进场路
        add("涨到趋势线上方买入", m2)
        add("跌到恐慌线抄底买入", m5)
    elif action == "sell" and px > oh_sell:  # 过热止盈卖出:等回落接回
        add("跌到过热回调线买回", oh_rebuy)
        add("跌回趋势线(等待作废,恢复普通空仓)", m2)
    elif action == "sell":                  # 破趋势线卖出:两条重新进场路
        add("涨回趋势线上方买入", m2)
        if not in_panic:
            add("跌到恐慌线抄底买入", m5)

    today = pd.Timestamp.today().normalize()

    # K 线窗口(策略提醒蜡烛图):尾部 240 根 ≈ 1 年。均线沿用全史 rolling 序列
    # 切尾而非窗口内重算,窗口首日的均线值即全史口径,与信号判定一致。
    n = 240
    win = df.iloc[-n:]
    tail = lambda s: [round(float(v), 2) if pd.notna(v) else None
                      for v in s.iloc[-n:]]
    kline = [[o, h, l, c] for o, h, l, c in
             zip(win["Open"].round(2), win["High"].round(2),
                 win["Low"].round(2), win["Close"].round(2))]

    return {
        "ok": True,
        "ticker": ticker,
        "label": cfg["label"],
        "as_of": str(last.date()),
        # 美股周末+短假 → >4 个自然日覆盖不过周五满周,超阈值即视为陈旧(频控事故哲学:披露不静默)
        "stale_days": int((today - last.normalize()).days),
        "px": round(px, 2),
        "px_chg_pct": round((px / px_prev - 1) * 100, 2),   # 昨日涨跌幅
        "ma200": round(m2, 2), "ma500": round(m5, 2),
        "px_ratio": round(px / m2, 2) if m2 else None,
        "overheat_sell_px": round(oh_sell, 2),
        "overheat_rebuy_px": round(oh_rebuy, 2),
        "strategies": [{
            "name": "MA趋势 + MA恐慌抄底 + 过热止盈",
            "action": action, "action_text": text,
            "distances": distances,
            "last_flip": last_flip,
        }],
        "chart": {
            "dates": [str(d.date()) for d in win.index],
            "kline": kline,
            "ma200": tail(ma200), "ma500": tail(ma500),
            "oh_sell": tail(ma_anchor * sell_m), "oh_rebuy": tail(ma_anchor * rebuy_m),
            # 四线名称(K线图 legend/端部标签/tooltip 共用;参数变了改这里即可)
            "line_labels": {
                "ma200": f"趋势线 MA{trend_n}", "ma500": f"恐慌线 MA{panic_n}",
                "oh_sell": f"过热线 MA{anchor_n}×{sell_m}",
                "oh_rebuy": f"过热回调线 MA{anchor_n}×{rebuy_m}",
            },
            # 买卖信号打点(▲买/▼卖):翻转日 = 信号收盘确认日;窗口外丢弃
            "flips": [{"date": str(d.date()), "type": t}
                      for d, t in flips if d >= win.index[0]],
        },
    }


@router.get("/ma_signal")
async def ma_signal():
    """当日策略提醒(多标的):ma200_panic 过热增强形态(overheat=on)的最新信号快照。

    语义 = 每日收盘后的执行指令(引擎 next_open 口径):t 收盘信号 → t+1 开盘
    成交。空仓跟随者按「在场」信号于次一交易日开盘买入。标的清单见
    SIGNAL_CONFIGS(TQQQ 跟单形态 + QQQ/SPY 共识版),单标的缺数据只隐藏该卡并
    披露原因,不影响其他标的。
    """
    signals = [_signal_snapshot(c) for c in SIGNAL_CONFIGS]
    if not any(s.get("ok") for s in signals):
        return {"ok": False, "error": "所有标的均无本地真实数据——先运行同步;提醒面板不使用拼接序列"}
    return {"ok": True, "signals": signals}
