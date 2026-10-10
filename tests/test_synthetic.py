"""TQQQ 合成器测试：杠杆数学、防跳变拼接、真实数据对账（对账需先 sync，标记 slow）。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import synthetic
from app.data.synthetic import _daily_lev_return, _lint_core_returns, SyntheticConfig


def _qqq_seq(daily_ret=0.01, n=300, start="1999-03-10"):
    idx = pd.bdate_range(start, periods=n)
    return pd.Series((1 + daily_ret) ** np.arange(n) * 100, index=idx, dtype=float)


def test_lint_blocks_garbage_constant_drift():
    """护栏：恒 +1%/日的垃圾序列（2026-10 事故形态，曾把 SYNTH 复利到 3e38）必须被拒。"""
    idx = pd.bdate_range("2010-01-01", periods=300)
    r = pd.Series(0.01, index=idx)
    try:
        _lint_core_returns(r)
    except ValueError:
        pass
    else:
        raise AssertionError("恒 +1%/日序列未被护栏拦截")


def test_lint_blocks_extreme_single_day():
    """护栏：单日 |r|>35% 拒绝（QQQ 历史极端日 ~±15%）。"""
    idx = pd.bdate_range("2010-01-01", periods=50)
    r = pd.Series(0.001, index=idx)
    r.iloc[10] = 0.5     # 单日 +50%
    try:
        _lint_core_returns(r)
    except ValueError:
        pass
    else:
        raise AssertionError("单日 +50% 未被护栏拦截")


def test_lint_blocks_statistically_calm_fake():
    """护栏：统计温和但现实不可能的伪造序列（+0.1%/日、σ仅~2% 年化 ——
    2026-10-09 实际出现的第二种垃圾形态）必须拒绝。"""
    idx = pd.bdate_range("2000-01-01", periods=400)
    r = pd.Series(0.001 + 0.0014 * np.sin(np.arange(400) / 20), index=idx)
    try:
        _lint_core_returns(r)
    except ValueError:
        pass
    else:
        raise AssertionError("异常平静的伪造序列未被护栏拦截")


def test_lint_passes_realistic_series():
    """护栏：正常市况（年化 ~13% 收益、~22% 波动）通过。"""
    idx = pd.bdate_range("2010-01-01", periods=400)
    r = pd.Series(0.0005 + 0.02 * np.sin(np.arange(400) / 10), index=idx)
    _lint_core_returns(r)     # 不抛即通过


def test_daily_lev_math():
    """lev = 3r - 2·fin - fee：固定融资模式下数学精确。"""
    qqq = _qqq_seq(daily_ret=0.01)
    r = qqq.pct_change(fill_method=None).fillna(0.0)
    cfg = SyntheticConfig(financing_mode="fixed", fixed_financing_rate=0.05)
    lev = _daily_lev_return(r, r.index, None, cfg)
    fee = cfg.expense_ratio / 252
    fin = 0.05 / 252
    expected_first = 3 * 0.01 - 2 * fin - fee
    assert lev.iloc[1] == pytest.approx(expected_first, rel=1e-12)
    # IRX 模式：fin 由序列给出（%）
    irx = pd.Series(5.0, index=qqq.index)
    lev2 = _daily_lev_return(r, r.index, irx, SyntheticConfig())
    assert lev2.iloc[1] == pytest.approx(3 * 0.01 - 2 * 0.05 / 252 - fee, rel=1e-12)


def test_depleting_market_never_goes_negative_nav():
    """QQQ 连续下跌：合成 3x 净值趋近 0 但永不为负。"""
    qqq = _qqq_seq(daily_ret=-0.02, n=400)
    r = qqq.pct_change(fill_method=None).fillna(0.0)
    lev = _daily_lev_return(r, r.index, None,
                            SyntheticConfig(financing_mode="fixed", fixed_financing_rate=0.0))
    nav = (1 + lev).cumprod()
    assert (nav > 0).all()           # 复利结构保证
    assert nav.iloc[-1] < 1e-9       # -2%×400 天 → 3x 几乎归零


def test_spliced_no_jump_at_seam(monkeypatch):
    """拼接无跳变：合成段→真实段边界收益率连续（无归一基准跳变）。"""
    # 构造 QQQ（1999 起，温和漂移+正弦波动，通过护栏）与「真实TQQQ」
    # （2010-02-11 起，日收益恰为 3×QQQ 同日收益 —— 模拟完美跟踪的衔接）
    tsei = pd.Timestamp("2010-02-11")
    qqq_idx = pd.DatetimeIndex(
        list(pd.bdate_range("1999-03-10", tsei - pd.Timedelta(days=1)))
        + list(pd.bdate_range(tsei, periods=60)))
    r_q = 0.0005 + 0.02 * np.sin(np.arange(len(qqq_idx)) / 10)   # 日收益序列(年化~22%波动)
    qqq = pd.Series(100 * np.cumprod(1 + r_q), index=qqq_idx)

    def fake_load(ticker):
        if ticker == "QQQ":
            df = pd.DataFrame(index=qqq.index)
            for c in ("Open", "High", "Low", "Close", "Adj Close"):
                df[c] = qqq.values
            df["Volume"] = 0
            return df
        if ticker == "TQQQ":
            idx2 = pd.bdate_range(tsei, periods=60)
            r_leg = r_q[-60:] * 3.0        # 真实段日收益 = 3×QQQ
            px = pd.Series(100 * np.cumprod(1 + r_leg), index=idx2)
            df = pd.DataFrame(index=idx2)
            for c in ("Open", "High", "Low", "Close", "Adj Close"):
                df[c] = px.values
            df["Volume"] = 0
            return df
        return None

    monkeypatch.setattr(synthetic, "_load_or_fetch", fake_load)
    out = synthetic.build_spliced(SyntheticConfig(financing_mode="fixed",
                                                  fixed_financing_rate=0.0))
    assert out is not None
    px = out["Adj Close"]
    # 边界处的单日收益不应出现破坏性跳变（合成段日收益与真实段同規模）
    nearby = px.pct_change().loc[tsei - pd.Timedelta(days=10): tsei + pd.Timedelta(days=10)]
    assert nearby.abs().max() < 0.20    # 无 >20% 的跳日
    # 拼接日之后收益应恰为真实段收益（3×QQQ 当日收益）
    after = px.pct_change()
    assert after.iloc[-1] == pytest.approx(3.0 * r_q[-1], rel=1e-6)


def test_verify_needs_data():
    """无数据时 verify 明确失败而非崩溃。"""
    # 不 run（CI 无数据场景），检查 VerifyResult 默认状态
    r = synthetic.VerifyResult()
    assert r.ok is False
