"""اختبارات «الموظفين» (0021: القاعدة + الكود EMP-001 + القسم نص حر + الـ Service + الصلاحيات + جهازين + الشاشة)."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from datetime import date

import psycopg
import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.repositories.employee_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.employee_service import EmployeeService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

BLANK = {"name_en": None, "job_title": None, "department": None, "mobile": None, "phone": None, "email": None,
         "address": None, "national_id": None, "hire_date": None, "is_sales_rep": False, "is_treasurer": False,
         "is_storekeeper": False, "takes_custody": False, "notes": None, "is_active": True}


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
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _text: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "service": EmployeeService(temp_db)}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.employees_screen import EmployeesScreen

    return EmployeesScreen(EmployeeService(db))


def ins(db, name, **extra):
    """INSERT مباشر (عشان نجرّب القاعدة من غير الـ Service)."""
    cols = {"seq": extra.pop("seq", int(db.fetch_value("SELECT coalesce(max(seq), 0) + 1 FROM party.employees"))),
            "name_ar": name, **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO party.employees ({names}) VALUES ({marks}) RETURNING id", list(cols.values()))


def emp(db, code):
    return db.fetch_one("SELECT * FROM party.employees WHERE code = %s", [code])


def payload(**extra):
    return {**BLANK, "name_ar": "م/ كريم عادل", **extra}


# ---- القاعدة ---------------------------------------------------------------
def test_code_format():
    assert [format_code(n) for n in (1, 12, 999, 1000)] == ["EMP-001", "EMP-012", "EMP-999", "EMP-1000"]


def test_database_rules(env):
    db = env["db"]
    assert db.fetch_value("SELECT count(*) FROM party.employees") == 0          # مفيش موظفين جاهزين
    eid = ins(db, "كريم", national_id="28505050100022", hire_date=date(2018, 6, 15))
    e = emp(db, "EMP-001")
    assert e["id"] == eid and e["row_version"] == 1 and e["is_sales_rep"] is False
    ins(db, "موظف 1000", seq=1000)
    assert emp(db, "EMP-1000")["seq"] == 1000
    with pytest.raises(psycopg.errors.GeneratedAlways):
        db.execute("INSERT INTO party.employees (seq, code, name_ar) VALUES (5, 'X', 'x')")
    with pytest.raises(psycopg.errors.UniqueViolation):                         # المسلسل مايتكررش
        ins(db, "تاني", seq=1)
    with pytest.raises(psycopg.errors.UniqueViolation):                         # الاسم مايتكررش
        ins(db, "  كريم ")
    with pytest.raises(psycopg.errors.UniqueViolation):                         # الرقم القومي مايتكررش
        ins(db, "تالت", national_id="28505050100022")
    ins(db, "من غير رقم 1")
    ins(db, "من غير رقم 2")                                                     # الفاضي يتكرر عادي
    for extra in (dict(seq=0), dict(national_id="123"), dict(national_id="2850505010002X"),
                  dict(department="  "), dict(hire_date=date(1900, 1, 1))):
        with pytest.raises(psycopg.errors.CheckViolation):
            ins(db, "x", **extra)
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE party.employees SET seq = 77 WHERE id = %s", [eid])
    db.execute("UPDATE party.employees SET mobile = '01000000001', is_sales_rep = true WHERE id = %s", [eid])
    assert emp(db, "EMP-001")["row_version"] == 2


# ---- الـ Service -------------------------------------------------------------
def test_service_save_rules_and_list(env):
    db, service = env["db"], env["service"]
    assert service.new_defaults() == {"code": "EMP-001", "is_active": True}
    eid = service.save_employee(None, payload(job_title="مدير مبيعات", department="  المبيعات ",
                                              national_id="2850505 0100022", hire_date="2018-06-15",
                                              mobile="01000002002", is_sales_rep=True, takes_custody=True))
    e = service.get_employee(eid)
    assert (e["code"], e["department"], e["national_id"], e["hire_date"], e["is_sales_rep"], e["takes_custody"],
            e["is_treasurer"]) == ("EMP-001", "المبيعات", "28505050100022", date(2018, 6, 15), True, True, False)
    assert EmployeeService.roles_text(e) == "مندوب · عهدة"
    audit = db.fetch_one("SELECT action, entity_label, record_label FROM sec.audit_log "
                         "WHERE entity = 'party.employees' ORDER BY id DESC LIMIT 1")
    assert audit == {"action": "create", "entity_label": "موظف", "record_label": "EMP-001 م/ كريم عادل"}
    with pytest.raises(BusinessError, match="بنفس الاسم .* ميّزه"):
        service.save_employee(None, payload(name_ar="م/  كريم عادل"))
    with pytest.raises(BusinessError, match="متسجل قبل كده للموظف «م/ كريم عادل» \\(EMP-001\\)"):
        service.save_employee(None, payload(name_ar="تاني", national_id="28505050100022"))
    for bad, msg in ((dict(name_ar=""), "اسم الموظف"), (dict(national_id="123"), "14 رقم \\(مكتوب 3\\)"),
                     (dict(national_id="2850505010002X"), "14 رقم"), (dict(email="no-at"), "الإيميل"),
                     (dict(hire_date="bad"), "تاريخ التعيين"), (dict(hire_date=date(1900, 1, 1)), "برّه"),
                     (dict(department="ق" * 81), "80 حرف")):
        with pytest.raises(BusinessError, match=msg):
            service.save_employee(None, payload(**{"name_ar": "جديد", **bad}))
    # القسم نص حر: الجديد بيتضاف للقايمة · نفس القسم بشكل تاني بياخد كتابة الموجود
    service.save_employee(None, payload(name_ar="أ/ هاني سعد", department="Stores", is_storekeeper=True))
    service.save_employee(None, payload(name_ar="أ/ ياسر نبيل", department="  stores  ", takes_custody=True))
    assert service.department_choices() == ["Stores", "المبيعات"]
    assert service.get_employee(service.list_employees("ياسر")[0]["id"])["department"] == "Stores"
    service.save_employee(None, payload(name_ar="أ/ نهى صلاح"))                    # من غير قسم ولا أدوار
    # تعديل بالإصدار + الكود ثابت
    service.save_employee(eid, {**e, "seq": 99, "code": "EMP-099", "job_title": "مدير المبيعات"}, e["row_version"])
    e2 = service.get_employee(eid)
    assert (e2["code"], e2["job_title"], e2["row_version"]) == ("EMP-001", "مدير المبيعات", 2)
    # الإيقاف / التفعيل
    service.set_active(eid, False, e2["row_version"])
    assert service.get_employee(eid)["is_active"] is False
    service.set_active(eid, False, 3)                                              # نفس الحالة ← مفيش تغيير
    assert service.get_employee(eid)["row_version"] == 3
    # القائمة: البحث والفلاتر والصفحات والعدد
    codes = lambda rows: [r["code"] for r in rows]                                 # noqa: E731
    assert codes(service.list_employees()) == ["EMP-001", "EMP-002", "EMP-003", "EMP-004"]
    for key in ("EMP-002", "emp-002", "002", "هاني", "stores"):
        assert "EMP-002" in codes(service.list_employees(key)), key
    assert codes(service.list_employees("مدير")) == ["EMP-001"]                    # الوظيفة
    assert codes(service.list_employees("2850505")) == ["EMP-001"]                 # الرقم القومي
    assert codes(service.list_employees("01000002")) == ["EMP-001"]                # الموبايل
    assert codes(service.list_employees(status="inactive")) == ["EMP-001"]
    assert codes(service.list_employees(role="custody")) == ["EMP-001", "EMP-003"]
    assert codes(service.list_employees(role="storekeeper")) == ["EMP-002"]
    assert codes(service.list_employees(role="bogus")) == codes(service.list_employees())
    assert codes(service.list_employees(page=2, page_size=3)) == ["EMP-004"]
    assert service.count_employees() == {"total": 4, "active": 3, "inactive": 1, "sales_reps": 1}
    assert len(service.lookup_employees()) == 4
    service.delete_employee(eid, 3)
    assert emp(db, "EMP-001") is None
    assert service.new_defaults()["code"] == "EMP-005"                             # بيكمّل بعد الأكبر


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"hr.employees.view", "hr.employees.create", "hr.employees.save"})
    eid = service.save_employee(None, payload())
    e = service.get_employee(eid)
    with pytest.raises(PermissionDenied):
        service.save_employee(eid, {**e, "name_ar": "x"}, e["row_version"])
    with pytest.raises(PermissionDenied):
        service.set_active(eid, False, e["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_employee(eid, e["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'hr.employees.%%'")}
    assert codes == {f"hr.employees.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · الإضافة في نفس اللحظة بتاخد أكواد مختلفة ·
    نفس الرقم القومي من جهازين في نفس اللحظة ← واحد بس اللي بيتسجل."""
    db = env["db"]
    dev1, dev2 = EmployeeService(db), EmployeeService(Database(db.settings))
    try:
        eid = dev1.save_employee(None, payload())
        seen1, seen2 = dev1.get_employee(eid), dev2.get_employee(eid)
        dev2.save_employee(eid, {**seen2, "job_title": "مدير"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_employee(eid, {**seen1, "job_title": "محاسب"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(eid, False, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_employee(eid, seen1["row_version"])
        assert dev1.get_employee(eid)["job_title"] == "مدير"
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_employee(None, payload(name_ar="موظف الجهاز التاني",
                                                                 national_id="29001010100088"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_seq(tx)                   # الجهاز الأول ماسك قفل الترقيم
            worker = threading.Thread(target=second_device)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            dev1.repository.insert_employee({**BLANK, "seq": first, "name_ar": "موظف الجهاز الأول",
                                             "national_id": "29001010100088"}, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "متسجل قبل كده" in str(results["error"])
        results.clear()

        def third():
            try:
                results["id"] = dev2.save_employee(None, payload(name_ar="موظف تالت"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_seq(tx)
            worker = threading.Thread(target=third)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            dev1.repository.insert_employee({**BLANK, "seq": first, "name_ar": "موظف رابع"}, ex=tx)
        worker.join(10)
        assert "error" not in results
        assert sorted(r["code"] for r in db.fetch_all("SELECT code FROM party.employees")) == \
               ["EMP-001", "EMP-002", "EMP-003", "EMP-004"]
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_new_save_edit_toggle_delete(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    try:
        assert screen.table.rowCount() == 0 and screen.department.count() == 1      # فاضية في الأول
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "EMP-001 (مقترح)"
        assert not screen.hire_date.isEnabled()                                     # التعيين اختياري
        screen.name_ar.setText("م/ كريم عادل")
        screen.job_title.setText("مدير مبيعات")
        screen.department.setEditText("المبيعات")                                    # قسم جديد بالكتابة
        screen.national_id.setText("28505050100022")
        screen.has_hire.setChecked(True)
        assert screen.hire_date.isEnabled()
        screen.hire_date.setDate(QDate(2018, 6, 15))
        screen.role_checks["sales_rep"].setChecked(True)
        screen.role_checks["custody"].setChecked(True)
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "EMP-001"
        e = emp(db, "EMP-001")
        assert (e["department"], e["national_id"], e["hire_date"], e["is_sales_rep"], e["takes_custody"]) == \
               ("المبيعات", "28505050100022", date(2018, 6, 15), True, True)
        texts = [screen.table.item(0, col).text() for col in range(screen.table.columnCount())]
        assert texts[:5] == ["EMP-001", "م/ كريم عادل", "مدير مبيعات", "المبيعات", "مندوب · عهدة"]
        assert screen.department.findText("المبيعات") > 0                          # القسم الجديد في القايمة
        # موظف تاني بيختار القسم من القايمة · من غير تاريخ تعيين
        screen.new_record()
        assert screen.department.currentText() == ""
        screen.name_ar.setText("م/ عمر طارق")
        screen.department.setCurrentIndex(screen.department.findText("المبيعات"))
        screen.save()
        e2 = emp(db, "EMP-002")
        assert e2["department"] == "المبيعات" and e2["hire_date"] is None
        # الرقم القومي الغلط ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("شخص تالت")
        screen.national_id.setText("123")
        screen.save()
        assert "14 رقم" in shown[-1] and screen.mode == "new"
        screen.cancel()
        screen.load(int(e["id"]))
        assert screen.has_hire.isChecked() and screen.hire_date.date() == QDate(2018, 6, 15)
        assert screen.role_checks["sales_rep"].isChecked() and not screen.role_checks["treasurer"].isChecked()
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.edit_record()
        db.execute("UPDATE party.employees SET notes = 'من جهاز تاني' WHERE code = 'EMP-001'")
        screen.job_title.setText("مدير")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and emp(db, "EMP-001")["job_title"] == "مدير مبيعات"
        screen.cancel()
        # فلتر الدور
        screen.role_filter.setCurrentIndex(screen.role_filter.findData("custody"))
        assert screen.table.rowCount() == 1 and "عدد الموظفين: 1" in screen.count_label.text()
        screen.role_filter.setCurrentIndex(0)
        # إيقاف من الشريط + الفلتر
        screen.load(int(e["id"]))
        assert screen.toggle_button.isEnabled()
        screen.toggle_active()
        assert emp(db, "EMP-001")["is_active"] is False
        active_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "active")
        active_chip.click()
        assert screen.table.rowCount() == 1 and screen.current_id is None
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        screen.load(int(e["id"]))
        screen.delete()
        assert emp(db, "EMP-001") is None and screen.current_id is None
    finally:
        screen.close()


def test_screen_paging(env, monkeypatch):
    import app.ui.screens.employees_screen as module

    db = env["db"]
    for i in range(1, 6):
        ins(db, f"موظف {i}")
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    screen = make_screen(db)
    try:
        assert screen.page_label.text() == "صفحة 1 من 3" and screen.table.rowCount() == 2
        assert screen.current_record["code"] == "EMP-001"
        screen.next_button.click()
        screen.next_button.click()
        assert screen.page_label.text() == "صفحة 3 من 3" and screen.table.rowCount() == 1
        screen.search.setText("EMP-004")
        assert screen.page_label.text() == "صفحة 1 من 1" and screen.table.rowCount() == 1
        screen.search.clear()
        screen.new_record()
        assert not screen.next_button.isEnabled() and not screen.role_filter.isEnabled()
        screen.cancel()
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("hr").groups[0]
    assert group.title == "الموظفين" and [t.code for t in group.targets] == ["employees"]
    target = reg.find_target("hr", "employees")
    assert target.name_ar == "الموظفين"
    assert "employees" in SCREEN_FACTORIES
