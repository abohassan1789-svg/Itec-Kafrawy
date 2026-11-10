"""إعدادات البرنامج: ملف الاتصال أولاً، وبعده متغيرات البيئة (.env) للتطوير، وبعدها القيم الافتراضية."""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.config import connection_config as cc
from app.config.paths import app_root

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


@dataclass(frozen=True)
class Settings:
    db_host: str = "localhost"
    db_port: int = cc.DEFAULT_PORT
    db_name: str = cc.DEFAULT_DB_NAME
    db_user: str = "postgres"
    db_password: str = ""
    db_sslmode: str = "prefer"
    log_level: str = "INFO"
    source: str = "defaults"  # profile / env / defaults — للتشخيص بس

    def profile(self) -> cc.ConnectionProfile:
        return cc.ConnectionProfile(
            server_address=self.db_host,
            server_port=self.db_port,
            db_name=self.db_name,
            db_user=self.db_user,
            db_password=self.db_password,
            sslmode=self.db_sslmode,
        )


def get_settings() -> Settings:
    if load_dotenv is not None:
        load_dotenv(app_root() / ".env")
    log_level = os.getenv("LOG_LEVEL", "INFO")

    profile = None
    try:
        profile = cc.load()
    except Exception:
        profile = None
    if profile is not None:
        return Settings(
            db_host=profile.server_address,
            db_port=int(profile.server_port),
            db_name=profile.db_name,
            db_user=profile.db_user,
            db_password=profile.db_password or os.getenv("DB_PASSWORD", ""),
            db_sslmode=profile.sslmode,
            log_level=log_level,
            source="profile",
        )

    has_env = any(os.getenv(key) for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"))
    return Settings(
        db_host=os.getenv("DB_HOST", "localhost"),
        db_port=int(os.getenv("DB_PORT", str(cc.DEFAULT_PORT))),
        db_name=os.getenv("DB_NAME", cc.DEFAULT_DB_NAME),
        db_user=os.getenv("DB_USER", "postgres"),
        db_password=os.getenv("DB_PASSWORD", ""),
        db_sslmode=os.getenv("DB_SSLMODE", "prefer"),
        log_level=log_level,
        source="env" if has_env else "defaults",
    )
