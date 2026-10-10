"""全局配置。

密钥策略（吸取老项目硬编码泄漏教训）：
- 一律从 .env / 环境变量读取，代码中无任何默认密钥值
- FMP_API_KEY 默认空：仅做回测不需要；用到财报/估值功能时才校验非空
- 钉钉推送由 NOTIFY_ENABLED 总开关控制，默认关闭
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "OxComes"

    # 本地服务
    host: str = "127.0.0.1"
    port: int = 8300

    # API 密钥（无硬编码默认；按需校验）
    fmp_api_key: str = ""
    dingtalk_token: str = ""
    dingtalk_secret: str = ""
    notify_enabled: bool = False

    # 回测默认参数
    initial_capital: float = 10_000.0
    rebalance_cost_bps: float = 2.0

    # yfinance 重试
    fetch_retries: int = 2

    # 路径
    base_dir: Path = Path(__file__).resolve().parent.parent

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def daily_dir(self) -> Path:
        return self.data_dir / "daily"

    @property
    def backtest_curve_dir(self) -> Path:
        return self.data_dir / "backtest"

    @property
    def storage_dir(self) -> Path:
        return self.base_dir / "storage"

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "app.db"

    @property
    def cache_dir(self) -> Path:
        return self.base_dir / "cache"

    @property
    def log_dir(self) -> Path:
        return self.base_dir / "logs"

    def ensure_dirs(self) -> None:
        for p in (self.daily_dir, self.backtest_curve_dir, self.storage_dir,
                  self.cache_dir, self.log_dir):
            p.mkdir(parents=True, exist_ok=True)

    def require_fmp_key(self) -> str:
        if not self.fmp_api_key:
            raise RuntimeError(
                "FMP_API_KEY 未配置：请在 .env 中填入 FMP 免费 Key "
                "(https://site.financialmodelingprep.com/developer/dashboard)"
            )
        return self.fmp_api_key


settings = Settings()
