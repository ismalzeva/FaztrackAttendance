from functools import lru_cache
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FAZTRACK_", extra="ignore")
    env: str = "development"
    database_url: str = "sqlite:///./faztrack_attendance.db"
    jwt_secret: str = "development-only-secret-change-before-production"
    access_token_minutes: int = 30
    cors_origins: list[str] = ["http://localhost:3000"]
    demo_seed_password: str | None = None
    demo_worker_pin: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def strong_secret_in_production(self):
        if self.env == "production" and (
            len(self.jwt_secret) < 32 or "development-only" in self.jwt_secret
        ):
            raise ValueError("FAZTRACK_JWT_SECRET must be a unique 32+ character secret")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
