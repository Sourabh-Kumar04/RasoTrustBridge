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

    # Database
    DATABASE_URL: str = "sqlite:///./trustbridge.db"

    # PayPal REST API Sandbox
    PAYPAL_CLIENT_ID: Optional[str] = None
    PAYPAL_CLIENT_SECRET: Optional[str] = None
    PAYPAL_MODE: str = "sandbox"
    PAYPAL_API_BASE: str = "https://api-m.sandbox.paypal.com"
    PAYPAL_WEBHOOK_ID: Optional[str] = None

    # AI Contract Builder (Local Ollama)
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.2:3b"
    OLLAMA_TIMEOUT_SECONDS: float = 15.0

    # FX Service
    FX_RATES_API_URL: str = "https://open.er-api.com/v6/latest/USD"
    FX_CACHE_TTL_SECONDS: int = 3600


@lru_cache()
def get_settings() -> Settings:
    return Settings()
