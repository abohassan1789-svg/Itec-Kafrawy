"""اختبارات ترقيم المستندات الأوتوماتيك (القاعدة + الـ Service) على قاعدة مؤقتة."""

from __future__ import annotations

import threading
import time
from datetime import date

import psycopg
import pytest

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.document_numbering_service import DocumentNumberingService
from app.services.errors import BusinessError
from app.services.fiscal_year_service import FiscalYearService
from app.services.permissions_sync_service import PermissionsSyncService


@pytest.fixture()
def env(temp_db):
    migrator.migrate(temp_db)
    # 2025 مقفولة · 2026 · 2027 (ورا بعض من غير فجوة)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date, status, closed_at) "
                    "VALUES ('2025', '2025-01-01', '2025-12-31', 'closed', now())")
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2026', '2026-01-01', '2026-12-31'), ('2027', '2027-01-01', '2027-12-31')")
    ids = {r["year_code"]: int(r["id"]) for r in temp_db.fetch_all("SELECT id, year_code FROM core.fiscal_years")}
    return {"db": temp_db, "service": DocumentNumberingService(temp_db), "ids": ids}


def issue(env, doc_type, d):
    with env["db"].atomic() as tx:
        return env["service"].next_number(doc_type, d, tx)


def test_types_seeded(env):
    types = {t["code"]: t for t in env["service"].list_types()}
    assert len(types) == 10
    assert (types["sales_invoice"]["prefix"], types["sales_invoice"]["digits"]) == ("INV", 5)
    assert types["receipt"]["prefix"] == "RCV" and types["journal"]["prefix"] == "JV"
    assert not any(t["two_step_approval"] for t in types.values())   # الافتراضي: اعتماد = ترحيل


def test_starts_from_one_and_counts_up(env):
    assert env["service"].peek_next("sales_invoice", date(2026, 3, 1)) == "INV-2026-00001"
    assert issue(env, "sales_invoice", date(2026, 3, 1)) == "INV-2026-00001"
    assert issue(env, "sales_invoice", date(2026, 11, 8)) == "INV-2026-00002"
    assert env["service"].peek_next("sales_invoice", date(2026, 5, 5)) == "INV-2026-00003"
    # كل نوع ليه عدّاد لوحده
    assert issue(env, "receipt", date(2026, 3, 1)) == "RCV-2026-00001"
    assert issue(env, "sales_return", date(2026, 3, 1)) == "SRT-2026-0001"
    assert issue(env, "opening", date(2026, 1, 1)) == "OPN-2026-001"


def test_new_year_starts_from_one_automatically(env):
    issue(env, "sales_invoice", date(2026, 12, 31))
    issue(env, "sales_invoice", date(2026, 12, 31))
    assert issue(env, "sales_invoice", date(2027, 1, 1)) == "INV-2027-00001"   # عدّاد 2027 اتعمل لوحده
    assert issue(env, "sales_invoice", date(2026, 12, 30)) == "INV-2026-00003"  # 2026 كمّلت من مكانها
    seqs = env["service"].list_sequences(env["ids"]["2027"])
    assert [(s["doc_type"], s["last_no"]) for s in seqs] == [("sales_invoice", 1)]


def test_failed_save_gives_number_back(env):
    db, svc = env["db"], env["service"]
    with pytest.raises(RuntimeError):
        with db.atomic() as tx:
            assert svc.next_number("payment", date(2026, 6, 1), tx) == "PAY-2026-00001"
            raise RuntimeError("الحفظ فشل")
    assert issue(env, "payment", date(2026, 6, 1)) == "PAY-2026-00001"


def test_overflow_keeps_full_number(env):
    env["db"].execute("INSERT INTO core.document_sequences (doc_type, fiscal_year_id, last_no) "
                      "VALUES ('opening', %s, 999)", [env["ids"]["2026"]])
    assert issue(env, "opening", date(2026, 2, 2)) == "OPN-2026-1000"


def test_errors_in_arabic(env):
    with pytest.raises(BusinessError, match="مقفولة"):
        issue(env, "sales_invoice", date(2025, 6, 1))
    with pytest.raises(BusinessError, match="مفيش سنة مالية فيها تاريخ 01/01/2030"):
        issue(env, "sales_invoice", date(2030, 1, 1))
    with pytest.raises(BusinessError, match="مش متعرّف"):
        issue(env, "nothing", date(2026, 1, 1))
    assert env["service"].peek_next("sales_invoice", date(2030, 1, 1)) is None


def test_database_guards(env):
    db = env["db"]
    issue(env, "journal", date(2026, 4, 4))
    issue(env, "journal", date(2026, 4, 4))
    with pytest.raises(psycopg.errors.RaiseException, match="مابينزلش"):
        db.execute("UPDATE core.document_sequences SET last_no = 1 WHERE doc_type = 'journal'")
    with pytest.raises(psycopg.errors.RaiseException, match="مابيتمسحش"):
        db.execute("DELETE FROM core.document_sequences WHERE doc_type = 'journal'")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO core.document_types (code, name_ar, prefix) VALUES ('x_doc', 'تجربة', 'inv')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("INSERT INTO core.document_types (code, name_ar, prefix) VALUES ('x_doc', 'تجربة', 'INV')")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO core.document_types (code, name_ar, prefix, digits) VALUES ('x_doc', 'تجربة', 'XD', 9)")
    issue(env, "journal", date(2027, 1, 5))
    with pytest.raises(psycopg.errors.RestrictViolation):            # سنة عليها أرقام مابتتمسحش
        db.execute("DELETE FROM core.fiscal_years WHERE id = %s", [env["ids"]["2027"]])


def test_fiscal_year_with_numbers_cannot_be_deleted(env):
    db = env["db"]
    AuthService(db).seed_admin()
    PermissionsSyncService(db).sync()
    SESSION.clear()
    try:
        assert AuthService(db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
        issue(env, "expense", date(2027, 3, 3))
        with pytest.raises(BusinessError, match="اتصرف فيها أرقام مستندات"):
            FiscalYearService(db).delete_year(env["ids"]["2027"])
    finally:
        SESSION.clear()


def test_two_devices_never_get_the_same_number(env):
    """جهاز 1 صرف رقم ولسه ماحفظش ← جهاز 2 بيستنى، وبعد ما 1 يحفظ ياخد اللي بعده."""
    db1, db2 = env["db"], Database(env["db"].settings)
    got: list[str] = []
    try:
        with db1.atomic() as tx1:
            got.append(env["service"].next_number("sales_invoice", date(2026, 7, 7), tx1))

            def second():
                with db2.atomic() as tx2:
                    got.append(DocumentNumberingService(db2).next_number("sales_invoice", date(2026, 7, 7), tx2))

            t = threading.Thread(target=second)
            t.start()
            time.sleep(0.5)
            assert t.is_alive() and len(got) == 1       # التاني مستني القفل
        t.join(10)
        assert got == ["INV-2026-00001", "INV-2026-00002"]
    finally:
        db2.close()
