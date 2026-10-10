"""向量化回测引擎。

防前视铁律在引擎层统一执行：策略产出的 weights 是「t 日收盘信号想要的仓位」，
引擎统一 shift(1) —— t 日信号只能吃到 t+1 日起的收益，策略作者无法犯前视错误。

成交价口径（execution_price）：
  "close"     — t 日信号，t+1 日收盘→收盘持有（原实现）：
                  r_p(t) = Σ_i w_exec(t,i)·(Close_t/Close_{t-1} - 1) - turnover·cost
  "next_open" — t 日收盘信号，t+1 日【开盘】成交持有（外部主流回测站口径，默认）：
                  段 [open(t), open(t+1)) 的仓位 = w(t-1)，即 w_exec 与 close 模式同形；
                  末段 [open(T), close(T)] 以 w(T-1) 清算至结束日收盘。
                  买入持有 TQQQ 2011-12-01 → 2026-10-01 在该口径下 216.29x，
                  与外站(testfol 等)同窗口 215.7x 对齐（残差为单日开盘价的源间复权基差）。

无外部现金流时 nav 向量化 cumprod；有现金流（DCA）时 nav(t) = nav(t-1)·(1+r_p) + cf(t)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BTC_SYMBOL = "BTC-USD"


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    rebalance_cost_bps: float = 2.0      # 单边换手的成本（基点）
    allow_leverage: bool = False        # False 时权重逐行归一到绝对和=1
    calendar: str = "auto"              # auto | us | btc | intersection
    execution_price: str = "next_open"  # "close"(t+1收盘结算) | "next_open"(t+1开盘成交,外站口径)


@dataclass
class TradeEvent:
    """单笔成交事件（从实际执行权重矩阵的相邻行 diff 提取）。

    date = 事件日 = 成交日：close 口径为当日收盘结算、next_open 口径为当日开盘成交
    （两者都是 w_exec 相邻行发生变化的那个 t，即换手成本入账的同一行）。
    side="deposit" 为 DCA 外部入金事件（非权重变化，不计入交易笔数口径）。
    """
    date: str                              # YYYY-MM-DD
    ticker: Optional[str] = None           # 标的；deposit 事件为 None
    side: str = "buy"                      # "buy" | "sell" | "deposit"
    delta: Optional[float] = None          # 权重变化（带符号，+买 -卖）；deposit=None
    weight_after: Optional[float] = None   # 变动后该标的权重；deposit=None
    amount: Optional[float] = None         # deposit 入金金额($)；trade=None
    price: Optional[float] = None          # 成交价：close=事件日收盘 / next_open=事件日开盘
    nav: Optional[float] = None            # 事件日账户净值($)

    def to_dict(self) -> dict:
        """JSON 序列化：跳过 None 键 + 数值取整（控制 payload 体积）。"""
        d: dict = {"date": self.date, "side": self.side}
        if self.ticker is not None:
            d["ticker"] = self.ticker
        if self.delta is not None:
            d["delta"] = round(self.delta, 6)
        if self.weight_after is not None:
            d["weight_after"] = round(self.weight_after, 4)
        if self.amount is not None:
            d["amount"] = round(self.amount, 2)
        if self.price is not None:
            d["price"] = round(self.price, 4)
        if self.nav is not None:
            d["nav"] = round(self.nav, 2)
        return d


@dataclass
class BacktestResult:
    nav: pd.Series                      # 净值曲线（index=交易日）
    returns: pd.Series                  # 组合日收益
    weights_exec: pd.DataFrame          # 实际执行的权重（已 shift）
    turnover: pd.Series                 # 日换手（绝对权重变化和）
    cashflows: Optional[pd.Series] = None
    invested: Optional[float] = None    # DCA 总投入本金（含 initial）
    trades: list = field(default_factory=list)  # list[TradeEvent] 成交事件（含 deposit）
    drawdown: pd.Series = field(init=False)  # 水下曲线（0 之上）

    def __post_init__(self):
        roll = self.nav.cummax()
        self.drawdown = self.nav / roll - 1.0


def _align_prices(prices: pd.DataFrame, calendar: str) -> pd.DataFrame:
    """日历对齐：混合组合(含 BTC + 美股)取交集日期；其余取并集。"""
    if prices is None or prices.empty:
        return prices
    if calendar == "intersection":
        return prices.dropna(how="any")
    if calendar in ("auto", "us", "btc"):
        has_btc = any(c == BTC_SYMBOL for c in prices.columns)
        non_btc = [c for c in prices.columns if c != BTC_SYMBOL]
        if has_btc and non_btc and calendar != "btc":
            return prices.dropna(how="any")   # BTC 与美股混合 → 交集
        # 单一市场或纯某市场 → 直接用（缺值按 0 收益处理）
        return prices
    raise ValueError(f"未知日历模式: {calendar}")


def _normalize(w: pd.DataFrame, allow_leverage: bool) -> pd.DataFrame:
    """权重归一。allow_leverage=False 禁止借贷：绝对权重和压缩到 1。"""
    if allow_leverage:
        return w
    gross = w.abs().sum(axis=1)
    scale = 1.0 / gross.where(gross > 1.0, 1.0)  # 只降不升（留现金）
    return w.mul(scale, axis=0)


def _extract_trades(
    w_exec: pd.DataFrame,
    price: pd.DataFrame,
    nav: pd.Series,
    cf: Optional[pd.Series],
    eps: float = 1e-9,
) -> list:
    """从实际执行权重矩阵提取逐笔成交事件（纯后处理，不碰 core 运算路径）。

    一致性不变量：逐日 Σ|delta| ≡ turnover —— 两者同源于 w_exec 的相邻行 diff
    （t2 建仓 / next_open 口径的 t 开盘换仓 / 末日末段换仓均被覆盖，不重不漏）。

    price 矩阵须与 nav 同一坐标系（引擎内 reindex 对齐后的矩阵）：
      close 口径传对齐后收盘价（成交价=事件日收盘），next_open 传对齐后开盘价
      O（成交价=事件日开盘）。缺价（NaN）→ price=None。
    cashflows 非空时，入金日追加 side="deposit" 事件（不计入逐笔买卖口径）。
    """
    events: list = []
    diff = w_exec.sub(w_exec.shift(1).fillna(0.0))
    coords = np.argwhere(diff.abs().to_numpy() > eps)
    for row, col in coords:
        t = w_exec.index[row]
        tk = w_exec.columns[col]
        d = float(diff.iat[row, col])
        p = price.iat[row, col]
        events.append(TradeEvent(
            date=str(t.date()),
            ticker=str(tk),
            side="buy" if d > 0 else "sell",
            delta=d,
            weight_after=float(w_exec.iat[row, col]),
            price=(float(p) if pd.notna(p) else None),
            nav=float(nav.loc[t]),
        ))
    if cf is not None:
        for t, v in cf[cf != 0].items():
            events.append(TradeEvent(
                date=str(t.date()), ticker=None, side="deposit",
                amount=float(v), nav=float(nav.loc[t]),
            ))
    events.sort(key=lambda e: (e.date, e.ticker or ""))
    return events


def run(
    prices: pd.DataFrame,
    weights: pd.DataFrame,
    config: Optional[BacktestConfig] = None,
    cashflows: Optional[pd.Series] = None,
    prices_open: Optional[pd.DataFrame] = None,
) -> BacktestResult:
    """执行回测。

    prices      : index=交易日, columns=ticker, 值=Adj Close
    weights     : 与 prices 对齐的目标权重矩阵（t 日收盘信号）
    prices_open : 开盘价矩阵（execution_price="next_open" 时必需；列/索引需含 prices 的范围，
                  内部会 reindex 对齐）
    """
    cfg = config or BacktestConfig()
    if cfg.execution_price == "next_open":
        if prices_open is None:
            raise ValueError('execution_price="next_open" 需要 prices_open（开盘价矩阵）')
        return _run_next_open(prices, prices_open, weights, cfg, cashflows)
    if cfg.execution_price != "close":
        raise ValueError(f"未知成交价口径: {cfg.execution_price}（close | next_open）")
    return _run_close(prices, weights, cfg, cashflows)


def _run_close(
    prices: pd.DataFrame,
    weights: pd.DataFrame,
    cfg: BacktestConfig,
    cashflows: Optional[pd.Series],
) -> BacktestResult:
    """收盘结算口径（t 日信号 → t+1 收盘起计入收盘→收盘收益）。"""
    prices = _align_prices(prices, cfg.calendar).astype(float)

    # 对齐权重与价格的行列
    weights = weights.reindex(index=prices.index, columns=prices.columns).fillna(0.0)

    # ── 防前视：t 日信号 t+1 生效 ──
    w_exec = weights.shift(1).fillna(0.0)
    w_exec = _normalize(w_exec, cfg.allow_leverage)

    # ── 资产日收益（停牌/缺数据/未上市 → 0，不影响持仓现金部分）──
    rets = prices.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    # ── 换手与成本：单边变动绝对值和；首日建仓也计成本 ──
    turnover = w_exec.sub(w_exec.shift(1).fillna(0.0)).abs().sum(axis=1)
    first_day = w_exec.index[0]
    if first_day in turnover.index:
        turnover.loc[first_day] = w_exec.loc[first_day].abs().sum()

    r_p = (w_exec * rets).sum(axis=1) - turnover * cfg.rebalance_cost_bps / 1e4
    r_p = r_p.fillna(0.0)

    # ── 净值 ──
    if cashflows is None:
        nav = (1.0 + r_p).cumprod() * cfg.initial_capital
        cf_series = None
        invested_amount = cfg.initial_capital
    else:
        cf = cashflows.reindex(r_p.index).fillna(0.0)
        invested_amount = cfg.initial_capital + float(cf.sum())
        nav_vals = np.empty(len(r_p))
        nav_vals[0] = cfg.initial_capital * (1.0 + r_p.iloc[0]) + cf.iloc[0]
        r = r_p.to_numpy()
        c = cf.to_numpy()
        for i in range(1, len(r_p)):
            nav_vals[i] = nav_vals[i - 1] * (1.0 + r[i]) + c[i]
        nav = pd.Series(nav_vals, index=r_p.index)
        cf_series = cf

    return BacktestResult(
        nav=nav,
        returns=r_p,
        weights_exec=w_exec,
        turnover=turnover,
        cashflows=cf_series,
        invested=invested_amount,
        trades=_extract_trades(w_exec, prices, nav, cf_series),
    )


def _run_next_open(
    prices: pd.DataFrame,
    prices_open: pd.DataFrame,
    weights: pd.DataFrame,
    cfg: BacktestConfig,
    cashflows: Optional[pd.Series],
) -> BacktestResult:
    """次日开盘成交口径（外部主流回测站语义）。

    时间结构（t1..T 为交易日）：
      - [t 开盘, t+1 开盘) 的仓位 = w(t-1)（t 收盘信号在 t+1 开盘执行；
        [t1 开盘, t2 开盘) 无历史信号 → 空仓，等价于"起始日次日开盘建仓"）
      - 段收益 seg(t) = open(t+1)/open(t) - 1，t = t1..T-1
      - 末段（最后交易日 T）：[open(T), close(T)] 以 w(T-1) 清算 ——
        组合最终一段在 close(T) 结算，nav 终点 = close(T) 语义
      - 换手发生在 t 开盘：|w(t-1) - w(t-2)|，建仓计 |w(t1)| @t2 开盘
    买入持有验证：nav 终点/起点 = close(T)/open(t2) —— 与外站
    "起始日次日开盘买入、结束日收盘卖出"完全同口径。
    """
    C = _align_prices(prices, cfg.calendar).astype(float)
    O = prices_open.reindex(index=C.index, columns=C.columns).astype(float)

    weights = weights.reindex(index=C.index, columns=C.columns).fillna(0.0)

    # 段矩阵：行 t = 段 [open(t), open(t+1)) 的仓位 w(t-1)
    w_seg = weights.shift(1).fillna(0.0)
    w_seg = _normalize(w_seg, cfg.allow_leverage)

    # 开盘→开盘段收益（最后一行为 NaN，末段另行处理）
    S = O.shift(-1) / O - 1.0
    S = S.replace([np.inf, -np.inf], np.nan)

    # 段起点 t 开盘换手：|w_seg(t) - w_seg(t-1)|（t1 段以 0 为基准 → 建仓成本自动落在 t2）
    turnover = w_seg.sub(w_seg.shift(1).fillna(0.0)).abs().sum(axis=1)
    cost = turnover * cfg.rebalance_cost_bps / 1e4

    # 段组合收益 r_seg(t)（t = t1..T-1）
    r_seg = (w_seg.mul(S, axis=0)).sum(axis=1, skipna=True).fillna(0.0) - cost

    # 末段：[open(T), close(T)]，仓位 = w(T-1)（最后行信号在 T 开盘执行）
    w_last = w_seg.iloc[-1]        # w_seg(T) = w(T-1) —— 语义一致
    seg_last = C.iloc[-1] / O.iloc[-1] - 1.0
    r_last = float((w_last * seg_last).sum()) - float(
        (w_last - w_seg.iloc[-2]).abs().sum() * cfg.rebalance_cost_bps / 1e4
    ) if len(C) > 1 else 0.0

    # 每日 growth：nav(t+1) = nav(t) × (1+r_seg(t))；最后一日骑末段
    idx = C.index
    g = pd.Series(1.0, index=idx)
    if len(idx) > 1:
        g.iloc[1:] = 1.0 + r_seg.iloc[:-1].to_numpy()
        g.iloc[-1] = (1.0 + r_seg.iloc[-2]) * (1.0 + r_last) if len(idx) > 1 else 1.0
        # 长度 1 时无段

    # ── 净值 ──
    if cashflows is None:
        nav = g.cumprod() * cfg.initial_capital
        cf_series = None
        invested_amount = cfg.initial_capital
        returns = nav.pct_change(fill_method=None).fillna(0.0)
    else:
        cf = cashflows.reindex(idx).fillna(0.0)
        invested_amount = cfg.initial_capital + float(cf.sum())
        nav_vals = np.empty(len(idx))
        nav_vals[0] = cfg.initial_capital * g.iloc[0] + cf.iloc[0]
        gv = g.to_numpy()
        c = cf.to_numpy()
        for i in range(1, len(idx)):
            nav_vals[i] = nav_vals[i - 1] * gv[i] + c[i]
        nav = pd.Series(nav_vals, index=idx)
        cf_series = cf
        returns = nav.pct_change(fill_method=None).fillna(0.0)

    return BacktestResult(
        nav=nav,
        returns=returns,
        weights_exec=w_seg,
        turnover=turnover,
        cashflows=cf_series,
        invested=invested_amount,
        trades=_extract_trades(w_seg, O, nav, cf_series),
    )


def run_benchmark(prices: pd.DataFrame, ticker: str,
                  config: Optional[BacktestConfig] = None,
                  prices_open: Optional[pd.DataFrame] = None,
                  cashflows: Optional[pd.Series] = None) -> BacktestResult:
    """便捷：单标的基准（买入持有，零成本；cashflows 非空时镜像主策略资金流，
    如定投场景下基准做同款定投，保证左右对照 apples-to-apples）。"""
    w = pd.DataFrame({ticker: 1.0}, index=prices.index)
    c = (config or BacktestConfig()).__dict__.copy()
    bench_cfg = BacktestConfig(rebalance_cost_bps=0.0, **{k: v for k, v in c.items() if k != "rebalance_cost_bps"})
    return run(prices[[ticker]], w, bench_cfg, cashflows=cashflows, prices_open=prices_open)
