"""اختبار الاتصال بقاعدة البيانات (بيستخدمه شاشة إعداد الاتصال وتسلسل فتح البرنامج)."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from app.config import connection_config as cc
from app.config.db_errors import describe_connection_error
from app.database.db import APPLICATION_NAME, CONNECT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    message: str
    server_version: str = ""


def test_connection(profile: cc.ConnectionProfile) -> ConnectionTestResult:
    errors = profile.validate()
    if errors:
        return ConnectionTestResult(False, " | ".join(errors))
    try:
        with psycopg.connect(
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            application_name=APPLICATION_NAME,
            **profile.conninfo(),
        ) as conn:
            version = conn.execute("SHOW server_version").fetchone()[0]
    except Exception as exc:  # noqa: BLE001 — كل الأخطاء بتتحول لرسالة عربي
        return ConnectionTestResult(False, describe_connection_error(
            exc,
            host=profile.server_address,
            port=profile.server_port,
            db_name=profile.db_name,
            password=profile.db_password,
        ))
    return ConnectionTestResult(
        True,
        f"تم الاتصال بقاعدة «{profile.db_name}» بنجاح (PostgreSQL {version}).",
        str(version),
    )
