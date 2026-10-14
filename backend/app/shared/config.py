from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://notes:notes@localhost:5432/notes"
    app_env: str = "development"
    app_origin: str = "http://localhost:5173"
    cookie_secure: bool = False
    session_days: int = 7
    rate_limit_requests: int = 10
    rate_limit_seconds: int = 300
    job_lease_seconds: int = 30
    db_pool_size: int = 5
    db_max_overflow: int = 5

    @model_validator(mode="after")
    def validate_production(self):
        if self.app_env == "production" and (
            not self.cookie_secure or not self.app_origin.startswith("https://")
        ):
            raise ValueError("Production requires HTTPS origin and secure cookies")
        if min(self.session_days, self.rate_limit_requests, self.rate_limit_seconds, self.job_lease_seconds) < 1:
            raise ValueError("Limits must be positive")
        if self.db_pool_size < 1 or self.db_max_overflow < 0:
            raise ValueError("Invalid database pool limits")
        return self


@lru_cache
def settings() -> Settings:
    return Settings()
