"""策略注册表（REGISTRY）：用户「选择与指定策略」的载体。

- 前端下拉框读 list_strategies() 自动生成表单（由 params schema 驱动）
- 自定义策略：新建 app/backtest/strategies/custom.py，类上标 @register
  并实现 build()（契约见 base.py）即零侵入接入，无需改动本文件
"""
from __future__ import annotations

from typing import Dict, Type

from app.backtest.strategies.base import Strategy, StrategyOutput, ParamSpec  # noqa: F401

REGISTRY: Dict[str, Type[Strategy]] = {}


def register(cls: Type[Strategy]) -> Type[Strategy]:
    if not cls.id:
        raise ValueError(f"{cls.__name__} 缺少 id 属性")
    if cls.id in REGISTRY:
        raise ValueError(f"策略 id 重复: {cls.id}")
    REGISTRY[cls.id] = cls
    return cls


def get_strategy(strategy_id: str) -> Type[Strategy]:
    if strategy_id not in REGISTRY:
        _ensure_builtin_registered()
        if strategy_id not in REGISTRY:
            raise KeyError(
                f"未注册的策略: {strategy_id}（可用: {', '.join(REGISTRY)}）"
            )
    return REGISTRY[strategy_id]


def list_strategies() -> list:
    _ensure_builtin_registered()
    try:
        import app.backtest.strategies.custom  # noqa: F401
    except ImportError:
        pass
    return [cls.schema() for cls in REGISTRY.values()]


def _ensure_builtin_registered() -> None:
    if not REGISTRY:
        import app.backtest.strategies.builtin  # noqa: F401
