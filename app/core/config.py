"""Application configuration.

Everything tunable lives here and can be overridden through environment variables
(`RUPESHIELD_...`) or a local ``.env`` file, so configuration never has to be edited
inside source code. See ``.env.example`` for the documented variables.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = BASE_DIR / "models"
DEFAULT_DATA_DIR = BASE_DIR / "data"


class ThresholdSettings(BaseModel):
    """Decision and correlation thresholds.

    Kept as data (not hard-coded constants) because the whole point of the
    Defense Comparison Lab is to show that these choices change the outcome.
    """

    # -- hybrid risk engine decision bands -----------------------------------
    block_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    flag_threshold: float = Field(default=0.45, ge=0.0, le=1.0)

    # -- rule-based defense (no ML) ------------------------------------------
    rule_velocity_10min: int = Field(default=5, ge=1)
    rule_velocity_1h: int = Field(default=8, ge=1)
    rule_amount_zscore: float = Field(default=4.0, ge=0.0)
    rule_amount_over_avg: float = Field(default=5.0, ge=0.0)

    # -- security-event correlation ------------------------------------------
    failed_login_burst: int = Field(default=5, ge=1)
    failed_login_window_minutes: int = Field(default=10, ge=1)
    correlation_lookback_minutes: int = Field(default=30, ge=1)
    # 400.00 INR, i.e. roughly 4x the median legitimate transaction in the synthetic
    # bank, so the rule fires on attack-sized amounts but not on ordinary traffic.
    new_device_high_value_minor: int = Field(default=40_000, ge=0)
    coordinated_fanout_beneficiaries: int = Field(default=3, ge=1)


class Settings(BaseSettings):
    """Runtime settings for the RupeeShield platform."""

    model_config = SettingsConfigDict(
        env_prefix="RUPESHIELD_",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "RupeeShield"
    app_version: str = "1.0.0"
    environment: str = "local"
    log_level: str = "INFO"

    # Storage. SQLite by default: no server, no credentials, no cost.
    database_url: str = f"sqlite:///{(BASE_DIR / 'rupeeshield.db').as_posix()}"

    model_dir: Path = DEFAULT_MODEL_DIR
    data_dir: Path = DEFAULT_DATA_DIR

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Simulation limits (guard against accidental runaway runs).
    default_seed: int = 42
    max_simulation_events: int = Field(default=1500, ge=10)
    max_batch_events: int = Field(default=6000, ge=10)
    default_stream_pace_per_second: float = Field(default=12.0, gt=0.0)

    # API limits.
    max_page_size: int = Field(default=500, ge=1)
    rate_limit_requests_per_minute: int = Field(default=240, ge=1)

    # Training through the API is disabled by default: it is expensive and the
    # documented workflow is `python -m app.ml.train` on the command line.
    enable_api_training: bool = False

    # -- local authentication -------------------------------------------------
    # The product is single-user-per-browser and local: an opaque session token is
    # issued for the configured lifetime and re-checked on every request.
    session_ttl_minutes: int = Field(default=120, ge=1)
    password_min_length: int = Field(default=8, ge=8)
    # PBKDF2-HMAC-SHA256 work factor. Lowered in tests to keep the suite fast;
    # the default is the value the shipped configuration uses.
    password_iterations: int = Field(default=180_000, ge=1_000)
    # After this many consecutive failures the account is temporarily locked.
    max_failed_logins: int = Field(default=5, ge=1)

    # -- demo access ----------------------------------------------------------
    # The demo credentials are published in the README, so they are configuration
    # rather than secrets. There is no production deployment of this project.
    demo_mode_enabled: bool = True
    demo_customer_email: str = "priya.sharma@rupeeshield.demo"
    demo_customer_password: str = "demo-customer-2026"
    demo_analyst_email: str = "analyst@rupeeshield.demo"
    demo_analyst_password: str = "demo-analyst-2026"

    # -- banking limits -------------------------------------------------------
    # 200,000.00 INR per transfer and 500,000.00 INR per rolling 24 hours.
    transfer_max_minor: int = Field(default=20_000_000, ge=1)
    transfer_daily_limit_minor: int = Field(default=50_000_000, ge=1)
    transfer_min_minor: int = Field(default=100, ge=1)  # 1.00 INR

    thresholds: ThresholdSettings = ThresholdSettings()

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
