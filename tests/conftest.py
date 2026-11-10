"""إعدادات الاختبارات.

اختبارات القاعدة (integration) بتعمل **قاعدة مؤقتة** جديدة بإعدادات ملف الاتصال وتمسحها
في الآخر — عمرها ما بتلمس DBITEC نفسها. لو مفيش اتصال بتتعمل skip.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import psycopg
import pytest

from app.config.settings import get_settings
from app.database.db import Database


@pytest.fixture(scope="session")
def base_settings():
    settings = get_settings()
    try:
        with psycopg.connect(
            host=settings.db_host, port=settings.db_port, dbname="postgres",
            user=settings.db_user, password=settings.db_password, connect_timeout=5,
        ):
            pass
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"مفيش اتصال بـ PostgreSQL للاختبارات: {type(exc).__name__}")
    return settings


@pytest.fixture()
def temp_db(base_settings):
    """قاعدة فاضية مؤقتة لكل اختبار."""
    name = f"itec_test_{uuid.uuid4().hex[:10]}"
    admin = dict(host=base_settings.db_host, port=base_settings.db_port, dbname="postgres",
                 user=base_settings.db_user, password=base_settings.db_password, autocommit=True)
    with psycopg.connect(**admin) as conn:
        conn.execute(f'CREATE DATABASE "{name}" ENCODING \'UTF8\' TEMPLATE template0')
    db = Database(replace(base_settings, db_name=name))
    try:
        yield db
    finally:
        db.close()
        with psycopg.connect(**admin) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
