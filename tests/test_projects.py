"""اختبارات «أوامر التوريد» (0016: القاعدة + الترقيم + حماية المركز والعميل + الـ Service + الصلاحيات
+ جهازين + الشاشة)."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.customer_service import CustomerService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.project_service import ProjectService
from app.ui.common import messages


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
    cc = {r["code"]: int(r["id"]) for r in temp_db.fetch_all("SELECT id, code FROM acc.cost_centers")}
    customers = CustomerService(temp_db)
    c1 = customers.save_customer(None, {**customers.new_defaults(), "name_ar": "العاصمة للمقاولات"})
    c2 = customers.save_customer(None, {**customers.new_defaults(), "name_ar": "Delta Dynamics",
                                        "currency_code": "USD"})
    yield {"db": temp_db, "shown": shown, "service": ProjectService(temp_db), "cc": cc, "c1": c1, "c2": c2}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.projects_screen import ProjectsScreen

    return ProjectsScreen(ProjectService(db))


def ins(db, code, center, **extra):
    """INSERT مباشر (عشان نجرّب الـ trigger من غير الـ Service)."""
    cols = {"code": code, "name": extra.pop("name", f"مشروع {code}"), "cost_center_id": center,
            "currency_code": extra.pop("currency_code", "EGP"), **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO acc.projects ({names}) VALUES ({marks}) RETURNING id", list(cols.values()))


def proj(db, code):
    return db.fetch_one("SELECT * FROM acc.projects WHERE code = %s", [code])


def payload(env, **extra):
    data = {**env["service"].new_defaults(), "auto_code": True, "name": "Medical Simulation Building",
            "cost_center_id": env["cc"]["2101"], "customer_id": env["c1"], "amount": "1121076"}
    data.update(extra)
    return data


# ---- القاعدة ---------------------------------------------------------------
def test_database_rules(env):
    db, cc, c1 = env["db"], env["cc"], env["c1"]
    pid = ins(db, "0001-Itec", cc["2101"], customer_id=c1)
    p = proj(db, "0001-Itec")
    assert (p["status"], p["tax_inclusive"], p["doc_kind"], p["row_version"]) == ("open", True, "invoice", 1)
    ins(db, "0002-Itec", cc["2202"])                                            # من غير عميل ← مسموح
    with pytest.raises(psycopg.errors.UniqueViolation):                         # الرقم مايتكررش (من غير كابيتال)
        ins(db, "0001-ITEC", cc["2101"])
    for extra in (dict(amount=-1), dict(status="x"), dict(doc_kind="x"), dict(code=" ", name="x"),
                  dict(start_date=date(2026, 5, 1), end_date=date(2026, 4, 1))):
        with pytest.raises(psycopg.errors.CheckViolation):
            ins(db, extra.pop("code", "X-1"), cc["2101"], **extra)
    with pytest.raises(psycopg.errors.RaiseException, match="رئيسي — اختار مركز فرعي"):
        ins(db, "X-2", cc["21"])
    db.execute("UPDATE acc.cost_centers SET is_active = false WHERE id = %s", [cc["2102"]])
    with pytest.raises(psycopg.errors.RaiseException, match="موقوف — مينفعش تختاره"):
        ins(db, "X-3", cc["2102"])
    db.execute("UPDATE party.customers SET is_active = false WHERE id = %s", [env["c2"]])
    with pytest.raises(psycopg.errors.RaiseException, match="موقوف — مينفعش تختاره لأمر توريد"):
        ins(db, "X-4", cc["2101"], customer_id=env["c2"])
    db.execute("UPDATE core.currencies SET is_active = false WHERE code = 'EUR'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة"):
        ins(db, "X-5", cc["2101"], currency_code="EUR")
    db.execute("UPDATE acc.projects SET status = 'cancelled', amount = 10 WHERE id = %s", [pid])
    assert proj(db, "0001-Itec")["row_version"] == 2
    # العميل اللي عليه أمر مايتمسحش (يتوقف بس) · المركز اللي عليه أمر مايتمسحش ولا يتحوّل رئيسي من «تعديل»
    with pytest.raises(psycopg.errors.RaiseException, match="عليه 1 أمر توريد — وقّفه"):
        db.execute("DELETE FROM party.customers WHERE id = %s", [c1])
    db.execute("UPDATE party.customers SET is_active = false WHERE id = %s", [c1])
    db.execute("UPDATE acc.projects SET notes = 'عادي' WHERE id = %s", [pid])   # الموجود بيفضل يتعدّل
    with pytest.raises(psycopg.errors.RaiseException, match="عليه 1 أمر توريد — وقّفه"):
        db.execute("DELETE FROM acc.cost_centers WHERE id = %s", [cc["2101"]])
    with pytest.raises(psycopg.errors.RaiseException, match="مينفعش يتحوّل رئيسي"):
        db.execute("UPDATE acc.cost_centers SET is_leaf = false WHERE id = %s", [cc["2101"]])
    db.execute("UPDATE acc.cost_centers SET manager_name = 'م. جديد' WHERE id = %s", [cc["2101"]])


def test_numbering(env):
    db, cc = env["db"], env["cc"]
    assert db.fetch_value("SELECT acc.peek_project_no()") == "0001-Itec"
    assert db.fetch_value("SELECT acc.next_project_no()") == "0001-Itec"
    ins(db, "0001-Itec", cc["2101"])
    ins(db, "0002-itec", cc["2101"])                                            # اتكتب بالإيد قبل العدّاد
    assert db.fetch_value("SELECT acc.peek_project_no()") == "0003-Itec"        # بيعدّي المكتوب بالإيد
    assert db.fetch_value("SELECT acc.next_project_no()") == "0003-Itec"
    with pytest.raises(psycopg.errors.RaiseException, match="مابينزلش"):
        db.execute("UPDATE acc.project_numbering SET last_no = 1")
    with pytest.raises(psycopg.errors.RaiseException, match="مابيتمسحش"):
        db.execute("DELETE FROM acc.project_numbering")
    # الصيغة بتتغيّر بسكريبت (لما المستخدم يبعتها) والعدّاد يبدأ من الأول
    db.execute("UPDATE acc.project_numbering SET pattern = 'IC{YY}-{SEQ}', digits = 3, last_no = 0")
    assert db.fetch_value("SELECT acc.next_project_no(%s)", [date(2026, 11, 10)]) == "IC26-001"
    assert db.fetch_value("SELECT acc.format_project_no('{SEQ}', 2, 1234, current_date)") == "1234"
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE acc.project_numbering SET pattern = 'ITEC'")


def test_split_cost_center_moves_projects(env):
    db, cc = env["db"], env["cc"]
    pid = ins(db, "0001-Itec", cc["2101"])
    general = db.fetch_value("SELECT acc.split_cost_center(%s)", [cc["2101"]])
    row = db.fetch_one("SELECT code, name_ar FROM acc.cost_centers WHERE id = %s", [general])
    assert row["code"] == "210101" and row["name_ar"].endswith("— عام")
    assert db.fetch_value("SELECT cost_center_id FROM acc.projects WHERE id = %s", [pid]) == general
    assert db.fetch_value("SELECT is_leaf FROM acc.cost_centers WHERE id = %s", [cc["2101"]]) is False
    # فلتر الأب بيجيب الأوامر اللي تحته
    service = env["service"]
    assert [r["code"] for r in service.list_projects(center_id=cc["2"])] == ["0001-Itec"]
    assert service.list_projects(center_id=cc["1"]) == []


# ---- الـ Service -------------------------------------------------------------
def test_service_save_rules_and_list(env):
    db, service, cc = env["db"], env["service"], env["cc"]
    d = service.new_defaults()
    assert (d["code"], d["status"], d["currency_code"], d["tax_inclusive"], d["doc_kind"], d["order_date"]) == \
           ("0001-Itec", "open", "EGP", True, "invoice", date.today())
    pid = service.save_project(None, payload(env, start_date=date(2026, 11, 1), end_date=date(2027, 3, 1),
                                             order_date=date(2026, 10, 12)))
    p = service.get_project(pid)
    assert (p["code"], p["customer_name"], p["center_code"], p["amount"], p["start_date"]) == \
           ("0001-Itec", "العاصمة للمقاولات", "2101", Decimal("1121076.00"), date(2026, 11, 1))
    audit = db.fetch_one("SELECT action, entity_label, record_label FROM sec.audit_log "
                         "WHERE entity = 'acc.projects' ORDER BY id DESC LIMIT 1")
    assert audit == {"action": "create", "entity_label": "أمر توريد",
                     "record_label": "0001-Itec Medical Simulation Building"}
    # رقم بالإيد + من غير عميل + غير شامل/بيان بالدولار + سنة تانية
    pid2 = service.save_project(None, payload(env, auto_code=False, code="IC25-129", name="صيانة طارئة",
                                              customer_id=None, cost_center_id=cc["24"], tax_inclusive=False,
                                              doc_kind="statement", currency_code="USD", amount="0",
                                              order_date=date(2025, 2, 11)))
    assert service.get_project(pid2)["customer_id"] is None
    assert service.new_defaults()["code"] == "0002-Itec"
    with pytest.raises(BusinessError, match="موجود قبل كده"):
        service.save_project(None, payload(env, auto_code=False, code="ic25-129"))
    for bad, msg in ((dict(name=""), "اسم المشروع"), (dict(cost_center_id=None), "مركز التكلفة"),
                     (dict(amount="-5"), "القيمة"), (dict(amount="abc"), "رقم"), (dict(currency_code=""), "العملة"),
                     (dict(status="x"), "الحالة"), (dict(doc_kind="x"), "فاتورة"),
                     (dict(order_date=None), "تاريخ الأمر"),
                     (dict(start_date=date(2026, 5, 1), end_date=date(2026, 4, 1)), "تاريخ النهاية"),
                     (dict(auto_code=False, code="1" * 31), "30 حرف"),
                     (dict(cost_center_id=cc["21"]), "رئيسي — اختار مركز فرعي")):
        with pytest.raises(BusinessError, match=msg):
            service.save_project(None, payload(env, **bad))
    # تعديل بالإصدار + الرقم يتعدّل + الحالة ملغي
    service.save_project(pid, {**p, "code": "0001-ITEC-A", "status": "cancelled"}, p["row_version"])
    p2 = service.get_project(pid)
    assert (p2["code"], p2["status"], p2["row_version"]) == ("0001-ITEC-A", "cancelled", 2)
    with pytest.raises(BusinessError, match="رقم الأمر مطلوب"):
        service.save_project(pid, {**p2, "code": " "}, p2["row_version"])
    # القائمة: البحث والفلاتر والسنة والصفحات والعدد (الأحدث الأول)
    pid3 = service.save_project(None, payload(env, name="FUTURE CAMPUS", customer_id=env["c2"],
                                              order_date=date(2026, 6, 18)))
    assert [r["id"] for r in service.list_projects()] == [pid, pid3, pid2]
    assert [r["id"] for r in service.list_projects("future")] == [pid3]
    assert [r["id"] for r in service.list_projects("delta")] == [pid3]               # باسم العميل
    assert [r["id"] for r in service.list_projects("ic25")] == [pid2]
    assert [r["id"] for r in service.list_projects(status="cancelled")] == [pid]
    assert [r["id"] for r in service.list_projects(year=2025)] == [pid2]
    assert [r["id"] for r in service.list_projects(center_id=cc["2"])] == [pid, pid3, pid2]
    assert [r["id"] for r in service.list_projects(center_id=cc["21"])] == [pid, pid3]
    assert [r["id"] for r in service.list_projects(page=2, page_size=2)] == [pid2]
    assert service.count_projects() == {"total": 3, "open": 2, "closed": 0, "cancelled": 1}
    assert service.years() == [2026, 2025] and len(service.lookup_projects()) == 3
    # المسح + العميل اللي عليه أمر رسالته عربي من الـ Service بتاعه
    service.delete_project(pid, 2)
    assert service.get_project(pid) is None
    with pytest.raises(Exception, match="عليه 1 أمر توريد"):
        c = CustomerService(db).get_customer(env["c2"])
        CustomerService(db).delete_customer(env["c2"], c["row_version"])


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"sales.projects.view", "sales.projects.create", "sales.projects.save"})
    pid = service.save_project(None, payload(env))
    p = service.get_project(pid)
    with pytest.raises(PermissionDenied):
        service.save_project(pid, {**p, "name": "x"}, p["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_project(pid, p["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'sales.projects.%%'")}
    assert codes == {f"sales.projects.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · الإضافة في نفس اللحظة بالرقم المقترح بتاخد رقمين مختلفين ·
    نفس الرقم بالإيد من جهازين ← واحد بس اللي بيتسجل."""
    db = env["db"]
    dev1, dev2 = ProjectService(db), ProjectService(Database(db.settings))
    try:
        pid = dev1.save_project(None, payload(env))
        seen1, seen2 = dev1.get_project(pid), dev2.get_project(pid)
        dev2.save_project(pid, {**seen2, "amount": "500"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_project(pid, {**seen1, "status": "closed"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_project(pid, seen1["row_version"])
        fresh = dev1.get_project(pid)
        assert fresh["amount"] == Decimal("500.00") and fresh["status"] == "open" and fresh["row_version"] == 2
        # الاتنين شايفين نفس الرقم المقترح وبيحفظوا في نفس اللحظة: الأول ماسك قفل العدّاد ← التاني يستنى
        assert dev1.new_defaults()["code"] == dev2.new_defaults()["code"] == "0002-Itec"
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_project(None, payload(env, name="أمر الجهاز التاني"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_code(date.today(), tx)
            worker = threading.Thread(target=second_device)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني القفل
            data = dev1._clean(payload(env, name="أمر الجهاز الأول"), new=True)
            dev1.repository.insert_project({**data, "code": first}, ex=tx)
        worker.join(10)
        assert "error" not in results
        assert dev1.get_project(results["id"])["code"] == "0003-Itec"
        assert sorted(r["code"] for r in db.fetch_all("SELECT code FROM acc.projects")) == \
               ["0001-Itec", "0002-Itec", "0003-Itec"]
        # نفس الرقم بالإيد من جهازين في نفس اللحظة ← التاني بيترفض برسالة عربي
        results.clear()

        def manual_second():
            try:
                results["id"] = dev2.save_project(None, payload(env, auto_code=False, code="IC26-500",
                                                                name="تاني"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            data = dev1._clean(payload(env, auto_code=False, code="IC26-500", name="أول"), new=True)
            dev1.repository.insert_project(data, ex=tx)
            worker = threading.Thread(target=manual_second)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # مستني الـ unique index
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError)
        assert "IC26-500" in str(results["error"])
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_new_save_edit_delete(env, monkeypatch):
    db, shown, cc = env["db"], env["shown"], env["cc"]
    screen = make_screen(db)
    try:
        assert screen.current_id is None and "عدد الأوامر: 0" in screen.count_label.text()
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "0001-Itec"
        assert screen.status_combo.currentData() == "open" and screen.currency.currentData() == "EGP"
        assert not screen.start_date.isEnabled()
        screen.name_edit.setText("BMS Controllers for City Tower")
        screen.customer.setCurrentIndex(screen.customer.findData(env["c2"]))
        assert screen.currency.currentData() == "USD"                   # عملة العميل في «جديد»
        screen.center.setCurrentIndex(screen.center.findData(cc["2202"]))
        screen.amount.setValue(64000)
        screen.tax_combo.setCurrentIndex(screen.tax_combo.findData(False))
        screen.has_end.setChecked(True)
        assert screen.end_date.isEnabled()
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "0001-Itec"
        p = proj(db, "0001-Itec")
        assert (p["customer_id"], p["cost_center_id"], p["amount"], p["currency_code"], p["tax_inclusive"],
                p["end_date"], p["start_date"]) == \
               (env["c2"], cc["2202"], Decimal("64000.00"), "USD", False, date.today(), None)
        texts = [screen.table.item(0, col).text() for col in range(screen.table.columnCount())]
        assert texts[2] == "Delta Dynamics" and texts[3] == "جاري" and "64,000.00 USD" in texts[4]
        # رقم بالإيد + من غير عميل
        screen.new_record()
        assert screen.code_edit.text() == "0002-Itec"
        screen.code_edit.setText("IC26-310")
        screen.name_edit.setText("Medical Simulation Building")
        screen.center.setCurrentIndex(screen.center.findData(cc["2101"]))
        screen.save()
        assert shown[-1] == "saved" and proj(db, "IC26-310")["customer_id"] is None
        # عميل مكتوب غلط ← رسالة والفورم بيفضل
        screen.new_record()
        assert screen.code_edit.text() == "0002-Itec"
        screen.name_edit.setText("x")
        screen.center.setCurrentIndex(screen.center.findData(cc["2101"]))
        screen.customer.setEditText("عميل مش موجود")
        screen.save()
        assert "مش في القايمة" in shown[-1] and screen.mode == "new"
        # الرقم المقترح اتاخد من جهاز تاني قبل الحفظ ← بياخد اللي بعده ورسالة
        screen.customer.setCurrentIndex(0)
        ins(db, "0002-Itec", cc["2101"], name="من جهاز تاني")
        screen.save()
        assert "اتحفظ برقم 0003-Itec" in shown[-2] and shown[-1] == "saved"
        # «تعديل» + جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.load(int(p["id"]))
        screen.edit_record()
        db.execute("UPDATE acc.projects SET notes = 'من جهاز تاني' WHERE code = '0001-Itec'")
        screen.status_combo.setCurrentIndex(screen.status_combo.findData("closed"))
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and proj(db, "0001-Itec")["status"] == "open"
        screen.cancel()
        screen.edit_record()
        screen.status_combo.setCurrentIndex(screen.status_combo.findData("closed"))
        screen.save()
        assert proj(db, "0001-Itec")["status"] == "closed"
        # الفلاتر: الحالة والمركز (الأب بيجيب الأبناء)
        closed_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "closed")
        closed_chip.click()
        assert screen.table.rowCount() == 1 and screen.current_id == int(p["id"])
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        screen.center_filter.setCurrentIndex(screen.center_filter.findData(cc["22"]))
        assert screen.table.rowCount() == 1
        screen.center_filter.setCurrentIndex(screen.center_filter.findData(cc["2"]))
        assert screen.table.rowCount() == 4 and "عدد الأوامر: 4" in screen.count_label.text()
        assert screen.year_filter.findData(date.today().year) > 0
        screen.load(int(p["id"]))
        screen.delete()
        assert proj(db, "0001-Itec") is None and screen.current_id is None
    finally:
        screen.close()


def test_screen_paging(env, monkeypatch):
    import app.ui.screens.projects_screen as module

    db, cc = env["db"], env["cc"]
    for i in range(1, 6):
        ins(db, f"{i:04d}-Itec", cc["2101"], order_date=date(2026, 1, i))
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    screen = make_screen(db)
    try:
        assert screen.page_label.text() == "صفحة 1 من 3" and screen.table.rowCount() == 2
        assert screen.current_record["code"] == "0005-Itec"                       # الأحدث الأول
        screen.next_button.click()
        screen.next_button.click()
        assert screen.page_label.text() == "صفحة 3 من 3" and screen.table.rowCount() == 1
        screen.search.setText("0002")                                           # بحث جديد ← أول صفحة
        assert screen.page_label.text() == "صفحة 1 من 1" and screen.table.rowCount() == 1
        screen.search.clear()
        screen.new_record()
        assert not screen.next_button.isEnabled() and not screen.center_filter.isEnabled()
        screen.cancel()
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("sales", "projects")
    assert target.name_ar == "أوامر التوريد"
    assert "projects" in SCREEN_FACTORIES
