"""数据源注册/优先链配置测试(不依赖网络)。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import sources


def test_domain_of_classification():
    assert sources.domain_of("QQQ") == "us_stock"
    assert sources.domain_of("TQQQ") == "us_stock"
    assert sources.domain_of("BTC-USD") == "btc"
    assert sources.domain_of("^IRX") == "rates"
    assert sources.domain_of("^TNX") == "rates"


def test_default_chain_and_get_chain():
    chain = sources.get_chain("us_stock")
    assert chain == sources.DOMAINS["us_stock"]["default_chain"]
    assert "eastmoney" in chain and "yahoo" in chain


def test_no_synthetic_rate_provider():
    """无假数据铁律:分段常数利率兜底已下线,rates 域只允许真实源。"""
    assert "rates_fallback" not in sources.PROVIDERS
    assert "rates_fallback" not in sources.DOMAINS["rates"]["default_chain"]
    assert set(sources.get_chain("rates")) <= {"us_treasury", "fred", "yahoo"}


def test_set_chain_roundtrip_and_validation():
    # 合法链:保存后 get_chain 立即反映,再保存回默认
    saved = sources.set_chain("us_stock", ["tencent", "eastmoney"])
    assert saved == ["tencent", "eastmoney"]
    assert sources.get_chain("us_stock") == ["tencent", "eastmoney"]
    # 恢复默认,避免污染其他测试/实际运行
    sources.set_chain("us_stock", sources.DOMAINS["us_stock"]["default_chain"])
    assert sources.get_chain("us_stock") == sources.DOMAINS["us_stock"]["default_chain"]
    # 非法 provider / 跨域 / 未知域:一律拒绝(自制 shim 无 pytest.raises,用 try 风格)
    for bad in ((["not_a_provider"], "us_stock"),
                (["fred"], "us_stock"),
                (["eastmoney"], "nope")):
        chain_arg, domain_arg = bad
        try:
            sources.set_chain(domain_arg, chain_arg)
        except ValueError:
            pass
        else:
            raise AssertionError(f"非法配置未被拒绝: {domain_arg} {chain_arg}")


def test_snapshot_shape():
    snap = sources.snapshot()
    assert snap["ok"] is True
    assert set(snap["domains"].keys()) >= {"us_stock", "rates", "btc"}
    for _, info in snap["domains"].items():
        assert info["chain"] and info["available"]
        for p in info["chain"]:
            assert p in snap["providers"]
    assert any(f["domain"] == "events" for f in snap["fixed"])
