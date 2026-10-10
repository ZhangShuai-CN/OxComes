"""策略行为测试：ma200_panic 按持仓状态分派的事件状态机 / ma_cross 金叉死叉 / registry 完备性。

序列构造用斜坡段（恒平台使 close==MA，严格比较恒 False，会卡边界）。
切换锚点先用 REPL 实跑校准再固化（见各断言处的当日均线值注释）。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtest.strategies import get_strategy, list_strategies
from app.backtest.strategies.builtin import (
    BuyHoldStrategy,
    MA200PanicStrategy,
    MACrossStrategy,
)
from app.backtest import params as params_mod


def _frame(qqq, tqqq=None, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(qqq))
    tqqq = tqqq if tqqq is not None else [v * 3 for v in qqq]
    return pd.DataFrame({"QQQ": qqq, "TQQQ": tqqq}, index=idx)


def _switches(w: pd.Series) -> int:
    """仓位切换次数（0→1 或 1→0）。"""
    return int((w.diff().fillna(w.iloc[0]) != 0).sum())


def _built_w(strategy_cls, params, qqq):
    out = strategy_cls().build(_frame(qqq), params)
    return out.weights["TQQQ"]


# ---------------- ma200_panic ----------------
# 用户钦点的状态机语义（按持仓状态分派）：
#   持仓中：跌破200MA（从上方穿越）→ 卖出。唯一卖出。站上/跌破500MA、站上200MA 均不动。
#   空仓中：站上200MA → 买入；收盘<500MA（恐慌区）→ 买入。

def test_ma200_panic_crash_through_then_panic_rebuy():
    """单日崩穿两线：持仓跌破200MA卖出（同日虽已入恐慌区，卖出优先不先买）；
    次日收盘仍在恐慌区 → 空仓恐慌买入；此后阴跌全程持有（无新「跌破200」穿越）。

    序列（trend=5, panic=20）：60天缓涨(100→135.4) → 崩跌5天到52 → 阴跌35天。
    """
    qqq = [100.0 + 0.6 * i for i in range(60)]
    qqq += [60.0, 58.0, 56.0, 54.0, 52.0]
    qqq += [51.8 - 0.2 * k for k in range(35)]
    w = _built_w(MA200PanicStrategy, {"ma_trend": 5, "ma_panic": 20,
                                     "signal_ticker": "QQQ", "holding_ticker": "TQQQ"}, qqq)

    # day4: 102.4 > ma5=101.2 站上趋势线买入；缓涨段持有
    assert w.iloc[:4].eq(0).all() and w.iloc[4] == 1.0
    # day60: 60 < ma5=119.6 从上方跌破趋势线 → 卖出（同日 60<ma20 已入恐慌区，纪律先卖）
    assert w.iloc[59] == 1.0 and w.iloc[60] == 0.0
    # day61: 空仓 + 58 < ma20=123.17 恐慌区 → 买回
    assert w.iloc[61] == 1.0
    # 此后阴跌全程在 200线下方：无新「站上→跌破」穿越 → 持有到底
    assert w.iloc[61:].eq(1.0).all()
    assert _switches(w) == 3


def test_ma200_panic_position_crosses_panic_line_noop():
    """恐慌仓生命周期（用户钦点核心）：恐慌区买入 → 反弹站上500MA 不卖 →
    再跌破500MA 不动 → 站上200MA 不动（已持仓）→ 最终跌破200MA（从上方穿越）
    才卖出。卖出时价格已在恐慌线上方（观望带），空仓等到再次入恐慌区才回补。

    序列（trend=5, panic=20）：恒100×60 → 阴跌25天(99→75) → 强反弹20天(76.5→105)
    → 平台97×12。
    """
    qqq = [100.0] * 60
    qqq += [100.0 - 1.0 * k for k in range(1, 26)]
    qqq += [75.0 + 1.5 * k for k in range(1, 21)]
    qqq += [97.0] * 12
    w = _built_w(MA200PanicStrategy, {"ma_trend": 5, "ma_panic": 20,
                                     "signal_ticker": "QQQ", "holding_ticker": "TQQQ"}, qqq)

    # day60: 99 < ma20=99.95 阴跌首日入恐慌区 → 空仓恐慌买入
    assert w.iloc[:60].eq(0).all() and w.iloc[60] == 1.0
    # 反弹段 close 穿越 ma20（≈day85-90，从下方回到上方）→ 站上500MA：仍持有
    # 全程 day60..day104 无任何切换（若「出恐慌区卖出」回归，这里会提前变 0）
    assert w.iloc[60:105].eq(1.0).all()
    # day105: 97 < ma5=101.6 且此前已站上过趋势线（反弹中 above 转真）→ 跌破200卖出；
    # 此时 97 > ma20=91.78（恐慌线上方）→ 观望带空仓，不回补
    assert w.iloc[105:113].eq(0).all()
    # day113: 平台把 ma20 拖到 97.28 > 97 → 再次入恐慌区 → 空仓买入
    assert w.iloc[113] == 1.0
    assert _switches(w) == 3


def test_ma200_panic_shallow_dip_sell_then_reentry():
    """趋势完整环：缓涨站上买 → 浅回撤进观望带（跌破200卖，未到500不动买）
    → 反弹站上200MA → 空仓重新买入（用户要求的「重新买入」回归锚）。"""
    qqq = [100.0 + 1.0 * i for i in range(60)]        # 100 → 159
    qqq += [157.0, 154.0, 156.0, 158.5]               # 浅回撤 → 反弹
    qqq += [159.5 + 1.0 * k for k in range(20)]       # 续涨
    w = _built_w(MA200PanicStrategy, {"ma_trend": 5, "ma_panic": 20,
                                     "signal_ticker": "QQQ", "holding_ticker": "TQQQ"}, qqq)

    # day4: 104 > ma5=102 缓涨段买入并持有
    assert w.iloc[4] == 1.0 and w.iloc[4:60].eq(1.0).all()
    # day60: 157 < ma5=157.4 跌破趋势线卖出；157 > ma20=150.35（观望带，不恐慌）→ 保持空仓
    assert w.iloc[60] == 0.0 and w.iloc[61] == 0.0 and w.iloc[62] == 0.0
    # day63: 158.5 > ma5=156.9 重新站上200MA → 空仓重新买入，持有到底
    assert w.iloc[63] == 1.0 and w.iloc[63:].eq(1.0).all()
    assert _switches(w) == 3


def test_ma200_panic_param_guards():
    """恐慌线必须长于趋势线（否则无观望带）；缺资产明确报错。"""
    prices = _frame([100.0] * 30)
    for bad_params, msg in [
        ({"ma_trend": 200, "ma_panic": 150}, "恐慌均线应长于趋势线未被拦截"),
        ({"ma_trend": 10, "ma_panic": 20, "signal_ticker": "SPY",
          "holding_ticker": "TQQQ"}, "缺信号资产未报错"),
    ]:
        try:
            MA200PanicStrategy().build(prices, bad_params)
            raise AssertionError(msg)
        except ValueError:
            pass


# ---------------- ma_cross ----------------

def test_ma_cross_golden_and_death_cross():
    """跌30天 → 涨40天 → 跌30天：金叉前空仓、涨段持有、死叉后清仓。"""
    qqq = [200.0 - 1.0 * i for i in range(30)]        # 200 → 171（死叉态）
    qqq += [171.0 + 1.0 * j for j in range(40)]       # → 211（金叉）
    qqq += [211.0 - 1.0 * k for k in range(30)]       # → 181（死叉）
    w = _built_w(MACrossStrategy, {"fast": 5, "slow": 20,
                                "signal_ticker": "QQQ", "holding_ticker": "TQQQ"}, qqq)

    # 跌段（ma5 < ma20 恒成立）全程空仓
    assert w.iloc[:30].eq(0).all()
    # 涨段中后段（金叉已发生）持有
    assert w.iloc[50:70].eq(1.0).all()
    # 死叉后清仓到结束
    assert w.iloc[-5:].eq(0).all()
    # 恰好 1 次金叉 + 1 次死叉
    assert _switches(w) == 2


def test_ma_cross_param_guards():
    prices = _frame([100.0] * 30)
    for bad_params, msg in [
        ({"fast": 30, "slow": 20}, "快线≥慢线未被拦截"),
        ({"fast": 5, "slow": 20, "signal_ticker": "QQQ",
          "holding_ticker": "QQQ_INVALID_COL"}, "缺持仓资产未报错"),
    ]:
        try:
            MACrossStrategy().build(prices, bad_params)
            raise AssertionError(msg)
        except ValueError:
            pass


# ---------------- 基础策略冒烟 ----------------

def test_warmup_days_declarations():
    """「均线全历史预热」：策略声明指标计算需要的最长回看窗（交易日）。"""
    assert MA200PanicStrategy().warmup_days({"ma_trend": 200, "ma_panic": 500}) == 500
    assert MA200PanicStrategy().warmup_days({"ma_trend": 250, "ma_panic": 400}) == 400
    assert MACrossStrategy().warmup_days({"fast": 5, "slow": 30}) == 30
    assert BuyHoldStrategy().warmup_days({}) == 0
    from app.backtest.strategies.builtin import DCAStrategy
    assert DCAStrategy().warmup_days({"amount": 500, "freq": "weekly"}) == 0


def test_buy_hold_smoke():
    prices = _frame([100.0, 101.0, 102.0, 103.0])
    out = BuyHoldStrategy().build(prices, {})
    # 主标的 = 首列（runner 把用户单一主标的排在价格矩阵首位）
    assert out.weights[prices.columns[0]].eq(1.0).all()
    assert out.weights[prices.columns[1]].eq(0.0).all()


def test_param_validation_min_max():
    """ParamSpec min/max 校验（前端与 API 共用路径）。"""
    cls = get_strategy("ma200_panic")
    for bad, msg in [({"ma_trend": 2}, "低于下限未拦截"),     # min=5
                     ({"ma_trend": 999}, "高于上限未拦截")]:   # max=500
        try:
            params_mod.validate_params(cls, bad)
            raise AssertionError(msg)
        except ValueError:
            pass


def test_registry_completeness():
    """精简后 REGISTRY：买入持有/定投/均线两类 4 个；旧 id 已下线归档。"""
    ids = {s["id"] for s in list_strategies()}
    expected = {"buy_hold", "dca", "ma200_panic", "ma_cross"}
    assert ids == expected, f"REGISTRY 应恰为 {expected}，实际 {ids}"
    for sid in expected:
        cls = get_strategy(sid)
        assert cls.schema()["params"] is not None
        # 每个策略可实例化且参数可走完 validate 补全默认值
        params_mod.validate_params(cls, {})
