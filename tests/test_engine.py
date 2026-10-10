"""回测引擎单元测试：防前视、成本、DCA 现金流（全部用构造数据，不依赖网络）。

口径说明：默认系统口径为 next_open（t+1 开盘成交，外站对齐）；老一批语义测试
显式传 execution_price="close" 固定原行为，另设 next_open 专属用例。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtest.engine import BacktestConfig, run


def _prices(values, name="A", start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({name: values}, index=idx)


CLOSE_CFG = dict(execution_price="close")


def test_buy_hold_matches_asset_return():
    """无成本买入持有：建仓日按 px[0] 起算，nav 终值/初值 == 资产终值/首日价。"""
    px = _prices([100.0, 101, 102.5, 99, 105, 110])
    w = pd.DataFrame({"A": 1.0}, index=px.index)
    res = run(px, w, BacktestConfig(initial_capital=10_000, rebalance_cost_bps=0.0, **CLOSE_CFG))
    assert res.nav.iloc[0] == pytest.approx(10_000)   # 首日无已实现收益
    assert res.nav.iloc[-1] / 10_000 == pytest.approx(110 / 100)
    # 曲线整体与资产价格同比
    assert res.nav.iloc[3] / 10_000 == pytest.approx(99 / 100)


def test_no_lookahead_engine_shifts_weights():
    """防前视：t 日才出现的仓位只从 t+1 日起吃到收益。"""
    # 资产每天 +10%
    n = 6
    px = _prices([100.0 * 1.1 ** i for i in range(n)])
    # 策略在第 4 根K线（index[3]）才给出 1.0 仓位，此后持续持有
    w = pd.DataFrame(0.0, index=px.index, columns=["A"])
    w.iloc[3:] = 1.0
    res = run(px, w, BacktestConfig(initial_capital=1_000, rebalance_cost_bps=0.0, **CLOSE_CFG))
    # 信号日（第4根）权重尚未生效 → 当日收益仍是0
    assert res.returns.iloc[3] == pytest.approx(0.0)
    # 生效日 = 第5根 → 当日吃到 +10%
    assert res.returns.iloc[4] == pytest.approx(0.10, rel=1e-9)
    # 之后保持
    assert res.returns.iloc[5] == pytest.approx(0.10, rel=1e-9)
    # 建仓前 nav 不动
    assert res.nav.iloc[2] == pytest.approx(1_000)


def test_rebalance_cost_reduces_nav():
    """换手成本：单次建仓 2bps 成本正确体现在净值。"""
    px = _prices([100.0, 100, 100, 100, 100])  # 资产横盘
    w = pd.DataFrame({"A": 1.0}, index=px.index)
    res = run(px, w, BacktestConfig(initial_capital=10_000, rebalance_cost_bps=2.0, **CLOSE_CFG))
    # 建仓成本 10_000 * 2/1e4 = 2
    expected = 10_000 - 10_000 * 2 / 1e4
    assert res.nav.iloc[1] == pytest.approx(expected)  # shift后第2根K线承担成本
    for v in res.nav.values[1:]:
        assert v == pytest.approx(expected)


def test_no_leverage_normalization_caps_gross():
    """allow_leverage=False：总权重大于1被压缩到1。"""
    px = _prices([100.0, 101, 102])
    w = pd.DataFrame({"A": 2.0, "B": 2.0}, index=px.index)
    px["B"] = px["A"] * 0.9
    res = run(px, w, BacktestConfig(allow_leverage=False, rebalance_cost_bps=0.0, **CLOSE_CFG))
    assert (res.weights_exec.abs().sum(axis=1) <= 1.0 + 1e-9).all()


def test_dca_cashflow_math():
    """DCA：nav(t) = nav(t-1)·(1+r) + cf，手算对账。"""
    px = _prices([100.0, 110, 121])   # 每天 +10%
    w = pd.DataFrame({"A": 1.0}, index=px.index)
    cf = pd.Series([100.0, 0.0, 0.0], index=px.index)
    res = run(px, w, BacktestConfig(initial_capital=100.0, rebalance_cost_bps=0.0, **CLOSE_CFG),
             cashflows=cf)
    # t0: 100·(1+0) + 100 = 200（首日无收益）
    assert res.nav.iloc[0] == pytest.approx(200.0)
    # t1: 200·1.1 + 0 = 220
    assert res.nav.iloc[1] == pytest.approx(220.0)
    # t2: 220·1.1 + 0 = 242
    assert res.nav.iloc[2] == pytest.approx(242.0)


def test_btc_us_mixed_calendar_intersection():
    """BTC+美股混合：自动取交集日历。"""
    btc_idx = pd.date_range("2020-01-01", "2020-01-14", freq="D")  # 7×24
    us_idx = pd.bdate_range("2020-01-01", "2020-01-14")            # 仅工作日
    px = pd.DataFrame(index=us_idx)
    px_stock = px.copy()
    px_stock["SPY"] = 100.0
    prices = pd.DataFrame(index=btc_idx)
    prices["BTC-USD"] = 100.0
    prices["SPY"] = np.nan
    prices.loc[us_idx, "SPY"] = 100.0
    w = pd.DataFrame(0.5, index=prices.index, columns=prices.columns)
    res = run(prices, w, BacktestConfig(calendar="auto", **CLOSE_CFG))
    # 交集 == 美股交易日
    assert list(res.nav.index) == list(us_idx)


# ───────── next_open 成交口径（外部网站语义，系统默认） ─────────

def _ohlc(opens, closes, name="A", start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(opens))
    return (pd.DataFrame({name: closes}, index=idx),
            pd.DataFrame({name: opens}, index=idx))


def test_next_open_buy_hold_end_to_end():
    """买入持有：起始日次日开盘买入 → 结束日收盘卖出（外站口径）。
    总倍数必等于 close(T)/open(t2)。"""
    opens  = [100.0, 90, 99, 80, 88]      # t1..t5 开盘
    closes = [ 90.0, 99, 80, 88, 96]      # t1..t5 收盘
    C, O = _ohlc(opens, closes)
    w = pd.DataFrame({"A": 1.0}, index=C.index)
    res = run(C, w, BacktestConfig(rebalance_cost_bps=0.0), prices_open=O)  # 默认 next_open
    assert res.nav.iloc[0] == pytest.approx(10_000)                  # t1 空仓（无次日信号外的前置段）
    assert res.nav.iloc[-1] / 10_000 == pytest.approx(96 / 90)       # close(T=tp)/open(t2)
    # 建/换仓都发生在开盘：段链 = open(t2)→open..→close(T)


def test_next_open_no_lookahead():
    """防前视：第 4 根K线收盘的信号，从第 5 根K线开盘起执行。"""
    opens  = [100.0, 100, 100, 100, 110, 121]
    closes = [100.0, 100, 100, 100, 121, 133.1]
    C, O = _ohlc(opens, closes)   # 第5根 t5: open 110 → close 121; 第6根 t6: open 121 → close 133.1
    w = pd.DataFrame(0.0, index=C.index, columns=["A"])
    w.iloc[3:] = 1.0              # t4 收盘信号
    res = run(C, w, BacktestConfig(rebalance_cost_bps=0.0), prices_open=O)
    # t1..t4 nav 不动：信号 t4 收盘，t5 开盘才成交
    for i in range(4):
        assert res.nav.iloc[i] == pytest.approx(10_000)
    # t5 nav 仍无持仓收益（段 [t5_open, t6_open) 仓位 = w(t4)=1 → 这段收益进 t6），
    # nav(t5) 反映的是段 [t4_open→t5_open)（仓位 w(t3)=0）→ 不动
    assert res.nav.iloc[4] == pytest.approx(10_000)
    # nav(t6) = 段 [t5_open→t6_open) ×(末段 [t6_open→t6_close)) = 121/110 × 133.1/121
    assert res.nav.iloc[5] / 10_000 == pytest.approx((121 / 110) * (133.1 / 121))


def test_next_open_cost_on_entry():
    """建仓成本发生在成交开盘（t2），2bps 体现在净值。"""
    opens  = [100.0, 50]
    closes = [100.0, 50]           # 横盘，持仓不赚不亏
    C, O = _ohlc(opens, closes)
    w = pd.DataFrame({"A": 1.0}, index=C.index)
    res = run(C, w, BacktestConfig(initial_capital=10_000, rebalance_cost_bps=2.0),
              prices_open=O)
    # t2 开盘按 50 建仓（扣 2bps），当日 open=close → nav = 10_000×(1-0.0002)
    assert res.nav.iloc[-1] == pytest.approx(10_000 * (1 - 2 / 1e4))
    assert res.nav.iloc[0] == pytest.approx(10_000)


def test_next_open_requires_open_matrix():
    """next_open 缺开盘价矩阵：报错而非静默口径漂移。"""
    px = _prices([100.0, 101])
    w = pd.DataFrame({"A": 1.0}, index=px.index)
    try:
        run(px, w, BacktestConfig())   # 未传 prices_open
    except ValueError:
        pass
    else:
        raise AssertionError("next_open 缺开盘价矩阵未报错")


# ───────── 交易事件提取（TradeEvent / trades）─────────

def test_trades_buy_hold_close_single_entry():
    """close 口径 buy_hold：恰 1 条建仓 buy，事件日=信号日+1，价=事件日收盘。"""
    px = _prices([100.0, 101, 102, 99])
    w = pd.DataFrame({"A": 1.0}, index=px.index)
    res = run(px, w, BacktestConfig(rebalance_cost_bps=0.0, **CLOSE_CFG))
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.side == "buy" and t.ticker == "A"
    assert t.date == str(px.index[1].date())       # 信号(t1)+1 → t2 建仓
    assert t.price == pytest.approx(101.0)          # 事件日收盘
    assert t.delta == pytest.approx(1.0) and t.weight_after == pytest.approx(1.0)
    assert t.nav == pytest.approx(float(res.nav.iloc[1]))


def test_trades_buy_sell_pair_close():
    """0→1→0 切换：买/卖各 1 条，事件日=信号日+1，delta=±1，价=各自事件日收盘。"""
    px = _prices([100.0, 101, 102, 99, 105])
    w = pd.DataFrame(0.0, index=px.index, columns=["A"])
    w.iloc[1:3] = 1.0                              # t2 信号建仓、t4 信号清仓
    res = run(px, w, BacktestConfig(rebalance_cost_bps=0.0, **CLOSE_CFG))
    assert len(res.trades) == 2
    buy, sell = res.trades
    assert buy.side == "buy" and buy.date == str(px.index[2].date())
    assert buy.price == pytest.approx(102.0)
    assert sell.side == "sell" and sell.date == str(px.index[4].date())
    assert sell.price == pytest.approx(105.0)
    assert sell.delta == pytest.approx(-1.0) and sell.weight_after == pytest.approx(0.0)


def test_trades_next_open_price_is_event_day_open():
    """next_open：成交价=事件日开盘（≠当日收盘）；卖价同为开盘。"""
    opens = [100.0, 90, 99, 80, 88]
    closes = [100.0, 99, 80, 88, 96]
    C, O = _ohlc(opens, closes)
    w = pd.DataFrame(0.0, index=C.index, columns=["A"])
    w.iloc[1:3] = 1.0                              # t2 信号 → t3 开盘建仓；t4 信号 → t5 开盘清仓
    res = run(C, w, BacktestConfig(rebalance_cost_bps=0.0), prices_open=O)
    assert len(res.trades) == 2
    buy, sell = res.trades
    assert buy.side == "buy" and buy.date == str(C.index[2].date())
    assert buy.price == pytest.approx(99.0)        # open(t3)=99，区别于 close(t3)=80
    assert sell.side == "sell" and sell.date == str(C.index[4].date())
    assert sell.price == pytest.approx(88.0)       # open(t5)=88


def test_trades_next_open_missing_open_price_is_none():
    """next_open：标的缺开盘价（O 矩阵 NaN）→ price=None 而非报错。"""
    C, O = _ohlc([100.0, 100, 100], [100.0, 100, 100])
    O.iloc[:, 0] = np.nan                          # 整列缺开盘
    w = pd.DataFrame({"A": 1.0}, index=C.index)
    res = run(C, w, BacktestConfig(rebalance_cost_bps=0.0), prices_open=O)
    assert len(res.trades) == 1
    assert res.trades[0].price is None
    assert res.trades[0].side == "buy"


def _assert_turnover_consistency(res):
    """逐日 Σ|delta| ≡ turnover（同源 diff）；无事件的日必为 0（无幽灵交易）。"""
    by_day: dict = {}
    for t in res.trades:
        by_day[t.date] = by_day.get(t.date, 0.0) + abs(t.delta)
    for dt, tv in res.turnover.items():
        d = str(dt.date())
        assert by_day.get(d, 0.0) == pytest.approx(float(tv))
    no_event = [dt for dt in res.turnover.index if str(dt.date()) not in by_day]
    assert (res.turnover.loc[no_event] == 0).all()


def test_trades_turnover_consistency_close():
    """一致性不变量：close 口径。"""
    prices = _prices([100.0, 96, 100, 81, 90, 92, 108])
    w = pd.DataFrame(0.0, index=prices.index, columns=["A"])
    w.iloc[2:5] = 1.0
    res = run(prices, w, BacktestConfig(rebalance_cost_bps=1.0, **CLOSE_CFG))
    _assert_turnover_consistency(res)


def test_trades_turnover_consistency_next_open():
    """一致性不变量：next_open 口径。"""
    C, O = _ohlc([100.0, 95, 99, 80, 88, 91, 105], [100.0, 96, 100, 81, 90, 92, 108])
    w = pd.DataFrame(0.0, index=C.index, columns=["A"])
    w.iloc[2:5] = 1.0
    res = run(C, w, BacktestConfig(rebalance_cost_bps=1.0), prices_open=O)
    _assert_turnover_consistency(res)


def test_trades_dca_deposit_events():
    """DCA：入金日 deposit 事件（amount/nav） + 仅 1 条建仓 buy，不混入交易口径。"""
    px = _prices([100.0, 110, 121, 133.1])
    w = pd.DataFrame({"A": 1.0}, index=px.index)
    cf = pd.Series([100.0, 0.0, 100.0, 0.0], index=px.index)
    res = run(px, w, BacktestConfig(initial_capital=100.0, rebalance_cost_bps=0.0, **CLOSE_CFG),
              cashflows=cf)
    deps = [t for t in res.trades if t.side == "deposit"]
    buys = [t for t in res.trades if t.side == "buy"]
    assert len(deps) == 2 and len(buys) == 1
    assert [t.date for t in deps] == [str(px.index[0].date()), str(px.index[2].date())]
    assert all(t.amount == pytest.approx(100.0) for t in deps)
    assert all(t.ticker is None and t.delta is None for t in deps)
    assert deps[0].nav == pytest.approx(float(res.nav.iloc[0]))
    assert buys[0].date == str(px.index[1].date())   # t2 建仓


def test_trades_same_day_switch_generates_pair():
    """同日换仓（A 1→0.4 + B 0→0.6）：同日期 1 buy + 1 sell（前端 ◆ 的数据前提）。"""
    idx = pd.bdate_range("2020-01-01", periods=4)
    px = pd.DataFrame({"A": [100.0, 101, 102, 103], "B": [50.0, 51, 52, 53]}, index=idx)
    w = pd.DataFrame(0.0, index=idx, columns=["A", "B"])
    w.loc[idx[0]:idx[1], "A"] = 1.0                 # 前段满仓 A
    w.loc[idx[2]:idx[3], "A"] = 0.4                 # t3 信号：A 减 B 加
    w.loc[idx[2]:idx[3], "B"] = 0.6
    res = run(px, w, BacktestConfig(rebalance_cost_bps=0.0, **CLOSE_CFG))
    day3 = [t for t in res.trades if t.date == str(idx[3].date())]
    sides = sorted(t.side for t in day3)
    assert sides == ["buy", "sell"]
    by_tk = {t.ticker: t for t in day3}
    assert by_tk["A"].delta == pytest.approx(-0.6) and by_tk["A"].weight_after == pytest.approx(0.4)
    assert by_tk["B"].delta == pytest.approx(0.6) and by_tk["B"].weight_after == pytest.approx(0.6)
