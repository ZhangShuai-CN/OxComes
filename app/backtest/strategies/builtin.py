"""内置策略（REGISTRY 精简版：只保留 买入持有 / 定投 / 均线 三类）。

设计要点：
- 均线策略支持「信号资产与持仓资产分离」：默认信号=TQQQ 自身均线（与外站口径
  一致，2011-12 起 TQQQ 窗口实测显著更优）；想用母指数信号改 signal_ticker=QQQ
- dca 用 cashflows 机制：nav(t) = nav(t-1)·(1+r) + cf(t)
- ma200_panic 是事件驱动仓位机：站上200MA/跌破500MA 买入，跌破200MA 唯一卖出；
  恐慌仓反弹站上500MA不触发任何动作（用户钦点语义），用显式循环维护持仓状态
- ma_cross 金叉持有/死叉清仓在集合上等价于 (fast_ma > slow_ma)，可直接向量化
"""
from __future__ import annotations

import pandas as pd

from app.backtest.strategies import register
from app.backtest.strategies.base import ParamSpec, Strategy, StrategyOutput

SYNTH_NOTE = (
    "⚠ 本回测使用了 1999-2010 的合成 TQQQ 数据（QQQ×3 模型重建）。"
    "2000-2002 极端尾部只有量级意义，无精度意义。"
)


@register
class BuyHoldStrategy(Strategy):
    id = "buy_hold"
    name = "买入持有"
    description = "期初满仓买入主标的后一直持有（基准对照组）"

    # 单主标的语义下不再需要 weights 参数（多标的等权=每日再平衡曾两次误导用户,
    # 已移除;策略辅助资产由 runner 自动注入但权重恒为 0）

    def build(self, prices: pd.DataFrame, params: dict) -> StrategyOutput:
        out = StrategyOutput(weights=pd.DataFrame(0.0, index=prices.index, columns=prices.columns))
        main_col = prices.columns[0]          # 主标的 = ticker 列首列
        out.weights[main_col] = 1.0
        return out


@register
class DCAStrategy(Strategy):
    id = "dca"
    name = "定投"
    description = "定期定额投入（cashflow 模型：nav(t)=nav(t-1)·(1+r)+cf）"

    params = [
        ParamSpec("amount", "每期投入金额($)", type="number", default=500, min=1, step=50),
        ParamSpec("freq", "投入频率", type="select", default="weekly",
                  options=[{"value": "weekly", "label": "每周"},
                           {"value": "monthly", "label": "每月"}]),
    ]

    def build(self, prices: pd.DataFrame, params: dict) -> StrategyOutput:
        amount = float(params.get("amount") or 500)
        freq = params.get("freq") or "weekly"

        buy = BuyHoldStrategy().build(prices, {})
        out = StrategyOutput(weights=buy.weights)

        idx = prices.index
        if freq == "weekly":
            key = pd.Series(idx, index=idx).dt.isocalendar().astype(str).agg("-".join, axis=1)
        else:
            key = pd.Series(idx, index=idx).dt.to_period("M").astype(str)
        first_of_period = ~key.duplicated()
        cf = pd.Series(amount, index=idx)[first_of_period]
        out.cashflows = cf.reindex(idx).fillna(0.0)
        return out


def _asset_columns(prices: pd.DataFrame, signal: str, holding: str) -> None:
    """信号/持仓分离策略的公共防呆：两资产必须在价格矩阵中（缺数据明确报错）。"""
    for name, col in (("信号资产", signal), ("持仓资产", holding)):
        if col not in prices.columns:
            raise ValueError(f"{name} {col} 不在价格矩阵中")


@register
class MA200PanicStrategy(Strategy):
    """200MA 趋势 + 500MA 恐慌抄底（事件驱动仓位机，用户三轮确认的语义）。

    t 日收盘信号 → t+1 日开盘成交（引擎 shift(1) 统一防前视）：
    - 跌破 200MA（昨收 ≥ 线上、今收 < 线下）→ 卖出 ——**唯一的卖出触发**
    - 空仓中站上 200MA → 买入（趋势进场）
    - 空仓中收盘 < 500MA → 买入（恐慌区抄底）
    - 站上 500MA **不触发任何动作**：恐慌仓反弹脱离恐慌区后继续持有，
      直到站上 200MA（已在场内，无操作），将来再跌破 200MA 才离场。

    「跌破200」用交叉事件（从上方跌到下方）而非状态：恐慌买入时价格已在
    200MA 下方，状态语义会买入次日即卖形成死循环；事件语义下恐慌仓的
    出场 = 站上 200MA 之后的再次跌破。

    过热增强（overheat="on"，用户 2026-10-10 拍板）：滞回带止盈——
    - 持仓中收盘 > 趋势均线×1.5 → **次日开盘卖出**，进入「过热等待」态
      （空仓遇此条件同样直接等待，不进场）
    - 过热等待中，收盘回落到 ≤ 均线×1.2 且仍在线上 → **次日开盘买回**；
      1.2×~1.5× 带内继续等待；跌落到均线下方 → 恢复普通空仓语义
      （恐慌区/站上规则当日接管）
    """
    id = "ma200_panic"
    name = "200MA趋势 + 500MA恐慌抄底"
    description = (
        "收盘跌破200MA→次日开盘卖出(唯一卖出)；空仓时收盘站上200MA→次日开盘买入；"
        "收盘跌破500MA(恐慌区)→次日开盘买入。恐慌仓反弹站上500MA不卖出："
        "继续持有直到站上200MA后再跌破200MA才离场。"
        "可选过热增强：收盘>200MA+50%止盈卖出，回落到200MA+20%(线上)买回。"
    )

    OVERHEAT_SELL = 1.5    # 收盘 > 趋势均线×1.5 → 过热止盈
    OVERHEAT_REBUY = 1.2   # 收盘 ≤ 趋势均线×1.2 且线上 → 买回

    params = [
        ParamSpec("signal_ticker", "信号资产", type="string", default="TQQQ"),
        ParamSpec("holding_ticker", "持仓资产", type="string", default="TQQQ"),
        ParamSpec("ma_trend", "趋势均线(天数)", type="number", default=200, min=5, max=500, step=1,
                  grid=[100, 150, 200, 250]),
        ParamSpec("ma_panic", "恐慌抄底均线(天数)", type="number", default=500, min=21, max=1000, step=1,
                  grid=[300, 400, 500, 600]),
        ParamSpec("weight", "持仓权重(1=全仓)", type="number", default=1.0, min=0.05, max=1.0, step=0.05),
        ParamSpec("overheat", "过热增强", type="select", default="off",
                  options=[{"value": "off", "label": "关闭"},
                           {"value": "on", "label": "开启（收盘>200MA+50%止盈，回落200MA+20%线上买回）"}]),
    ]

    def warmup_days(self, params: dict) -> int:
        return int(max(float(params.get("ma_trend") or 200),
                       float(params.get("ma_panic") or 500)))

    def build(self, prices: pd.DataFrame, params: dict) -> StrategyOutput:
        signal = params.get("signal_ticker") or "QQQ"
        holding = params.get("holding_ticker") or "TQQQ"
        trend_n = int(params.get("ma_trend") or 200)
        panic_n = int(params.get("ma_panic") or 500)
        weight = float(params.get("weight") or 1.0)
        overheat = str(params.get("overheat") or "off") == "on"

        out = StrategyOutput(weights=pd.DataFrame(0.0, index=prices.index, columns=prices.columns))
        _asset_columns(prices, signal, holding)
        if panic_n <= trend_n:
            raise ValueError(f"恐慌均线({panic_n}日)应长于趋势均线({trend_n}日)，否则无观望带")

        sig_px = prices[signal]
        ma_t = sig_px.rolling(trend_n, min_periods=trend_n).mean()
        ma_p = sig_px.rolling(panic_n, min_periods=panic_n).mean()
        above_t = (sig_px > ma_t).fillna(False)          # 200MA 上方（NaN 比较=False）
        in_panic = (sig_px < ma_p).fillna(False)         # 500MA 下方（恐慌区）
        enter = above_t | in_panic                       # 买入：站上趋势线 或 恐慌区
        # 卖出「跌破」= 交叉事件：昨日线上 → 今日线下
        cross_dn = above_t.shift(1, fill_value=False) & ~above_t
        # 过热增强：卖出/买回阈值线（滞回带防鞭打）
        oh_sell = (sig_px > ma_t * self.OVERHEAT_SELL).fillna(False) if overheat else None
        oh_rebuy = (sig_px <= ma_t * self.OVERHEAT_REBUY).fillna(False) if overheat else None

        w = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        state = "flat"    # flat 空仓 | hold 持仓 | wait 过热等待（等回落买回）
        for date in prices.index:
            start_state = state
            if state == "hold":
                if bool(cross_dn.loc[date]):
                    # 单日暴跌同时穿两线（跌破200又直接进恐慌区）：卖出优先纪律，
                    # 卖出当日不再评估进场（哪怕同日已在恐慌区），次日收盘仍在
                    # 恐慌区(<500MA)则按恐慌规则买回
                    state = "flat"
                elif overheat and bool(oh_sell.loc[date]):
                    state = "wait"             # 过热止盈 → 等回落
            elif state == "wait":
                if bool(sig_px.loc[date] <= ma_t.loc[date]):
                    state = "flat"             # 跌回均线 → 恢复普通空仓语义（同样次日起评估）
                elif overheat and bool(oh_rebuy.loc[date] and above_t.loc[date]):
                    state = "hold"             # 回落到 (200MA, 200MA×1.2] 带内 → 买回
                # 1.2×~1.5× 带内继续等待（滞回带防鞭打）
            # 进场评估只对「当日开场即空仓」的 flat 生效：当日刚卖出/刚转出的
            # 不再进场（与原 elif 链的「卖出优先」纪律一致）
            if start_state == "flat":
                if overheat and bool(oh_sell.loc[date]):
                    state = "wait"             # 空仓遇过热：不进场，直接进入回落等待
                elif bool(enter.loc[date]):
                    state = "hold"
            if state == "hold":
                w.loc[date, holding] = weight
        out.weights = w
        if any(c.startswith("SYNTH_") for c in prices.columns):
            out.note(SYNTH_NOTE)
        return out


@register
class MACrossStrategy(Strategy):
    """双均线金叉死叉（默认 MA5×MA30，TQQQ 自身信号）。

    金叉（fast 上穿 slow）持有、死叉（fast 下穿 slow）清仓 —— 从金叉到死叉
    期间 fast_ma > slow_ma 恒成立，故持仓状态集合等价于 (fast_ma > slow_ma)，
    无需显式循环。均线未形成（NaN 比较=False）时自然空仓。
    """
    id = "ma_cross"
    name = "均线金叉死叉（MA5×MA30）"
    description = (
        "快均线上穿慢均线（金叉）→ 次日开盘买入；快均线下穿慢均线（死叉）→ 次日开盘卖出。"
        "默认 MA5×MA30、TQQQ 自身均线（可改信号资产如 QQQ）。"
    )

    params = [
        ParamSpec("signal_ticker", "信号资产", type="string", default="TQQQ"),
        ParamSpec("holding_ticker", "持仓资产", type="string", default="TQQQ"),
        ParamSpec("fast", "快均线天数", type="number", default=5, min=2, max=100, step=1,
                  grid=[3, 5, 10, 20]),
        ParamSpec("slow", "慢均线天数", type="number", default=30, min=10, max=250, step=1,
                  grid=[20, 30, 60, 120]),
        ParamSpec("weight", "持仓权重(1=全仓)", type="number", default=1.0, min=0.05, max=1.0, step=0.05),
    ]

    def warmup_days(self, params: dict) -> int:
        return int(float(params.get("slow") or 30))

    def build(self, prices: pd.DataFrame, params: dict) -> StrategyOutput:
        signal = params.get("signal_ticker") or "QQQ"
        holding = params.get("holding_ticker") or "TQQQ"
        fast = int(params.get("fast") or 5)
        slow = int(params.get("slow") or 30)
        weight = float(params.get("weight") or 1.0)
        if fast >= slow:
            raise ValueError(f"快均线({fast}日)必须短于慢均线({slow}日)")

        out = StrategyOutput(weights=pd.DataFrame(0.0, index=prices.index, columns=prices.columns))
        _asset_columns(prices, signal, holding)
        sig_px = prices[signal]
        fast_ma = sig_px.rolling(fast, min_periods=fast).mean()
        slow_ma = sig_px.rolling(slow, min_periods=slow).mean()
        long_state = (fast_ma > slow_ma).fillna(False)
        out.weights[holding] = long_state.astype(float) * weight
        if any(c.startswith("SYNTH_") for c in prices.columns):
            out.note(SYNTH_NOTE)
        return out
