from functools import lru_cache
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core Application
    PROJECT_NAME: str = "TrustBridge"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    SECRET_KEY: str = "trustbridge-dev-secret-key-32-chars-long"

    # Authentication & JWT
    JWT_SECRET: str = "trustbridge-dev-jwt-secret-key-32-chars-minimum"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # Database
    # PostgreSQL is the default production and development database; SQLite is dev fallback
    DATABASE_URL: str = "postgresql://trustbridge:trustbridge_secret@localhost:5432/trustbridge"

    # PayPal REST API Sandbox
    PAYPAL_CLIENT_ID: Optional[str] = None
    PAYPAL_CLIENT_SECRET: Optional[str] = None
    PAYPAL_MODE: str = "sandbox"
    PAYPAL_API_BASE: str = "https://api-m.sandbox.paypal.com"
    PAYPAL_WEBHOOK_ID: Optional[str] = "WH-TRUSTBRIDGE-DEV-001"
    PAYPAL_WEBHOOK_SECRET: Optional[str] = "trustbridge_paypal_webhook_secret_dev"

    # GitHub Webhook Secret
    GITHUB_WEBHOOK_SECRET: Optional[str] = "trustbridge_github_webhook_secret_dev"

    # AI Swappable Provider (Ollama zero-config default vs OpenAI)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"
    OLLAMA_TIMEOUT_SECONDS: float = 15.0
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o"
    OPENAI_TIMEOUT_SECONDS: float = 15.0

    # FX Service
    FX_RATES_API_URL: str = "https://open.er-api.com/v6/latest/USD"
    FX_CACHE_TTL_SECONDS: int = 3600

    # Graduated Staking & Dispute Protocol
    DEFAULT_STAKE_PERCENTAGE: float = 10.0  # Fallback baseline
    RATE_NEW_ACCOUNT: float = 10.0  # < 7 days OR 0 completed deals: 10%
    RATE_EMERGING: float = 7.0  # 1-4 completed deals, no lost disputes: 7%
    RATE_ESTABLISHED: float = 4.0  # 5-19 completed deals, dispute-loss rate < 10%: 4%
    RATE_PROVEN: float = 2.0  # 20+ completed deals, dispute-loss rate < 5%: 2%
    NEW_ACCOUNT_AGE_DAYS: int = 7  # Accounts < 7 days old are considered new
    DEFAULT_NEW_ACCOUNT_TRUST_SCORE: float = 30.0  # New accounts start at 30 trust score
    DEFAULT_SLASH_PERCENTAGE: float = 50.0  # 50% partial slash by default (25-75% configurable)
    MIN_SLASH_PERCENTAGE: float = 25.0
    MAX_SLASH_PERCENTAGE: float = 75.0


@lru_cache()
def get_settings() -> Settings:
    return Settings()
