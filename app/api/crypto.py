"""加密总览页 API:主流币行情、恐惧贪婪指数、F&G × BTC 择时回测。

数据源:币安归档(parquet 本地)+ alternative.me 恐惧贪婪指数(FNG-INDEX parquet)。
回测复用向量化引擎(防前视铁律:t 日 F&G 信号 t+1 生效,next_open 成交)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from app.data import store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/crypto", tags=["crypto"])

# 币种清单与名称
COINS = {
    "BTC-USD": "比特币",
    "ETH-USD": "以太坊",
    "SOL-USD": "Solana",
    "BNB-USD": "BNB",
    "XRP-USD": "瑞波",
    "DOGE-USD": "狗狗币",
}
_CHANGE_SPANS = [("1d", 1), ("7d", 7), ("30d", 30), ("90d", 90), ("365d", 365)]


@router.get("/daily")
async def crypto_daily():
    """各币现价与多周期涨跌 + F&G 当前值(全部读本地 parquet,无外部请求)。"""
    from app.data.loaders import fng_loader
    items = []
    for t, name in COINS.items():
        df = store.read_ohlcv(t)
        if df is None or df.empty:
            continue
        px = df["Adj Close"].dropna()
        ch = {}
        for key, n in _CHANGE_SPANS:
            if len(px) > n:
                ch[key] = float(px.iloc[-1] / px.iloc[-1 - n] - 1.0)
        if not ch:
            continue
        items.append({"id": t, "name": name, "price": float(px.iloc[-1]),
                       "changes": ch, "last": str(px.index[-1].date())})
    fng = store.read_ohlcv("FNG-INDEX")
    fng_now = None
    if fng is not None and not fng.empty:
        s = fng["Adj Close"].dropna()
        fng_now = {"value": float(s.iloc[-1]),
                   "label": fng_loader.classification(float(s.iloc[-1])),
                   "as_of": str(s.index[-1].date())}
    items.sort(key=lambda x: x["price"], reverse=True)   # 价格降序仅作稳定排序
    return {"ok": True, "items": items, "fng": fng_now}


@router.get("/fng")
async def fng_history(days: int = Query(1095, ge=30, le=10000)):
    df = store.read_ohlcv("FNG-INDEX")
    if df is None or df.empty:
        return {"ok": False,
                "error": "F&G 数据缺失(cli.py sync --tickers FNG-INDEX 或管理页同步行情)"}
    s = df["Adj Close"].dropna().tail(days)
    return {"ok": True,
            "dates": [str(d.date()) for d in s.index],
            "values": [float(v) for v in s.values]}


@router.get("/fng-btc")
async def fng_btc_backtest(
    buy_below: int = Query(25, ge=0, le=100, description="F&G 低于该值建仓(极度恐惧买入)"),
    sell_above: int = Query(75, ge=0, le=100, description="F&G 高于该值清仓(极度贪婪卖出)"),
    fee_bps: float = Query(10, ge=0, le=100, description="每次换手单边成本"),
):
    """经典恐惧贪婪择时:F&G < buy_below 满仓 BTC;F&G > sell_above 清仓;
    中间区保持原状态(滞回)。与 BTC 买入持有同窗对照。"""
    import pandas as pd
    from app.backtest import engine, metrics

    btc = store.read_ohlcv("BTC-USD")
    fng = store.read_ohlcv("FNG-INDEX")
    if btc is None or btc.empty:
        return {"ok": False, "error": "BTC-USD 数据缺失"}
    if fng is None or fng.empty:
        return {"ok": False, "error": "FNG-INDEX 数据缺失"}
    if buy_below >= sell_above:
        raise ValueError("buy_below 必须小于 sell_above")

    px = btc[["Adj Close", "Open"]].dropna(subset=["Adj Close"])
    fng_s = fng["Adj Close"].reindex(px.index).ffill()

    # 滞回状态机
    state = 0.0
    rows = []
    for t, v in fng_s.items():
        if pd.isna(v):
            state = 0.0
        elif v < buy_below:
            state = 1.0
        elif v > sell_above:
            state = 0.0
        rows.append(state)
    weights = pd.DataFrame({"BTC-USD": rows}, index=px.index)

    prices = px[["Adj Close"]].rename(columns={"Adj Close": "BTC-USD"})
    prices_open = px[["Open"]].rename(columns={"Open": "BTC-USD"})
    cfg = engine.BacktestConfig(rebalance_cost_bps=fee_bps,
                                calendar="btc", execution_price="next_open")
    result = engine.run(prices, weights, cfg, prices_open=prices_open)
    perf = metrics.compute(result.nav, result.returns)
    # BTC 买入持有对照
    bench = engine.run_benchmark(prices, "BTC-USD", engine.BacktestConfig(
        calendar="btc", execution_price="next_open"), prices_open=prices_open)
    bench_perf = metrics.compute(bench.nav, bench.returns)

    n_trades = int((result.turnover > 0).sum())
    return {"ok": True,
            "params": {"buy_below": buy_below, "sell_above": sell_above, "fee_bps": fee_bps},
            "window": {"start": str(result.nav.index[0].date()),
                       "end": str(result.nav.index[-1].date()),
                       "years": perf.years},
            "strategy": {**perf.to_dict(), "final_value_10k": float(result.nav.iloc[-1])},
            "buy_hold": {**bench_perf.to_dict(), "final_value_10k": float(bench.nav.iloc[-1])},
            "n_trades": n_trades,
            "nav": {"dates": [str(d.date()) for d in result.nav.index],
                    "strategy": [round(float(v), 2) for v in result.nav.values],
                    "buy_hold": [round(float(v), 2) for v in bench.nav.loc[result.nav.index].values]},
            }
