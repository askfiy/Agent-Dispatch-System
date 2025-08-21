import os
from typing import Any

from pydantic import Field, MySQLDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


env = os.getenv("ENV", "local")
configure_path = os.path.join(".", ".env", f".{env}.env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, env_file=configure_path)

    SYNC_DB_URL: str = Field(examples=["mysql+pymysql://root:123@127.0.0.1:3306/db1"])
    ASYNC_DB_URL: str = Field(examples=["mysql+asyncmy://root:123@127.0.0.1:3306/db1"])
    REDIS_SENTINELS: str = Field(
        examples=["127.0.0.1:6379;127.0.0.1:6380;127.0.0.1:6381;"]
    )
    REDIS_MASTER_NAME: str = Field(examples=["mymaster"])
    REDIS_PASSWORD: str = Field(examples=["..."])
    REDIS_SENTINEL_PASSWORD: str = Field(examples=["..."])
    REDIS_DB: str = Field(examples=["1"])

    @field_validator("SYNC_DB_URL", "ASYNC_DB_URL", mode="before")
    @classmethod
    def _validate_db_url(cls, db_url: Any) -> str:
        if not isinstance(db_url, str):
            raise TypeError("Database URL must be a string")
        try:
            # 验证是否符合 MySQLDsn 类型.
            MySQLDsn(db_url)
        except Exception as e:
            raise ValueError(f"Invalid MySQL DSN: {e}") from e

        return str(db_url)


env_helper = Settings()  # pyright: ignore[reportCallIssue]
