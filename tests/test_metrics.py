"""指标计算测试：用可手算的小样本对账。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.backtest import metrics


def _nav(values, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_cagr_and_total_return():
    # 2 年翻倍：nav 100 → 200（约 504 个交易日）
    import numpy as np
    values = np.linspace(100, 200, 504)
    nav = _nav(list(values))
    rets = nav.pct_change().fillna(0)
    p = metrics.compute(nav, rets)
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    assert p.total_return == pytest.approx(1.0, rel=1e-9)
    assert p.cagr == pytest.approx(2.0 ** (1 / years) - 1, rel=1e-4)


def test_xirr_known_case():
    """XIRR 手算对：期初 100 投 2 年 + 1 年后再投 100,期末 231 → 年化恰 10%。
    (100×1.1² + 100×1.1 = 121 + 110 = 231)"""
    idx = pd.to_datetime(["2020-01-01", "2021-01-01", "2022-01-01"])
    nav = pd.Series([100.0, 150.0, 231.0], index=idx)     # 中间值形状无关 XIRR
    rets = nav.pct_change().fillna(0)
    cashflows = pd.Series([0.0, 100.0, 0.0], index=idx)   # 引擎口径：不含期初
    p = metrics.compute(nav, rets, invested=200.0, cashflows=cashflows)
    assert p.invested == pytest.approx(200.0)
    assert p.xirr is not None
    # 365.25折算下 366/365 天自然年≈非整数持有年,解与手算差数十 bp 量级
    assert p.xirr == pytest.approx(0.10, abs=5e-4)
    # 等效一次性(口径A,保留字段): (231/200)^(1/2)−1 ≈ 7.47%
    assert p.cagr == pytest.approx(0.0747, abs=1e-4)


def test_max_drawdown_and_calmar():
    # 100 → 150 → 75 → 150：MDD = -50%
    nav = _nav([100, 150, 75, 150])
    rets = nav.pct_change().fillna(0)
    p = metrics.compute(nav, rets)
    assert p.max_drawdown == pytest.approx(-0.5)


def test_drawdown_periods_detection():
    # 100 → 120(新高) → 60(腰斩) → 160(恢复并新高)
    nav = _nav([100, 120, 60, 160])
    rets = nav.pct_change().fillna(0)
    p = metrics.compute(nav, rets)
    assert p.max_drawdown == pytest.approx(-0.5)
    top = p.drawdown_top5[0]
    assert top["depth"] == pytest.approx(-0.5)
    assert top["recovery_date"] is not None
    assert top["recovery_days"] is not None


def test_unrecovered_drawdown():
    nav = _nav([100, 150, 100, 140])   # 140 < 150 未恢复
    rets = nav.pct_change().fillna(0)
    p = metrics.compute(nav, rets)
    assert p.max_drawdown == pytest.approx(-1 / 3 + 1 - 1)  # 100/150-1
    top = p.drawdown_top5[0]
    assert top["recovery_date"] is None
    assert p.longest_recovery_days is None


def test_annual_returns():
    # 跨三个日历年: 2020 末 110, 2021 末 121, 2022 末 100
    nav = pd.Series(
        [100.0, 110, 121, 100],
        index=pd.to_datetime(["2020-01-02", "2020-12-31", "2021-12-31", "2022-12-30"]),
    )
    rets = nav.pct_change().fillna(0)
    p = metrics.compute(nav, rets)
    assert p.annual_returns["2020"] == pytest.approx(0.10)
    assert p.annual_returns["2021"] == pytest.approx(0.10)
    assert p.annual_returns["2022"] == pytest.approx(100 / 121 - 1, rel=1e-3)
    assert p.best_year == pytest.approx(0.10)
    assert p.worst_year == pytest.approx(100 / 121 - 1, rel=1e-3)


def test_win_rate_daily():
    nav = _nav([100, 101, 100.5, 110, 108])
    rets = nav.pct_change().fillna(0)
    p = metrics.compute(nav, rets)
    # 4 天中涨2跌2（首日0不计）
    assert p.win_rate_daily == pytest.approx(0.5)
