import os
import typing
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import Field, BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from xyz_config_system import config_env_create
from xyz_config_system.core.enums import SecretPlatform
from xyz_config_system.core.base import GlobalBaseEnv


env = os.getenv("ENV")

assert env, "ENV is not set"

ENV: Literal["test", "local", "esk-test", "esk-dev", "prod"] = typing.cast(
    "Literal['test', 'local', 'esk-test', 'esk-dev', 'prod']", env.lower()
)

ROOT = Path(__file__).parent.absolute()
env_file_path = ROOT.joinpath("../.env")
load_dotenv(env_file_path, override=True)


xyz_celery_config_env = config_env_create(ENV, SecretPlatform.XYZ_CELERY)
xyz_celery_env = xyz_celery_config_env.xyz_celery


xyz_platform_config_env = config_env_create(ENV, SecretPlatform.XYZ_PLATFORM)
xyz_platfrom_env = xyz_platform_config_env.xyz_platform


class Settings(GlobalBaseEnv):
    N8N_RDS_HOST: str = xyz_platfrom_env.N8N_RDS_HOST
    N8N_RDS_PORT: int = xyz_platfrom_env.N8N_RDS_PORT
    N8N_RDS_NAME: str = xyz_platfrom_env.N8N_RDS_NAME

    REDIS_SENTINELS: str = Field(
        examples=["127.0.0.1:6379;127.0.0.1:6380;127.0.0.1:6381;"],
        default=xyz_celery_env.CELERY_REDIS_SENTINELS,
    )

    REDIS_MASTER_NAME: str = Field(
        examples=["mymaster"], default=xyz_celery_env.CELERY_REDIS_MASTER_NAME
    )
    REDIS_PASSWORD: str = Field(
        examples=["..."], default=xyz_celery_env.CELERY_REDIS_PASSWORD
    )
    REDIS_SENTINEL_PASSWORD: str = Field(
        examples=["..."], default=xyz_celery_env.CELERY_REDIS_SENTINEL_PASSWORD
    )
    REDIS_DB: str = Field(examples=["1"], default=xyz_celery_env.CELERY_REDIS_DB)


env_helper = Settings()  # pyright: ignore[reportCallIssue]

