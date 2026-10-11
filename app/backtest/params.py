"""参数解析/校验。"""
from __future__ import annotations

from typing import Any

from app.backtest.strategies.base import Strategy


def validate_params(strategy: type[Strategy], params: dict) -> dict:
    """用 ParamSpec 校验并补全参数（未知键剔除，缺省补默认值）。"""
    spec = {p.key: p for p in strategy.params}
    clean: dict[str, Any] = {}
    for key, sp in spec.items():
        v = params.get(key, sp.default)
        if sp.type == "number" and v is not None and not isinstance(v, bool):
            v = float(v)
            if sp.min is not None and v < sp.min:
                raise ValueError(f"{strategy.id}.{key}={v} 低于下限 {sp.min}")
            if sp.max is not None and v > sp.max:
                raise ValueError(f"{strategy.id}.{key}={v} 高于上限 {sp.max}")
        elif sp.type == "select":
            valid = [o["value"] for o in (sp.options or [])] + list(sp.hidden_values or [])
            if v not in valid:
                raise ValueError(f"{strategy.id}.{key}={v} 不在选项 {valid} 中")
        clean[key] = v
    return clean
