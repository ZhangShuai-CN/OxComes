"""加密总览页 API:主流币行情、恐惧贪婪指数、F&G × BTC 择时回测。

数据源:币安归档(parquet 本地)+ alternative.me 恐惧贪婪指数(FNG-INDEX parquet)。
回测复用向量化引擎(防前视铁律:t 日 F&G 信号 t+1 生效,next_open 成交)。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query

import pandas as pd

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


# ── BTC 逃顶/抄底指标(2026-10-12):coinglass bull-market-peak-signals 系的
#    「纯价格均线族」——本地 BTC(2017-08 起)即可复现;MVRV/NUPL 等链上指标
#    需 realized cap(无大陆可达免费源,不造数)。

def _btc_price():
    btc = store.read_ohlcv("BTC-USD")
    if btc is None or btc.empty:
        return None
    return btc[["Adj Close", "Open"]].dropna(subset=["Adj Close"])


@router.get("/peak-indicators")
async def peak_indicators():
    """BTC 逃顶/抄底信号灯:AHR999(=price/MA200,Mayer 多倍数同型)、Pi 周期距
    (111MA/2×350MA)、price/200 周均线、price/2 年均线(五倍规则)。当前值+参考
    区间+档位;均线未成形(数据 2017-08 起)的指标返回说明。"""
    import math
    px = _btc_price()
    if px is None:
        return {"ok": False, "error": "BTC-USD 数据缺失"}
    close = px["Adj Close"]
    ma200 = close.rolling(200).mean()
    ma111 = close.rolling(111).mean()
    ma350 = close.rolling(350).mean()
    ma200w = close.rolling(200 * 7).mean()
    ma2y = close.rolling(730).mean()
    cur = float(close.iloc[-1])

    def val(v):
        return round(float(v), 2) if pd.notna(v) else None

    ahr = val(cur / ma200.iloc[-1])
    pi = val(ma111.iloc[-1] / (2 * ma350.iloc[-1]))
    r200w = val(cur / ma200w.iloc[-1])
    r2y = val(cur / ma2y.iloc[-1])

    items = [
        {"key": "ahr999", "name": "AHR999 / Mayer 倍数", "formula": "价格 / MA200",
         "value": ahr, "as_of": str(close.index[-1].date()),
         "zones": "<0.45 抄底(九神) · 0.45~1.2 定投区 · >2.4 顶部带(Mayer)",
         **_zone(ahr, lo=0.45, hi=1.2, top=2.4)},
        {"key": "pi_cycle", "name": "Pi 周期距", "formula": "111日均线 / (2×350日均线)",
         "value": pi, "as_of": str(close.index[-1].date()),
         "zones": "≥1 = 周期顶信号(111DMA 上穿 2×350DMA)",
         "state": ("顶部信号!" if (pi or 0) >= 1 else "未触发") if pi is not None else "均线未成形",
         "cls": "neg" if (pi or 0) >= 1 else ""},
        {"key": "ma200w", "name": "价格 / 200 周均线", "formula": "价格 / MA1400d",
         "value": r200w, "as_of": str(close.index[-1].date()),
         "zones": "<1 深熊抄底区(历次熊底触线;近年底部抬升参考价值下降) · 1~2 正常 · >2 偏热 · >3 泡沫",
         **_zone(r200w, lo=1.0, hi=2.0, top=3.0)},
        {"key": "ma2y", "name": "价格 / 2 年均线(五倍规则)", "formula": "价格 / MA730d",
         "value": r2y, "as_of": str(close.index[-1].date()),
         "zones": "<1 抄底区 · 1~5 持有 · >5 逃顶(zipsec 规则)",
         **_zone(r2y, lo=1.0, hi=5.0, top=5.0)},
    ]
    return {"ok": True, "as_of": str(close.index[-1].date()), "items": items,
            "note": "MVRV/NUPL 等链上指标需 realized cap,无大陆可达免费源,不造数据。"}


def _zone(color, lo, hi, top):
    """价格相对度→档位(inv=True:低=便宜绿)。"""
    if color is None:
        return {"state": "均线未成形(数据 2017-08 起)", "cls": ""}
    if color < lo:
        return {"state": "抄底区", "cls": "pos"}
    if color > top:
        return {"state": "顶部区", "cls": "neg"}
    if color > hi:
        return {"state": "偏热", "cls": "warn"}
    return {"state": "中性持有区", "cls": ""}


@router.get("/ahr-btc")
async def ahr_btc_backtest(
    sell_mult: float = Query(2.4, ge=0.5, le=8, description="乖离>该值清仓(逃顶)"),
    buy_mult: float = Query(0.6, ge=0.1, le=3, description="乖离<该值满仓(抄底)"),
    fee_bps: float = Query(10, ge=0, le=100, description="每次换手单边成本"),
):
    """AHR999/Mayer 乖离滞回(price/MA200):>sell_mult 清仓逃顶,<buy_mult 抄底
    满仓,中间保持。2026-10-12 实测:S2.4/B0.6 于 2017-08→2026-10 得 55.4% 年化
    /55x(持币 38.2%/18.3x),两档恰为 Mayer 顶部带(>2.4)与九神抄底区(≈0.45-0.6)。
    ⚠ B 档单事件敏感:0.6 与 0.8 的天量差异主要来自 2022 接刀位置(过拟合警示)。"""
    import pandas as pd
    from app.backtest import engine, metrics

    px = _btc_price()
    if px is None:
        return {"ok": False, "error": "BTC-USD 数据缺失"}
    if buy_mult >= sell_mult:
        raise ValueError("buy_mult 必须小于 sell_mult")
    close = px["Adj Close"]
    ratio = (close / close.rolling(200).mean()).dropna()
    px = px.loc[ratio.index]                       # MA200 预热完的窗口
    r = ratio.loc[px.index]

    state, rows = 0.0, []
    for v in r.values:
        if v < buy_mult:
            state = 1.0
        elif v > sell_mult:
            state = 0.0
        rows.append(state)
    weights = pd.DataFrame({"BTC-USD": rows}, index=px.index)

    prices = px[["Adj Close"]].rename(columns={"Adj Close": "BTC-USD"})
    prices_open = px[["Open"]].rename(columns={"Open": "BTC-USD"})
    cfg = engine.BacktestConfig(rebalance_cost_bps=fee_bps, calendar="btc",
                                execution_price="next_open")
    result = engine.run(prices, weights, cfg, prices_open=prices_open)
    perf = metrics.compute(result.nav, result.returns)
    bench = engine.run_benchmark(prices, "BTC-USD", engine.BacktestConfig(
        calendar="btc", execution_price="next_open"), prices_open=prices_open)
    bench_perf = metrics.compute(bench.nav, bench.returns)
    n_trades = int((result.turnover > 0).sum())
    return {"ok": True,
            "params": {"sell_mult": sell_mult, "buy_mult": buy_mult, "fee_bps": fee_bps},
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
