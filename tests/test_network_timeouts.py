"""اختبارات «مهلة القفل والاتصال» (المرحلة 1ج — الشغل على 10–20 جهاز) على قاعدة مؤقتة.

* جهاز ماسك سجل ← الجهاز التاني بيترفض بعد المهلة برسالة عربي (مش بيهنّج)، والفورم بيفضل زي ما هو.
* أمر بيطوّل بيتلغي، والاتصال بيفضل سليم.
* الاتصال لو وقع: القراية بتتعاد لوحدها، والكتابة لأ (ممكن تكون اتنفذت).
* transaction متسابة مفتوحة السيرفر بيقفلها ويفك أقفالها.
* التحديث وقت الفتح (migrations) مابيتأثرش بالمهلات.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.config.db_errors import describe_db_error
from app.database import db as dbmod
from app.database import migrator
from app.database.db import Database, is_read_only
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.tax_code_service import TaxCodeService
from app.ui.common import messages

BUSY = "مستخدم من جهاز تاني"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    monkeypatch.setattr(dbmod, "LOCK_TIMEOUT_MS", 400)        # أسرع في الاختبار (الحقيقي 5 ثواني)
    temp_db.close()                                            # الاتصال الجاي ياخد المهلة الجديدة
    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    other = Database(temp_db.settings)                         # «الجهاز التاني»
    shown: list[str] = []
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    yield {"db": temp_db, "other": other, "shown": shown}
    other.close()
    SESSION.clear()


def test_connection_settings():
    kwargs = Database().connect_kwargs()
    assert kwargs["keepalives"] == 1 and kwargs["keepalives_idle"] == 30 and kwargs["keepalives_count"] == 3
    assert "lock_timeout=5000" in kwargs["options"] and "statement_timeout=30000" in kwargs["options"]
    assert "idle_in_transaction_session_timeout=60000" in kwargs["options"]
    assert is_read_only("  SELECT 1") and is_read_only("show lock_timeout")
    assert not is_read_only("UPDATE x SET y = 1") and not is_read_only(None)


def test_session_has_timeouts_and_migrate_restores_them(env):
    db = env["db"]
    assert db.fetch_value("SHOW lock_timeout") == "400ms"
    assert db.fetch_value("SHOW statement_timeout") == "30s"
    assert db.fetch_value("SHOW idle_in_transaction_session_timeout") == "1min"
    migrator.migrate(db)                                       # مفيش جديد — بس بيقفل ويفك
    assert db.fetch_value("SHOW lock_timeout") == "400ms"      # المهلة رجعت بعد التحديث


def test_locked_row_is_rejected_quickly_with_arabic_message(env):
    db, other = env["db"], env["other"]
    vat = next(r for r in TaxCodeService(db).list_codes() if r["code"] == "VAT14")
    with other.transaction() as cur:                           # الجهاز التاني ماسك السجل (بيحفظ)
        cur.execute("SELECT id FROM acc.tax_codes WHERE id = %s FOR UPDATE", [vat["id"]])
        started = time.monotonic()
        with pytest.raises(psycopg.errors.LockNotAvailable) as err:
            TaxCodeService(db).save_code(vat["id"], {**vat, "name_ar": "x"}, vat["row_version"])
        assert time.monotonic() - started < 3                  # مااستناش لحد ما التاني يخلص
    assert describe_db_error(err.value) == "السجل ده مستخدم من جهاز تاني دلوقتي — جرّب كمان شوية."
    assert TaxCodeService(db).get_code(vat["id"])["name_ar"] != "x"
    TaxCodeService(db).save_code(vat["id"], {**vat, "name_ar": "بعد ما اتفك"}, vat["row_version"])
    assert TaxCodeService(db).get_code(vat["id"])["name_ar"] == "بعد ما اتفك"


def test_screen_stays_in_edit_while_record_is_locked(env):
    from app.ui.screens.tax_codes_screen import TaxCodesScreen

    db, other = env["db"], env["other"]
    screen = TaxCodesScreen(TaxCodeService(db))
    vat_id = next(r["id"] for r in TaxCodeService(db).list_codes() if r["code"] == "VAT14")
    screen.load(vat_id)
    screen.edit_record()
    screen.name_ar.setText("تعديلي")
    with other.transaction() as cur:
        cur.execute("SELECT id FROM acc.tax_codes WHERE id = %s FOR UPDATE", [vat_id])
        screen.save()
    assert BUSY in env["shown"][-1]
    assert screen.mode == "edit" and screen.name_ar.text() == "تعديلي"                 # اللي اتكتب مااتمسحش
    screen.save()                                              # بعد ما الجهاز التاني خلص ← بيتحفظ
    assert env["shown"][-1] == "saved" and TaxCodeService(db).get_code(vat_id)["name_ar"] == "تعديلي"


def test_statement_timeout_cancels_but_keeps_connection(env):
    db = env["db"]
    pid = db.fetch_value("SELECT pg_backend_pid()")
    db.execute("SET statement_timeout = 200")
    with pytest.raises(psycopg.errors.QueryCanceled) as err:
        db.fetch_value("SELECT pg_sleep(2)")
    assert "وقت أطول من المسموح" in describe_db_error(err.value)
    db.execute("RESET statement_timeout")
    assert db.fetch_value("SELECT pg_backend_pid()") == pid      # نفس الاتصال — مااتقفلش


def test_lost_connection_reads_retry_writes_do_not(env):
    db, other = env["db"], env["other"]
    pid = db.fetch_value("SELECT pg_backend_pid()")
    other.fetch_value("SELECT pg_terminate_backend(%s)", [pid])  # الشبكة «قطعت»
    assert db.fetch_value("SELECT 41 + 1") == 42                # القراية اتعادت لوحدها على اتصال جديد
    assert db.fetch_value("SELECT pg_backend_pid()") != pid

    pid = db.fetch_value("SELECT pg_backend_pid()")
    other.fetch_value("SELECT pg_terminate_backend(%s)", [pid])
    with pytest.raises(psycopg.OperationalError):               # الكتابة مابتتعادش لوحدها
        db.execute("UPDATE acc.tax_codes SET name_ar = name_ar WHERE code = 'VAT0'")
    assert db.fetch_value("SELECT 1") == 1                      # والمرة الجاية الاتصال بيتفتح جديد


def test_idle_transaction_is_closed_and_locks_released(env):
    db, other = env["db"], env["other"]
    other.execute("SET idle_in_transaction_session_timeout = 300")
    with pytest.raises(psycopg.OperationalError) as err:
        with other.transaction() as cur:
            cur.execute("SELECT id FROM acc.tax_codes WHERE code = 'VAT0' FOR UPDATE")
            time.sleep(1.0)                                     # «الجهاز اتساب» والقفل ماسك
            cur.execute("SELECT 1")
    assert describe_db_error(err.value).startswith("الاتصال بالسيرفر اتقطع في نص العملية")
    vat0 = next(r for r in TaxCodeService(db).list_codes() if r["code"] == "VAT0")
    TaxCodeService(db).save_code(vat0["id"], {**vat0, "name_ar": "القفل اتفك"}, vat0["row_version"])
    assert other.fetch_value("SELECT name_ar FROM acc.tax_codes WHERE code = 'VAT0'") == "القفل اتفك"
