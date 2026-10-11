"""策略抽象基类与参数规格。

策略只负责一件事：拿到全部标的的历史价格矩阵，输出「t 日收盘信号想要的目标权重」。
防前移（shift）、成本、归一等全部由引擎层统一处理 —— 策略作者无法犯前视错误。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd


@dataclass
class ParamSpec:
    """参数规格：驱动前端表单渲染与网格扫描。"""
    key: str
    label: str
    type: str = "number"          # number | select | tickers | bool
    default: Any = None
    min: Optional[float] = None
    max: Optional[float] = None
    step: Optional[float] = None
    options: Optional[list] = None  # select 的选项 [{value,label}]
    hidden_values: Optional[list] = None  # select 的内部合法值(校验放行,UI 不渲染——
                                          # 如 ma200_panic 的 overheat="band" 由 runner 配跑)
    formatting: Optional[str] = None  # 前端格式化提示
    grid: Optional[list] = None      # 网格扫描取值（CLI/API 用）

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None or k in ("default",)}


@dataclass
class StrategyOutput:
    weights: pd.DataFrame                 # T×N 目标权重（t 日收盘信号）
    cashflows: Optional[pd.Series] = None  # DCA 外部现金流（正=投入）
    notes: list = field(default_factory=list)  # 展示给用户的备注（如合成数据声明）

    def note(self, msg: str) -> None:
        if msg not in self.notes:
            self.notes.append(msg)


class Strategy(ABC):
    """所有策略的基类。子类定义类属性并在 build() 中构造权重。"""

    id: str = ""
    name: str = ""
    description: str = ""
    params: list = []   # list[ParamSpec]

    @abstractmethod
    def build(self, prices: pd.DataFrame, params: dict) -> StrategyOutput:
        """prices: index=交易日, columns=tickers, 值=Adj Close（已含全部请求标的）"""

    def warmup_days(self, params: dict) -> int:
        """指标计算需要的最大历史回看天数（交易日数）。

        runner 用它做「均线全历史预热」：给 build 的价格矩阵起点
        提前这么多交易日（外部回测站口径——窗口起点当天指标已就绪），
        账户/净值/统计仍从用户填的窗口起点起算。无指标依赖的策略返回 0。
        """
        return 0

    # ---- 工具 ----
    @staticmethod
    def equal_weights(index: pd.Index, columns) -> pd.DataFrame:
        n = len(list(columns)) or 1
        return pd.DataFrame(1.0 / n, index=index, columns=columns)

    @classmethod
    def schema(cls) -> dict:
        return {
            "id": cls.id,
            "name": cls.name,
            "description": cls.description,
            "params": [p.to_dict() for p in cls.params],
        }

    @classmethod
    def defaults(cls) -> dict:
        return {p.key: p.default for p in cls.params}
