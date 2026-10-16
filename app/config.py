"""环境配置：账号密码只从环境读取，不打印不落库明文。"""
from __future__ import annotations

from functools import lru_cache
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = os.environ.get("ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    ccb_username: str = ""
    ccb_password: str = ""

    web_port: int = 8090

    heartbeat_min: int = 30
    heartbeat_max: int = 60
    # 并发上限：0=无限制。防风控建议保守 2-3
    concurrency: int = 0
    jitter: bool = True
    token_refresh_on_401: bool = True

    login_worker_url: str = "http://login-worker:8000"
    database_url: str = "sqlite+aiosqlite:////data/ccb_course.db"

    api_base: str = "https://api.u.ccb.com"
    component_base: str = "https://component.u.ccb.com"

    request_timeout: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
