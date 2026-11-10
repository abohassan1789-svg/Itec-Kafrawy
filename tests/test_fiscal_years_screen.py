"""اختبارات «السنة المالية والفترات» (القاعدة + الـ Service + الشاشة) على قاعدة مؤقتة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date

import psycopg
import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services import permission_registry as reg
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.company_service import CompanyService
from app.services.errors import BusinessError, PermissionDenied
from app.services.fiscal_year_service import FiscalYearService, build_periods
from app.services.permission_service import PermissionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.user_service import UserService
from app.ui.common import messages
from app.ui.dialogs import action_password_dialog

FAST = PasswordHasher(1_000)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    answers = {"save": True, "yes": True}
    monkeypatch.setattr(messages, "ask_save", lambda _p: answers["save"])
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _x: answers["yes"])
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    yield {"db": temp_db, "shown": shown, "answers": answers, "service": FiscalYearService(temp_db)}
    SESSION.clear()


def year(code: str, start: date, end: date, **extra) -> dict:
    return {"year_code": code, "name_ar": f"السنة المالية {code}", "start_date": start, "end_date": end, **extra}


def add_years(service: FiscalYearService, *codes: int) -> dict[int, int]:
    return {c: service.save_year(None, year(str(c), date(c, 1, 1), date(c, 12, 31))) for c in codes}


def make_screen(db):
    from app.ui.screens.fiscal_years_screen import FiscalYearsScreen

    return FiscalYearsScreen(FiscalYearService(db))


# ---- القاعدة ---------------------------------------------------------------
def test_database_rules(env):
    db = env["db"]
    ins = "INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES (%s, %s, %s)"
    db.execute(ins, ["2026", date(2026, 1, 15), date(2026, 12, 31)])
    periods = db.fetch_all("SELECT period_no, name_ar, start_date, end_date FROM core.fiscal_periods ORDER BY 1")
    assert len(periods) == 12 and periods[0]["name_ar"] == "يناير 2026"
    assert periods[0]["start_date"] == date(2026, 1, 15) and periods[1]["end_date"] == date(2026, 2, 28)
    # نفس اللي بيحسبه البرنامج قبل الحفظ
    assert periods == [{k: p[k] for k in periods[0]} for p in build_periods(date(2026, 1, 15), date(2026, 12, 31))]

    with pytest.raises(psycopg.errors.ExclusionViolation):            # تداخل
        db.execute(ins, ["X", date(2026, 6, 1), date(2027, 5, 31)])
    with pytest.raises(psycopg.errors.RaiseException, match="فجوة"):    # فجوة
        db.execute(ins, ["2028", date(2028, 1, 1), date(2028, 12, 31)])
    with pytest.raises(psycopg.errors.CheckViolation):                # أكتر من 24 شهر
        db.execute(ins, ["L", date(2027, 1, 1), date(2029, 1, 1)])
    with pytest.raises(psycopg.errors.CheckViolation):                # من بعد إلى
        db.execute(ins, ["R", date(2027, 2, 1), date(2027, 1, 1)])
    with pytest.raises(psycopg.errors.UniqueViolation):               # الكود متكرر
        db.execute(ins, ["2026", date(2027, 1, 1), date(2027, 12, 31)])
    with pytest.raises(psycopg.errors.RaiseException, match="لوحدها"):  # الفترات مابتتعدّلش بإيد
        db.execute("DELETE FROM core.fiscal_periods")
    db.execute(ins, ["2027-28", date(2027, 1, 1), date(2028, 12, 31)])   # 24 شهر بالظبط مسموح
    assert db.fetch_value("SELECT count(*) FROM core.fiscal_periods") == 36

    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2026'")
    with pytest.raises(psycopg.errors.RaiseException, match="مقفولة"):
        db.execute("UPDATE core.fiscal_years SET notes = 'x' WHERE year_code = '2026'")
    with pytest.raises(psycopg.errors.RaiseException, match="مقفولة"):
        db.execute("DELETE FROM core.fiscal_years WHERE year_code = '2026'")
    db.execute("UPDATE core.fiscal_years SET status = 'open', closed_at = NULL WHERE year_code = '2026'")
    db.execute("DELETE FROM core.fiscal_years WHERE year_code = '2027-28'")     # الأخيرة ← تتمسح وفتراتها معاها
    assert db.fetch_value("SELECT count(*) FROM core.fiscal_periods") == 12


# ---- الـ Service ------------------------------------------------------------
def test_suggest_new_year_uses_company_month_then_follows_last(env):
    service = env["service"]
    CompanyService(env["db"]).save_company({"name_ar": "شركة", "fiscal_year_start_month": 7})
    first = service.suggest_new_year()
    today = date.today()
    start_year = today.year if today.month >= 7 else today.year - 1
    assert first["start_date"] == date(start_year, 7, 1) and first["end_date"] == date(start_year + 1, 6, 30)
    assert first["year_code"] == f"{start_year}/{start_year + 1}" and len(first["periods"]) == 12
    service.save_year(None, first)
    nxt = service.suggest_new_year()
    assert nxt["start_date"] == date(start_year + 1, 7, 1) and nxt["end_date"] == date(start_year + 2, 6, 30)


def test_save_validation_messages(env):
    service = env["service"]
    add_years(service, 2025)
    cases = [
        (year("", date(2026, 1, 1), date(2026, 12, 31)), "مطلوبة"),
        (year("2026", date(2026, 2, 1), date(2026, 1, 1)), "قبل"),
        (year("2026", date(2026, 1, 1), date(2028, 1, 1)), "24 شهر"),
        (year("2025", date(2026, 1, 1), date(2026, 12, 31)), "متسجلة قبل كده"),
        (year("2026", date(2025, 6, 1), date(2026, 5, 31)), "متداخلة"),
        (year("2027", date(2027, 1, 1), date(2027, 12, 31)), "فجوة"),
    ]
    for data, text in cases:
        with pytest.raises(BusinessError, match=text):
            service.save_year(None, data)
    assert env["db"].fetch_value("SELECT count(*) FROM core.fiscal_years") == 1


def test_save_update_delete_with_audit(env):
    db, service = env["db"], env["service"]
    ids = add_years(service, 2024, 2025, 2026)
    service.save_year(ids[2026], year("2026", date(2026, 1, 1), date(2026, 12, 31), notes="سنة التشغيل"))
    with pytest.raises(BusinessError, match="في النص"):
        service.delete_year(ids[2025])
    service.delete_year(ids[2026])
    logs = db.fetch_all("SELECT action, entity_id, details FROM sec.audit_log WHERE entity='core.fiscal_years' "
                        "ORDER BY id")
    assert [log["action"] for log in logs] == ["create", "create", "create", "update", "delete"]
    assert logs[3]["details"]["changed"] == ["notes"]
    assert [r["year_code"] for r in service.list_years()] == ["2025", "2024"]      # الأحدث فوق


def test_lock_unlock_audit_and_closed_year_protected(env):
    db, service = env["db"], env["service"]
    ids = add_years(service, 2025, 2026)
    service.set_closed(ids[2025], True)
    row = service.get_year(ids[2025])
    assert row["status"] == "closed" and row["closed_by_name"] == DEFAULT_ADMIN_USERNAME and row["closed_at"]
    with pytest.raises(BusinessError, match="مقفولة أصلاً"):
        service.set_closed(ids[2025], True)
    with pytest.raises(BusinessError, match="مقفولة ومينفعش تتعدّل"):
        service.save_year(ids[2025], year("2025", date(2025, 1, 1), date(2025, 12, 31), notes="x"))
    with pytest.raises(BusinessError, match="مقفولة ومينفعش تتمسح"):
        service.delete_year(ids[2025])
    service.set_closed(ids[2025], False)
    assert service.get_year(ids[2025])["closed_by"] is None
    logs = db.fetch_all("SELECT action FROM sec.audit_log WHERE entity='core.fiscal_years' "
                        "AND action IN ('lock', 'unlock') ORDER BY id")
    assert [log["action"] for log in logs] == ["lock", "unlock"]
    assert service.summary(service.list_years()) == {"total": 2, "open": 2, "closed": 0}


def test_lock_permission_and_action_password(env):
    db, service = env["db"], env["service"]
    assert "lock" in reg.PASSWORD_ACTIONS
    assert reg.find_target("sys", "fiscal_years").action_codes() == (
        "view", "create", "edit", "save", "delete", "export", "lock")
    ids = add_years(service, 2026)
    perms = PermissionService(db)
    pid = {p["permission_code"]: int(p["id"]) for p in perms.repository.list_permissions()}
    with pytest.raises(BusinessError):                         # «قفل» من غير كلمة سر ← مرفوض
        perms.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"},
                        {pid["sys.fiscal_years.view"]: True, pid["sys.fiscal_years.lock"]: True})
    role = perms.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"},
                           {pid["sys.fiscal_years.view"]: True, pid["sys.fiscal_years.lock"]: True},
                           {pid["sys.fiscal_years.lock"]: "9999"})
    UserService(db, hasher=FAST).create_user({"username": "ahmed", "full_name": "أحمد", "role_id": role}, "pw")
    clerk_role = perms.save_role(None, {"role_code": "VIEW", "role_name_ar": "عرض بس"},
                                 {pid["sys.fiscal_years.view"]: True})
    UserService(db, hasher=FAST).create_user({"username": "mona", "full_name": "منى", "role_id": clerk_role}, "pw")

    SESSION.clear()
    assert AuthService(db).login("mona", "pw").ok
    with pytest.raises(PermissionDenied):
        service.set_closed(ids[2026], True)
    with pytest.raises(PermissionDenied):
        service.save_year(None, year("2027", date(2027, 1, 1), date(2027, 12, 31)))

    SESSION.clear()
    assert AuthService(db).login("ahmed", "pw").ok
    assert service.needs_lock_password()
    with pytest.raises(BusinessError, match="غلط"):
        service.set_closed(ids[2026], True, "1111")
    service.set_closed(ids[2026], True, "9999")
    assert service.get_year(ids[2026])["status"] == "closed"


# ---- الشاشة -----------------------------------------------------------------
def test_screen_layout_and_first_open(env):
    service = env["service"]
    ids = add_years(service, 2024, 2025, date.today().year)
    service.set_closed(ids[2024], True)
    screen = make_screen(env["db"])
    assert screen.current_id == ids[date.today().year]                       # بيفتح على السنة الحالية
    assert screen.status_label.text() == "● مفتوحة — الحالية"
    assert screen.periods_table.rowCount() == 12 and "12 شهر" in screen.periods_box.title()
    assert screen.count_label.text() == "عدد السنين: 3 · مفتوحة 2 · مقفولة 1"
    statuses = [screen.table.item(r, 4).text() for r in range(screen.table.rowCount())]
    assert statuses == ["● مفتوحة — الحالية", "مفتوحة", "🔒 مقفولة"]
    assert screen.new_button.text() == "سنة جديدة" and screen.lock_button.isEnabled()
    # ترتيب شريط الأدوات زي النموذج: … تحديث · قفل/فتح · تصدير إكسل · خروج
    bar = screen.lock_button.parentWidget().layout()
    order = [bar.itemAt(i).widget() for i in range(bar.count()) if bar.itemAt(i).widget() is not None]
    tail = [w for w in order if w in (screen.refresh_button, screen.lock_button, screen.export_button,
                                       screen.exit_button)]
    assert tail == [screen.refresh_button, screen.lock_button, screen.export_button, screen.exit_button]
    assert not screen.year_code.isEnabled() and not screen.start_date.isEnabled()


def test_screen_new_year_suggestion_and_save(env):
    db = env["db"]
    add_years(env["service"], 2026)
    screen = make_screen(db)
    screen.new_record()
    assert screen.mode == "new" and screen.year_code.text() == "2027"
    assert screen.start_date.date() == QDate(2027, 1, 1) and screen.end_date.date() == QDate(2027, 12, 31)
    assert screen.status_label.text() == "● مفتوحة (سنة جديدة)" and screen.periods_table.rowCount() == 12
    screen.end_date.setDate(QDate(2028, 6, 30))                              # 18 شهر ← الفترات بتتحدّث
    assert screen.periods_table.rowCount() == 18
    screen.save()
    assert env["shown"] == ["saved"] and screen.mode == "view"
    assert db.fetch_value("SELECT count(*) FROM core.fiscal_periods WHERE fiscal_year_id = %s",
                          [screen.current_id]) == 18
    assert screen.count_label.text().startswith("عدد السنين: 2")


def test_screen_gap_error_shown_in_arabic(env):
    add_years(env["service"], 2026)
    screen = make_screen(env["db"])
    screen.new_record()
    screen.start_date.setDate(QDate(2028, 1, 1))
    screen.save()
    assert any("فجوة" in s for s in env["shown"]) and screen.mode == "new"


def test_screen_lock_toggle_disables_edit_and_delete(env, monkeypatch):
    service = env["service"]
    ids = add_years(service, 2025, 2026)
    screen = make_screen(env["db"])
    screen.load(ids[2026])
    assert screen.edit_button.isEnabled() and screen.delete_button.isEnabled()
    env["answers"]["yes"] = False
    screen.toggle_lock()                                                     # «لا» ← ولا حاجة
    assert service.get_year(ids[2026])["status"] == "open"
    env["answers"]["yes"] = True
    screen.toggle_lock()
    assert service.get_year(ids[2026])["status"] == "closed" and "اتقفلت" in env["shown"][-1]
    assert screen.status_label.text().startswith("🔒 مقفولة — user")
    assert not screen.edit_button.isEnabled() and not screen.delete_button.isEnabled()
    assert screen.closed_hint.isVisibleTo(screen)
    screen.toggle_lock()
    assert service.get_year(ids[2026])["status"] == "open" and screen.edit_button.isEnabled()


def test_screen_asks_action_password_for_non_admin(env, monkeypatch):
    db, service = env["db"], env["service"]
    ids = add_years(service, 2026)
    perms = PermissionService(db)
    pid = {p["permission_code"]: int(p["id"]) for p in perms.repository.list_permissions()}
    role = perms.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"},
                           {pid["sys.fiscal_years.view"]: True, pid["sys.fiscal_years.lock"]: True},
                           {pid["sys.fiscal_years.lock"]: "9999"})
    UserService(db, hasher=FAST).create_user({"username": "ahmed", "full_name": "أحمد", "role_id": role}, "pw")
    SESSION.clear()
    assert AuthService(db).login("ahmed", "pw").ok
    screen = make_screen(db)
    assert screen.new_button.isHidden() is False and not screen.new_button.isEnabled()   # مالوش «إضافة»
    asked: list[str] = []
    monkeypatch.setattr(action_password_dialog, "ask_action_password",
                        lambda _p, action="": asked.append(action) or None)
    screen.toggle_lock()                                                     # لغى نافذة كلمة السر
    assert asked == ["قفل / فتح السنة"] and service.get_year(ids[2026])["status"] == "open"
    monkeypatch.setattr(action_password_dialog, "ask_action_password", lambda _p, action="": "9999")
    screen.toggle_lock()
    assert service.get_year(ids[2026])["status"] == "closed"
