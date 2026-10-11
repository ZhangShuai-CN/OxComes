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
    trades: list = field(default_factory=list)         # list[engine.TradeEvent]（已按窗口过滤）
    n_rebalance_days: int = 0              # 总交易次数口径：有成交发生的交易日数
    n_buys: int = 0                        # 逐笔买入笔数（不含定投入金）
    n_sells: int = 0                       # 逐笔卖出笔数
    sibling: Optional["BacktestRun"] = None  # overheat=on/band 配跑：同窗口 overheat=off 基线（前端策略内对照）
    band_policy: Optional[dict] = None  # overheat=on 配跑：同参 overheat=band(快进快出)变体的精简 payload
    error: Optional[str] = None

    TRADES_CAP = 20_000                    # payload 安全阀（最坏 DCA 周定投 30 年 ≈1561 笔）

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
            "n_rebalance_days": self.n_rebalance_days,
            "n_buys": self.n_buys,
            "n_sells": self.n_sells,
            "trades": [t.to_dict() for t in self.trades[:self.TRADES_CAP]],
            "trades_truncated": len(self.trades) > self.TRADES_CAP,
            # 过热增强的基线对照（overheat=off 同窗口重跑；主策略未开增强时为 None）
            "baseline_policy": (self.sibling.to_api_payload()
                                if self.sibling is not None and self.sibling.ok else None),
            # 「一个开关跑双形态」:overheat=on 时同参配跑 band(快进快出)变体
            "band_policy": self.band_policy,
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

        # 均线全历史预热(默认开,对齐外部回测站口径)。全程精确、零估算:
        # ① 此处:在主标的本地全史 index 上按交易日回数 prewarm_n 根,精确得出
        #    向 prepare_prices 要料的起点(读多少原料)。曾经用自然日换算公式
        #    估原料上界(500×7/5+7=707 天仅折 489 根)造成 MA500 迟到 11 个
        #    交易日、恐慌分支静默失效 —— 2026-10-10 物理删除,精确量必须精确算。
        # ② build 前:以矩阵真实 index 再精确对齐并裁剪、不足则硬披露(矩阵是
        #    多标的 union 日历,可能与单标的探测日历有微差,最终权威以矩阵为准;
        #    两段方向一致、宁多勿少,永不产生静默缺口)。
        # 老哲学保留:窗口起点 ≥ QQQ 上市日时,预热不吃 1991-1999 的 NDX 双重
        # 桥接段(floor = QQQ 上市日);用户显式填更早的窗口视为授权,不再 clamp。
        prewarm_n = int(strategy().warmup_days(clean_params)) if prewarm else 0
        probe_start = effective_start
        if prewarm_n > 0 and effective_start is not None and tickers:
            t0 = tickers[0].upper()
            # 探测源模拟 prepare_prices 的替换决策:可能需要早期段时用 SYNTH
            # 拼接的全史日历(index ⊇ 真实段,回数宁多勿少方向安全),否则真实
            probe = None
            if t0 == "TQQQ" and use_synth_tqqq != "never" and _synth_health_ok():
                probe = store.read_ohlcv(synthetic.SYNTH_SPY_ID)
            if probe is None or probe.empty:
                probe = store.read_ohlcv(t0)
            if probe is not None and not probe.empty:
                pidx = probe.index
                pos = int(pidx.searchsorted(pd.to_datetime(effective_start)))
                lo = (int(pidx.searchsorted(synthetic.QQQ_LISTING))
                      if pd.to_datetime(effective_start) >= synthetic.QQQ_LISTING else 0)
                # 回数不够(或被 floor 挡住)就吃到可行最早;不足留给矩阵段披露
                probe_start = str(pidx[max(lo, pos - prewarm_n)].date())
        prices, prices_open, data_notes = prepare_prices(
            merged_tickers, probe_start, end, use_synth_tqqq)

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

        # 预热精确对齐(②段,最终权威):探测 index 已精确要料,此处以矩阵真实
        # index(多标的 union 日历)按交易日回数 —— 恰好保留 prewarm_n 根预热,
        # 使 min_periods=prewarm_n 的均线在窗口首日必然成形。多给的原料裁掉
        # (rolling(n) 数学上与预热起点无关,裁剪不改变窗口内任何信号值);
        # 原料客观不足(本地数据不够长/被 floor 挡住)则保留全部并硬披露,
        # 禁止静默。
        if prewarm_n > 0 and effective_start is not None:
            win_ts = pd.to_datetime(effective_start)
            pos = int(prices.index.searchsorted(win_ts))
            warm_exact = pos >= prewarm_n - 1
            # 窗口首日均线成形 ⇔ 首日之前+当日 ≥ n 根 ⇔ pos ≥ n-1
            # （rolling 第 n 根出值；pos = n-1 时窗口首日恰是第 n 根）
            if warm_exact:
                exact_start = prices.index[max(0, pos - prewarm_n)]
                prices = prices.loc[exact_start:]
                if prices_open is not None:
                    prices_open = prices_open.loc[exact_start:]
            else:
                ready_i = prewarm_n - 1
                ready_date = (prices.index[ready_i].date()
                              if len(prices.index) > ready_i else None)
                data_notes.append(
                    f"⚠ 预热不足：窗口起点前数据仅 {pos} 根（需 {prewarm_n} 根，"
                    f"本地数据从 {prices.index[0].date()} 起）。"
                    + (f"均线于 {ready_date} 才成形，此前趋势/恐慌/过热分支"
                       "均不可用，信号将系统性推迟——如需精确口径请提供更早数据"
                       "或后移窗口。" if ready_date else
                       "均线在整个窗口内无法成形，策略信号全部失效。")
                )

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
        # 净值/收益/换手/成交事件切回用户窗口(分母=窗口首日 nav,即资金起点)
        if prewarm_n > 0 and effective_start is not None:
            win_ts = pd.to_datetime(effective_start)
            if result.nav.index[0] < win_ts:
                result.nav = result.nav.loc[win_ts:]
                result.returns = result.returns.loc[win_ts:]
                result.turnover = result.turnover.loc[win_ts:]
                # 事件日=成交日,天然晚于信号日 ≥ 窗口首日+1(预热段权重恒0),此处防御性兜底
                result.trades = [t for t in result.trades
                                 if t.date >= str(win_ts.date())]
                # 精确裁剪成功才可断言「首日成形」；预热不足时 ⚠ 已在裁剪段披露，不重复
                if warm_exact:
                    data_notes.append(
                        f"ℹ 均线全历史预热：均线自窗口前数据计算(按真实交易日历精确回数,"
                        f"窗口首日均线必然成形，指标起点 {prices.index[0].date()})，"
                        f"账户从 {result.nav.index[0].date()} "
                        "按当日信号状态进场（结果与预热起点无关）。"
                    )
                if not start:
                    run.start = str(result.nav.index[0].date())
        perf = compute_metrics(result, prices)
        run.ok, run.nav, run.ret, run.perf = True, result.nav, result.returns, perf
        run.notes = list(built.notes) + list(data_notes)

        # 交易统计:总交易次数口径=有成交发生的交易日数(同 crypto 页 n_trades);
        # 逐笔买卖数与成交事件(含 DCA deposit)一并下发前端(打点/明细表)
        run.trades = result.trades
        run.n_rebalance_days = int((result.turnover > 0).sum())
        run.n_buys = sum(1 for t in result.trades if t.side == "buy")
        run.n_sells = sum(1 for t in result.trades if t.side == "sell")

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

        # 过热增强开启时配跑同窗口对照：①未增强基线(overheat=off)——卡片/折线/
        # 柱状/交易明细 diff 并排;②快进快出变体(overheat=band,同参)——
        # 「一个开关跑双形态」(2026-10-11 用户定稿),前端卡组+折线并排展示
        if strategy_id == "ma200_panic" and clean_params.get("overheat") in ("on", "band"):
            try:
                run.sibling = run_backtest(
                    strategy_id=strategy_id,
                    params={**clean_params, "overheat": "off"},
                    tickers=tickers, start=start, end=end, benchmarks=[],
                    initial_capital=initial_capital, rebalance_cost_bps=rebalance_cost_bps,
                    use_synth_tqqq=use_synth_tqqq, allow_leverage=allow_leverage,
                    execution_price=execution_price, prewarm=prewarm)
                if run.sibling.ok:
                    run.notes.append(
                        "ℹ 过热增强已开启：同窗口配跑未增强基线（图表与交易明细中与增强版并排对照）。"
                    )
            except Exception as e:
                logger.warning("过热基线配跑失败(不影响主结果): %s", e)
            if clean_params.get("overheat") == "on":
                try:
                    band_run = run_backtest(
                        strategy_id=strategy_id,
                        params={**clean_params, "overheat": "band"},
                        tickers=tickers, start=start, end=end, benchmarks=[],
                        initial_capital=initial_capital, rebalance_cost_bps=rebalance_cost_bps,
                        use_synth_tqqq=use_synth_tqqq, allow_leverage=allow_leverage,
                        execution_price=execution_price, prewarm=prewarm)
                    if band_run.ok:
                        # 精简 payload(比照 baseline_policy 但不带 trades——变体不进交易明细)
                        run.band_policy = {
                            "perf": band_run.perf,
                            "nav": {"dates": [str(d.date()) for d in band_run.nav.index],
                                    "values": [round(float(v), 2) for v in band_run.nav.values]},
                            "n_rebalance_days": band_run.n_rebalance_days,
                        }
                        run.notes.append(
                            "ℹ 已同时跑两种买回后处置形态：按趋势线持有(主)与快进快出(过热回调线)对照。"
                        )
                except Exception as e:
                    logger.warning("快进快出变体配跑失败(不影响主结果): %s", e)

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
