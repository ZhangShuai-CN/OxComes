"""回测编排：数据准备（含 TQQQ 合成替换）→ 策略 → 引擎 → 指标。

CLI（cli.py backtest）与 API（/api/backtest/run）共用本模块。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from app.backtest import engine, metrics
from app.backtest import params as params_mod
from app.backtest.strategies import get_strategy, list_strategies
from app.data import store, synthetic
from app.data.universe import CORE_UNIVERSE

logger = logging.getLogger(__name__)


@dataclass
class BacktestRun:
    ok: bool
    nav: Optional[pd.Series] = None
    ret: Optional[pd.Series] = None
    perf: Optional[dict] = None            # metrics.to_dict()
    bench_navs: dict = field(default_factory=dict)     # {benchmark: Series}
    bench_perf: dict = field(default_factory=dict)     # {benchmark: metrics}
    notes: list = field(default_factory=list)
    strategy_id: str = ""
    params: dict = field(default_factory=dict)
    tickers: list = field(default_factory=list)
    start: str = ""
    end: str = ""
    error: Optional[str] = None

    def to_api_payload(self) -> dict:
        if not self.ok:
            return {"ok": False, "error": self.error}
        return {
            "ok": True,
            "strategy_id": self.strategy_id,
            "params": self.params,
            "tickers": self.tickers,
            "start": self.start,
            "end": self.end,
            "notes": self.notes,
            "nav": {"dates": [str(d.date()) for d in self.nav.index],
                    "values": [round(float(v), 2) for v in self.nav.values]},
            "perf": self.perf,
            "benchmarks": {
                name: {
                    "nav": {"dates": [str(d.date()) for d in s.index],
                            "values": [round(float(v), 2) for v in s.values]},
                    "perf": self.bench_perf.get(name, {}),
                } for name, s in self.bench_navs.items()
            },
        }


def ensure_core_data(tickers: list[str] | None = None) -> tuple[bool, str]:
    """确保核心行情本地可用（无则现场拉全量）。"""
    from app.data.loaders import yfinance_loader
    targets = tickers or CORE_UNIVERSE
    missing = [t for t in targets if store.last_date(t) is None]
    if not missing:
        return True, "本地数据齐全"
    try:
        data = yfinance_loader.download_ohlcv(missing)
    except Exception as e:
        return False, f"下载失败: {e}"
    for t, df in data.items():
        store.save_ohlcv(t, df)
    still = [t for t in missing if store.last_date(t) is None]
    if still:
        return False, f"仍缺失: {', '.join(still)}"
    return True, f"已下载 {len(data)} 个标的"


def _synth_health_ok() -> bool:
    """SYNTH_TQQQ 拼接序列健康检查：覆盖非空、无 NaN/溢出值、与真实 TQQQ 同步更新。"""
    df = store.read_ohlcv(synthetic.SYNTH_SPY_ID)
    if df is None or df.empty:
        return False
    if df["Adj Close"].isna().any():
        return False           # NaN 污染（2026-10-09 实测过的第二类事故形态）
    adj = df["Adj Close"].dropna()
    if adj.empty or not adj.gt(0).all():
        return False
    mx = float(adj.max())
    if not (1e-8 < mx < 1e6):       # 历史上曾出现 3e38 溢出垃圾值
        return False
    # 陈旧度：应与真实 TQQQ 数据水位线同步（落后 >60 天视为停更需重建）
    real = store.read_ohlcv("TQQQ")
    if real is not None and not real.empty:
        if adj.index[-1] < real.index[-1] - pd.Timedelta(days=60):
            return False
    # 关键日期必须有覆盖（TQQQ 上市日起）
    return adj.index[0] <= synthetic.TQQQ_REAL_START


def prepare_prices(
    tickers: list[str],
    start: Optional[str],
    end: Optional[str],
    use_synth_tqqq: str = "auto",
) -> "tuple[pd.DataFrame, Optional[pd.DataFrame], list[str]]":
    """组装复权价格矩阵。返回 (prices_close, prices_open|None, data_notes)。

    prices_open 供引擎 next_open 撮合（各标的 Open 列，与 close 矩阵同形）；无 Open
    数据的标的列以 NaN 呈现，引擎段收益按 skipna 处理（转 0）。
    TQQQ 替换逻辑（use_synth_tqqq）：
      'auto'  : 请求起点早于本地真实 TQQQ 数据首日（2011-01-26，上游免费源的
                共同缺口）→ 改用拼接序列（早期段由 QQQ×3 模型合成，列名仍为 TQQQ）
      'always': 强制使用拼接序列
      'never' : 只用真实 TQQQ（基准对照等小窗口场景）
    """
    start_ts = pd.to_datetime(start) if start else None
    notes: list[str] = []
    cols, cols_open = {}, {}
    tqqq_real_first = None
    if "TQQQ" in tickers:
        real = store.read_ohlcv("TQQQ")
        if real is not None and not real.empty:
            tqqq_real_first = real.index[0]
    for t in tickers:
        src = t
        if t == "TQQQ" and use_synth_tqqq != "never":
            need = (start_ts is None
                    or (tqqq_real_first is not None and start_ts < tqqq_real_first)
                    or start_ts < synthetic.TQQQ_REAL_START)
            want_synth = use_synth_tqqq == "always" or (
                use_synth_tqqq == "auto" and need)
            if want_synth and _synth_health_ok():
                src = synthetic.SYNTH_SPY_ID
                notes.append(
                    f"ℹ TQQQ 本地真实数据从 {tqqq_real_first.date() if tqqq_real_first is not None else '无'} 起；"
                    f"{synthetic.TQQQ_REAL_START.date()} → 该日的早期段使用 QQQ×3 模型合成的拼接序列 "
                    f"(SYNTH_TQQQ，重叠期已通过校验)。"
                )
        df = store.read_ohlcv(src, start=start, end=end)
        if df is None:
            df = store.read_ohlcv(t, start=start, end=end)
        if df is None:
            raise ValueError(f"{t} 无本地数据（先运行: python cli.py sync）")
        cols[t] = df["Adj Close"]
        cols_open[t] = df["Open"] if "Open" in df.columns else None
        # 请求窗口比实际数据大时披露（不允许静默缩窗）
        if start_ts is not None and df.index[0] > start_ts:
            notes.append(f"⚠ {t} 本地数据从 {df.index[0].date()} 起，回测起点已从 {start} 顺延。")
    prices = pd.DataFrame(cols).dropna(how="all")
    if prices.empty:
        raise ValueError("指定区间无数据")
    opens = pd.DataFrame({
        t: (s.reindex(prices.index) if s is not None else pd.Series(np.nan, index=prices.index))
        for t, s in cols_open.items()
    }, index=prices.index)[prices.columns]
    has_open = opens.notna().any().any()
    prices_open = opens if has_open else None
    return prices, prices_open, notes


def run_backtest(
    strategy_id: str,
    params: dict,
    tickers: list[str],
    start: Optional[str] = None,
    end: Optional[str] = None,
    benchmarks: Optional[list[str]] = None,
    initial_capital: float = 10_000.0,
    rebalance_cost_bps: float = 2.0,
    use_synth_tqqq: str = "auto",
    allow_leverage: bool = False,
    financing_mode: Optional[str] = None,
    execution_price: str = "next_open",     # close | next_open（外站口径,默认）
    prewarm: bool = True,                   # 均线全历史预热（外部回测站口径,默认开）
) -> BacktestRun:
    """完整跑一次回测（含基准对照）。出错返回 ok=False + error，不抛异常。"""
    run = BacktestRun(
        ok=False, strategy_id=strategy_id, params=dict(params or {}),
        tickers=list(tickers), start=str(start or ""), end=str(end or ""),
    )
    try:
        strategy = get_strategy(strategy_id)

        # 默认日期口径:不填 start 时,TQQQ 的默认起点不早于 QQQ 上市日(1999-03-10)。
        # SYNTH 拼接 1991-1999 段是「NDX 指数桥接 × 3x 模型」的双重合成(置信度低),
        # 想做泡沫期压力测试请显式填更早日期(页面日期框可以手改)。
        effective_start = start
        if effective_start is None and "TQQQ" in tickers:
            effective_start = synthetic.QQQ_LISTING.strftime("%Y-%m-%d")

        # 合成序列可能需要重建（缺失/损坏/停更 60 天以上时强制重建）
        if "TQQQ" in tickers:
            start_ts = pd.to_datetime(effective_start) if effective_start else pd.Timestamp("1900-01-01")
            real_tqqq = store.read_ohlcv("TQQQ")
            real_first = real_tqqq.index[0] if real_tqqq is not None and not real_tqqq.empty else None
            need_synth = use_synth_tqqq == "always" or (
                use_synth_tqqq == "auto"
                and (start_ts < synthetic.TQQQ_REAL_START
                     or (real_first is not None and start_ts < real_first))
            )
            if need_synth and not _synth_health_ok():
                cfg = synthetic.SyntheticConfig()
                if financing_mode in (None, "auto"):
                    pass
                elif str(financing_mode).replace('.', '', 1).isdigit():
                    cfg.financing_mode = "fixed"
                    cfg.fixed_financing_rate = float(financing_mode)
                if synthetic.build_spliced(cfg) is None:
                    raise ValueError(
                        "TQQQ 早期段需要拼接序列，但重建失败（QQQ/^IRX/TQQQ 数据不全）。"
                        "已存在的旧拼接序列若仍可用会自动降级使用。"
                    )
            if store.read_ohlcv("TQQQ") is None:
                ensure_core_data(["TQQQ"])

        clean_params = params_mod.validate_params(strategy, params or {})
        run.params = clean_params

        # 单主标的体验:策略内部依赖的辅助资产(信号/防御/权重标的)自动并入矩阵
        merged_tickers = list(dict.fromkeys(
            [t.upper() for t in tickers] + _extra_assets(clean_params)))

        # 均线全历史预热(默认开,对齐外部回测站口径):指标/均线计算的数据起点
        # 比账户窗口提前「最大回看窗 × 7/5」自然日,窗口起点当天均线已就绪;
        # 净值与全部统计仍从窗口起点起算(=窗口起点时信号已在场,nav 切片计价)。
        # 老哲学保留:默认不主动吃进 1991-1999 的 NDX 双重桥接段 —— 预热起点
        # clamp 到 QQQ 上市日;用户显式填早于上市日的窗口视为授权,不再 clamp。
        prewarm_n = int(strategy().warmup_days(clean_params)) if prewarm else 0
        warm_start = effective_start
        if prewarm_n > 0 and effective_start is not None:
            floor = synthetic.QQQ_LISTING
            warm_ts = pd.to_datetime(effective_start) - pd.Timedelta(
                days=int(prewarm_n * 7 / 5) + 7)
            if warm_ts < floor and pd.to_datetime(effective_start) >= floor:
                warm_ts = floor
            warm_start = warm_ts.strftime("%Y-%m-%d")
        prices, prices_open, data_notes = prepare_prices(
            merged_tickers, warm_start, end, use_synth_tqqq)

        # 公平性对齐:窗口末端截到主标的的数据末日(混合矩阵中辅助/基准资产可能
        # 有更新鲜的数据 —— 如 QQQ 到 10-08 而 TQQQ 只到 10-07 —— 让基准比主策略
        # 多吃一天行情是错误对照,统一截齐 + 披露)
        main_col = tickers[0] if tickers and tickers[0] in prices.columns else prices.columns[0]
        main_series = prices[main_col].dropna() if main_col in prices.columns else None
        if main_series is not None and len(main_series) and main_series.index[-1] < prices.index[-1]:
            cut = main_series.index[-1]
            prices = prices.loc[:cut]
            if prices_open is not None:
                prices_open = prices_open.loc[:cut]
            data_notes.append(
                f"ℹ 窗口终点已对齐主标的 {main_col} 数据末日 {cut.date()}"
                f"(混合矩阵中其他资产有更晚数据,为公平对照截齐)。"
            )
        effective_end = str(prices.index[-1].date())
        if effective_start != start and start is None:
            data_notes.append(
                f"ℹ 未填起始日期,默认从 QQQ 上市日 {synthetic.QQQ_LISTING.date()} 开始"
                "(TQQQ 拼接序列的 1991-1999 段为 NDX 双重桥接,显式填写更早日期即可解锁)。"
            )
        if not start:
            run.start = str(prices.index[0].date())
        if not end or prices.index[-1].date().isoformat() != (end or ""):
            run.end = effective_end

        built = strategy().build(prices, clean_params)

        # 预热正确语义：均线用窗口前的历史数据计算（指标热身），但**预热段不建仓** ——
        # 权重硬置零,账户在窗口起点当天按指标的当前状态进出场(w[t0]=state(t0) 生效于
        # t0+1 开盘)。此前「预热段照常交易、nav 切片回窗口」的实现让结果依赖预热起点的
        # 选取（2026-10-10 实测:5×30 窗口倍数 35.9x vs 23.2x,信号序列完全相同）,
        # rolling 指标的数学等价性要求结果与预热起点无关。
        if prewarm_n > 0 and effective_start is not None:
            pre_mask = built.weights.index < pd.to_datetime(effective_start)
            if pre_mask.any():
                built.weights.loc[pre_mask] = 0.0

        cfg = engine.BacktestConfig(
            initial_capital=initial_capital,
            rebalance_cost_bps=rebalance_cost_bps,
            allow_leverage=allow_leverage,
            execution_price=execution_price,
        )
        if execution_price == "next_open" and prices_open is None:
            # 本地数据缺 Open 列（不常见）：自动降级 close 并披露，避免静默口径漂移
            cfg.execution_price = "close"
            data_notes.append("⚠ 无本地开盘价数据，本次回测自动降级为收盘成交口径。")
        result = engine.run(prices, built.weights, cfg,
                            cashflows=built.cashflows, prices_open=prices_open)

        # 预热段仓位已硬置零:矩阵前段(窗口前)策略空仓,nav 从窗口起点才随信号变化。
        # 净值/收益切回用户窗口(分母=窗口首日 nav,即资金起点)
        if prewarm_n > 0 and effective_start is not None:
            win_ts = pd.to_datetime(effective_start)
            if result.nav.index[0] < win_ts:
                result.nav = result.nav.loc[win_ts:]
                result.returns = result.returns.loc[win_ts:]
                data_notes.append(
                    f"ℹ 均线全历史预热：均线自窗口前数据计算(提前约 {prewarm_n} 个交易日,"
                    f"指标起点 {prices.index[0].date()})，账户从 {result.nav.index[0].date()} "
                    "按当日信号状态进场（结果与预热起点无关）。"
                )
                if not start:
                    run.start = str(result.nav.index[0].date())
        perf = compute_metrics(result, prices)
        run.ok, run.nav, run.ret, run.perf = True, result.nav, result.returns, perf
        run.notes = list(built.notes) + list(data_notes)

        # 多标的默认等权=每日再平衡的组合语义披露(用户两次踩坑,强制弹出)
        if strategy_id in ("buy_hold", "dca") and len(tickers) > 1:
            run.notes.insert(0,
                f"⚠ 本组合 = {len(tickers)} 标的等权 + 每日再平衡（引擎按固定权重日结），"
                "收益被几何摊薄。对照外部网站的「单一 TQQQ」数字请只填一个标的。")

        # 定投入金构成披露(期初资金是构成之一,防"倍数分母"歧义)
        if strategy_id == "dca" and result.cashflows is not None:
            n_cf = int(result.cashflows.ne(0).sum())
            per = float(clean_params.get("amount") or 500)
            run.notes.append(
                f"ℹ 入金构成: 期初 ${cfg.initial_capital:,.0f}(起始日一次性投入)"
                f" + 定投 {n_cf} 期 × ${per:,.0f} = 总投入 ${result.invested:,.0f}。"
                "期末/总投入即倍数分母;想只跑纯定投,把「初始资金」设为 0。"
            )

        for b in benchmarks or []:
            try:
                # 基准是买入持有(无指标依赖),从用户窗口起点直接起跑,无需预热
                b_prices, b_open, b_notes = prepare_prices([b], effective_start, effective_end, use_synth_tqqq="auto")
                b_cfg = dict(cfg.__dict__)
                b_cfg.update(rebalance_cost_bps=0.0)
                # 基准镜像主策略资金流:定投主策略 → 基准同额同期定投(apples-to-apples)
                b_result = engine.run_benchmark(b_prices, b, engine.BacktestConfig(**b_cfg),
                                                prices_open=b_open,
                                                cashflows=result.cashflows)
                m = compute_metrics(b_result, b_prices)
                clean = b.replace("^", "_").replace("-", "_").replace("=", "")
                run.bench_navs[clean] = b_result.nav
                run.bench_perf[clean] = m
                run.notes.extend(b_notes)
            except Exception as e:
                logger.warning("基准 %s 失败: %s", b, e)
    except Exception as e:
        run.error = str(e)
        logger.exception("回测失败")
    return run


def compute_metrics(result: engine.BacktestResult, prices: pd.DataFrame) -> dict:
    """Sharpe 用的无风险利率：本地 ^IRX 均值；无数据则 0。"""
    rf = 0.0
    irx = store.read_ohlcv("^IRX")
    if irx is not None and not irx.empty:
        lo = result.nav.index[0]
        hi = result.nav.index[-1]
        seg = irx["Adj Close"][(irx.index >= lo) & (irx.index <= hi)]
        if not seg.empty and seg.mean() > 0:
            rf = float(seg.mean()) / 100.0
    has_cf = result.cashflows is not None
    perf = metrics.compute(result.nav, result.returns, rf_annual=rf,
                           invested=result.invested if has_cf else None,
                           cashflows=result.cashflows if has_cf else None)
    return perf.to_dict()


def _extra_assets(clean_params: dict) -> list[str]:
    """策略声明但不在 tickers 里的辅助资产(信号/防御/权重标的)自动注入价格矩阵。

    UI 已改为单主标的;ma200_panic / ma_cross 的
    signal_ticker=QQQ、holding_ticker=TQQQ 等参数资产需要矩阵里有数据列 —
    在此统一收集,用户无需手动填写多个标的。
    """
    extras: set[str] = set()
    for k, v in (clean_params or {}).items():
        if k.endswith("_ticker") and isinstance(v, str) and v.strip():
            # "现金"类哨兵值(留空=现金)没有实质资产
            if v.strip().upper() not in ("", "CASH", "CASH_"):
                extras.add(v.strip().upper())
    return sorted(extras)


def strategy_catalog() -> list[dict]:
    return list_strategies()
