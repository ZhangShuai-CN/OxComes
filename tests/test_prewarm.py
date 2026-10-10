"""预热精确对齐回归测试（2026-10-10 修复）。

背景：旧实现用自然日换算（500×7/5+7=707 天）估算预热起点，实际仅折 489 根
交易日，MA500（min_periods=500）在窗口内迟到 11 个交易日——窗口起点位于
恐慌区时，首笔恐慌买入被静默推迟（2023-01-01 窗口实测 01-19 才买入）。

治本语义：
1. 数据充足 → 按真实交易日回数，恰好保留 prewarm_n 根预热，
   窗口首日均线必然成形（起点在恐慌区 → 当日出信号、次日开盘成交）；
2. 数据客观不足 → 不静默，notes 硬披露「⚠ 预热不足」。

monkeypatch store.read_ohlcv 提供内存序列（不依赖本地数据）。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtest import runner

PANIC_PARAMS = {"signal_ticker": "QQQ", "holding_ticker": "QQQ"}


def _fake_store(monkeypatch, values):
    """values: 收盘序列（bdate 日历，Open=Close）。patch 进 runner 使用的 store 模块。"""
    idx = pd.bdate_range("2020-01-01", periods=len(values))
    full = pd.DataFrame({"Open": values, "Adj Close": values}, index=idx)

    def read_ohlcv(ticker, start=None, end=None, **kw):
        df = full
        if start is not None:
            df = df.loc[str(start):]
        if end is not None:
            df = df.loc[:str(end):]
        return df.copy()

    monkeypatch.setattr(runner.store, "read_ohlcv", read_ohlcv)
    return idx


def test_prewarm_exact_first_day_signal_ready(monkeypatch):
    """充足场景（500 根预热恰好 + 窗口起点深在两线下方）。

    窗口首日收盘 50 < MA200≈100 < MA500=100（恐慌区）→ 当日即出买入信号，
    次日开盘成交。旧 bug 下 MA500 未成形 → enter 恒 False → 静默空仓盯盘。
    """
    idx = _fake_store(monkeypatch, [100.0] * 500 + [50.0] * 10)
    win0 = idx[500]
    run = runner.run_backtest(
        "ma200_panic", PANIC_PARAMS, ["QQQ"],
        str(win0.date()), str(idx[-1].date()))
    assert run.ok
    buys = [t for t in run.trades if t.side == "buy"]
    # 回归锚点：首笔买入必须发生在窗口次一交易日（次日开盘成交口径）
    assert buys and buys[0].date == str(idx[501].date())
    assert any("窗口首日均线必然成形" in n for n in run.notes)


def test_prewarm_short_history_disclosed(monkeypatch):
    """不足场景：全史只有 300 根，窗口起点前仅 100 根（< 500）。

    MA500 全程无法成形 → 策略信号失效必须被披露，禁止静默。
    """
    idx = _fake_store(monkeypatch, [100.0] * 100 + [50.0] * 200)
    win0 = idx[100]
    run = runner.run_backtest(
        "ma200_panic", PANIC_PARAMS, ["QQQ"],
        str(win0.date()), str(idx[-1].date()))
    assert run.ok
    assert any("⚠ 预热不足" in n for n in run.notes)
    # 全史不足 500 根 → 明确说「整个窗口内无法成形」
    assert any("无法成形" in n for n in run.notes)
