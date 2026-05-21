from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = Field(...)
    DATABASE_URL_SYNC: str = Field(...)
    REDIS_URL: str = Field(default="redis://redis:6379/0")
    PSP_URL: str = Field(default="http://mock_psp:8001/charge")
    SECRET_KEY: str = Field(...)
    API_KEY_PREFIX: str = Field(default="sk_live_")
    APP_NAME: str = Field(default="tenant-billing-service")
    DEBUG: bool = Field(default=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()