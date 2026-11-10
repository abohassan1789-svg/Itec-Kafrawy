"""اختبارات «العملاء» (0015: القاعدة + حماية المجموعة والضريبة + الـ Service + الصلاحيات + جهازين + الشاشة)."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.customer_group_service import CustomerGroupService
from app.services.customer_service import CustomerService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
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
    tax = {r["code"]: int(r["id"]) for r in temp_db.fetch_all("SELECT id, code FROM acc.tax_codes")}
    general = int(temp_db.fetch_value("SELECT id FROM party.customer_groups WHERE code = '1'"))
    yield {"db": temp_db, "shown": shown, "service": CustomerService(temp_db), "tax": tax, "general": general}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.customers_screen import CustomersScreen

    return CustomersScreen(CustomerService(db))


def ins(db, name, group, **extra):
    """INSERT مباشر (عشان نجرّب الـ trigger من غير الـ Service)."""
    cols = {"code": extra.pop("code", str(db.fetch_value(
        "SELECT coalesce(max(code::int), 0) + 1 FROM party.customers"))),
            "name_ar": name, "group_id": group, "currency_code": extra.pop("currency_code", "EGP"), **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO party.customers ({names}) VALUES ({marks}) RETURNING id",
                          list(cols.values()))


def cust(db, code):
    return db.fetch_one("SELECT * FROM party.customers WHERE code = %s", [code])


def payload(env, **extra):
    data = {**env["service"].new_defaults(), "name_ar": "شركة النخبة للتشييد"}
    data.update(extra)
    return data


def new_group(db, name="قطاع عام وجهات حكومية", **extra):
    service = CustomerGroupService(db)
    data = {**service.new_defaults(), "name_ar": name, **extra}
    return service.save_group(None, data)


# ---- القاعدة ---------------------------------------------------------------
def test_database_rules(env):
    db, tax, general = env["db"], env["tax"], env["general"]
    cid = ins(db, "النخبة", general, tax_reg_no="100-200-301")
    assert cust(db, "1")["row_version"] == 1                                    # الترقيم بيبدأ من 1
    # الرقم الضريبي مايتكررش (من غير مسافات وشُرَط ومن غير فرق كابيتال) ومن غير عدد معيّن وبحروف
    with pytest.raises(psycopg.errors.UniqueViolation):
        ins(db, "تانية", general, tax_reg_no="100200301")
    with pytest.raises(psycopg.errors.UniqueViolation):                         # الاسم مايتكررش
        ins(db, "  النخبة ", general)
    ins(db, "أجنبي", general, tax_reg_no="ae-1234")
    with pytest.raises(psycopg.errors.UniqueViolation):
        ins(db, "أجنبي 2", general, tax_reg_no="AE 1234")
    ins(db, "من غير رقم 1", general)
    ins(db, "من غير رقم 2", general)                                            # الفاضي يتكرر عادي
    for extra in (dict(payment_terms_days=400), dict(credit_limit=-1), dict(code="C-1"), dict(tax_reg_no="  ")):
        with pytest.raises(psycopg.errors.CheckViolation):
            ins(db, "x", general, **extra)
    raise_cases = [
        (dict(vat_tax_code_id=tax["WHT1"]), "ض.ق.م للعميل"),
        (dict(wht_tax_code_id=tax["VAT14"]), "خصم المنبع للعميل"),
    ]
    for extra, msg in raise_cases:
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            ins(db, "x", general, **extra)
    db.execute("UPDATE core.currencies SET is_active = false WHERE code = 'EUR'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة"):
        ins(db, "x", general, currency_code="EUR")
    # المجموعة الموقوفة مابتتختارش
    gid = new_group(db)
    db.execute("UPDATE party.customer_groups SET is_active = false WHERE id = %s", [gid])
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة — مينفعش تختارها"):
        ins(db, "x", gid)
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة — مينفعش تختارها"):
        db.execute("UPDATE party.customers SET group_id = %s WHERE id = %s", [gid, cid])
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE party.customers SET code = '99' WHERE id = %s", [cid])
    db.execute("UPDATE party.customers SET mobile = '01000000001' WHERE id = %s", [cid])
    assert cust(db, "1")["row_version"] == 2


def test_group_and_tax_protection(env):
    db, tax, general = env["db"], env["tax"], env["general"]
    gid = new_group(db, wht_tax_code_id=tax["WHT1"])
    cid = ins(db, "جهة حكومية", gid, wht_tax_code_id=tax["WHT1"])
    with pytest.raises(psycopg.errors.RaiseException, match="فيها 1 عميل"):
        db.execute("DELETE FROM party.customer_groups WHERE id = %s", [gid])
    with pytest.raises(BusinessError, match="فيها 1 عميل"):
        g = CustomerGroupService(db).get_group(gid)
        CustomerGroupService(db).delete_group(gid, g["row_version"])
    db.execute("UPDATE party.customer_groups SET wht_tax_code_id = NULL WHERE id = %s", [gid])
    with pytest.raises(psycopg.errors.RaiseException, match="متسجل على 1 عميل"):
        db.execute("UPDATE acc.tax_codes SET is_active = false WHERE id = %s", [tax["WHT1"]])
    with pytest.raises(psycopg.errors.RaiseException, match="غيّره عندهم الأول"):
        db.execute("DELETE FROM acc.tax_codes WHERE id = %s", [tax["WHT1"]])
    # المجموعة تتوقف عادي والعميل الموجود فيها بيفضل
    db.execute("UPDATE party.customer_groups SET is_active = false WHERE id = %s", [gid])
    db.execute("UPDATE party.customers SET notes = 'عادي' WHERE id = %s", [cid])
    db.execute("UPDATE party.customers SET group_id = %s, wht_tax_code_id = NULL WHERE id = %s", [general, cid])
    db.execute("DELETE FROM party.customer_groups WHERE id = %s", [gid])            # فضيت ← تتمسح
    db.execute("UPDATE acc.tax_codes SET is_active = false WHERE id = %s", [tax["WHT1"]])


# ---- الـ Service -------------------------------------------------------------
def test_service_defaults_save_and_rules(env):
    db, service, tax, general = env["db"], env["service"], env["tax"], env["general"]
    d = service.new_defaults()
    assert (d["code"], d["group_id"], d["currency_code"], d["vat_tax_code_id"], d["payment_terms_days"],
            d["credit_limit"]) == ("1", general, "EGP", tax["VAT14"], 30, Decimal("0"))
    gid = new_group(db, payment_terms_days=90, credit_limit=Decimal("1000000"), wht_tax_code_id=tax["WHT1"])
    gdef = service.group_defaults(next(g for g in service.group_choices() if g["id"] == gid))
    assert (gdef["payment_terms_days"], gdef["credit_limit"], gdef["wht_tax_code_id"]) == \
           (90, Decimal("1000000.00"), tax["WHT1"])
    cid = service.save_customer(None, payload(env, tax_reg_no=" 100 200 301 ", mobile="01000000001",
                                              email="info@nokhba.com", **gdef))
    c = service.get_customer(cid)
    assert (c["code"], c["tax_reg_no"], c["group_id"], c["account_code"], c["payment_terms_days"]) == \
           ("1", "100 200 301", gid, "1103", 90)
    audit = db.fetch_one("SELECT action, entity_label, record_label FROM sec.audit_log "
                         "WHERE entity = 'party.customers' ORDER BY id DESC LIMIT 1")
    assert audit == {"action": "create", "entity_label": "عميل", "record_label": "1 شركة النخبة للتشييد"}
    with pytest.raises(BusinessError, match="بنفس الاسم"):
        service.save_customer(None, payload(env, name_ar="شركة  النخبة للتشييد"))
    with pytest.raises(BusinessError, match="متسجل قبل كده للعميل «شركة النخبة للتشييد»"):
        service.save_customer(None, payload(env, name_ar="تانية", tax_reg_no="100-200-301"))
    for bad, msg in ((dict(name_ar=""), "اسم العميل"), (dict(group_id=None), "المجموعة"),
                     (dict(payment_terms_days=500), "مدة السداد"), (dict(credit_limit="-5"), "حد الائتمان"),
                     (dict(credit_limit="abc"), "رقم"), (dict(currency_code=""), "العملة"),
                     (dict(email="no-at"), "الإيميل"), (dict(tax_reg_no="--"), "أرقام أو حروف"),
                     (dict(tax_reg_no="1" * 31), "30 حرف")):
        with pytest.raises(BusinessError, match=msg):
            service.save_customer(None, payload(env, **{"name_ar": "جديد", **bad}))
    # أجنبي برقم بحروف + من غير رقم خالص
    service.save_customer(None, payload(env, name_ar="Gulfair FZE", tax_reg_no="TRN-100234567800003",
                                        currency_code="USD"))
    service.save_customer(None, payload(env, name_ar="من غير رقم"))
    # تعديل بالإصدار + الكود ثابت + الحد تنبيه بس (أي رقم مقبول)
    service.save_customer(cid, {**c, "code": "99", "credit_limit": "5000"}, c["row_version"])
    c2 = service.get_customer(cid)
    assert (c2["code"], c2["credit_limit"], c2["row_version"]) == ("1", Decimal("5000.00"), 2)
    # الإيقاف / التفعيل
    service.set_active(cid, False, c2["row_version"])
    assert service.get_customer(cid)["is_active"] is False
    service.set_active(cid, False, 3)                                              # نفس الحالة ← مفيش تغيير
    assert service.get_customer(cid)["row_version"] == 3
    # القائمة: البحث والفلاتر والصفحات والعدد
    assert [r["code"] for r in service.list_customers()] == ["1", "2", "3"]
    assert [r["code"] for r in service.list_customers("1002003")] == ["1"]          # الضريبي من غير شُرَط
    assert [r["code"] for r in service.list_customers("gulf")] == ["2"]
    assert [r["code"] for r in service.list_customers("0100000")] == ["1"]          # الموبايل
    assert [r["code"] for r in service.list_customers("من غير")] == ["3"]
    assert [r["code"] for r in service.list_customers(status="inactive")] == ["1"]
    assert [r["code"] for r in service.list_customers(group_id=general)] == ["2", "3"]
    assert [r["code"] for r in service.list_customers(page=2, page_size=2)] == ["3"]
    assert service.count_customers() == {"total": 3, "active": 2, "inactive": 1, "no_tax": 1}
    assert len(service.lookup_customers()) == 3
    service.delete_customer(cid, 3)
    assert cust(db, "1") is None
    assert service.new_defaults()["code"] == "4"                                    # بيكمّل بعد الأكبر


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"sales.customers.view", "sales.customers.create", "sales.customers.save"})
    cid = service.save_customer(None, payload(env))
    c = service.get_customer(cid)
    with pytest.raises(PermissionDenied):
        service.save_customer(cid, {**c, "name_ar": "x"}, c["row_version"])
    with pytest.raises(PermissionDenied):
        service.set_active(cid, False, c["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_customer(cid, c["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'sales.customers.%%'")}
    assert codes == {f"sales.customers.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · الإضافة في نفس اللحظة بتاخد أرقام مختلفة ·
    نفس الرقم الضريبي من جهازين في نفس اللحظة ← واحد بس اللي بيتسجل."""
    db = env["db"]
    dev1, dev2 = CustomerService(db), CustomerService(Database(db.settings))
    try:
        cid = dev1.save_customer(None, payload(env))
        seen1, seen2 = dev1.get_customer(cid), dev2.get_customer(cid)
        dev2.save_customer(cid, {**seen2, "payment_terms_days": 45}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_customer(cid, {**seen1, "payment_terms_days": 90}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(cid, False, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_customer(cid, seen1["row_version"])
        fresh = dev1.get_customer(cid)
        assert fresh["payment_terms_days"] == 45 and fresh["row_version"] == 2
        # اتنين بيضيفوا في نفس اللحظة بنفس الرقم الضريبي: قفل الترقيم ← التاني يستنى، وبعدين يترفض
        d1, d2 = dev1.new_defaults(), dev2.new_defaults()
        assert d1["code"] == d2["code"] == "2"
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_customer(None, {**d2, "name_ar": "عميل الجهاز التاني",
                                                          "tax_reg_no": "555666777"})
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_code(tx)                  # الجهاز الأول ماسك قفل الترقيم
            worker = threading.Thread(target=second_device)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            dev1.repository.insert_customer(
                {**d1, "code": first, "name_ar": "عميل الجهاز الأول", "name_en": None, "tax_reg_no": "555-666-777",
                 "contact_person": None, "mobile": None, "phone": None, "email": None, "address": None,
                 "notes": None}, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "متسجل قبل كده" in str(results["error"])
        assert sorted(r["code"] for r in db.fetch_all("SELECT code FROM party.customers")) == ["1", "2"]
        # من غير تعارض في الرقم: كودين مختلفين
        results.clear()

        def third():
            try:
                results["id"] = dev2.save_customer(None, {**d2, "name_ar": "عميل تالت"})
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_code(tx)
            worker = threading.Thread(target=third)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            dev1.repository.insert_customer(
                {**d1, "code": first, "name_ar": "عميل رابع", "name_en": None, "tax_reg_no": None,
                 "contact_person": None, "mobile": None, "phone": None, "email": None, "address": None,
                 "notes": None}, ex=tx)
        worker.join(10)
        assert "error" not in results
        assert sorted(int(r["code"]) for r in db.fetch_all("SELECT code FROM party.customers")) == [1, 2, 3, 4]
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_new_save_edit_toggle_delete(env, monkeypatch):
    db, shown, tax = env["db"], env["shown"], env["tax"]
    gid = new_group(db, payment_terms_days=90, credit_limit=Decimal("1000000"), wht_tax_code_id=tax["WHT1"])
    screen = make_screen(db)
    try:
        assert screen.current_id is None and "عدد العملاء: 0" in screen.count_label.text()
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "1 (مقترح)"
        assert screen.group.currentData() == env["general"] and screen.terms.value() == 30
        assert screen.control_account.text().startswith("1103") and screen.vat_tax.currentText().startswith("VAT14")
        # تغيير المجموعة في «جديد» بينسخ افتراضيها
        screen.group.setCurrentIndex(screen.group.findData(gid))
        assert screen.terms.value() == 90 and screen.credit_limit.value() == 1_000_000
        assert screen.wht_tax.currentData() == tax["WHT1"]
        screen.name_ar.setText("وزارة الإسكان")
        screen.tax_reg_no.setText("200-300-400")
        screen.mobile.setText("01000000009")
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "1"
        c = cust(db, "1")
        assert (c["group_id"], c["payment_terms_days"], c["wht_tax_code_id"], c["tax_reg_no"]) == \
               (gid, 90, tax["WHT1"], "200-300-400")
        texts = [screen.table.item(0, col).text() for col in range(screen.table.columnCount())]
        assert texts[1] == "وزارة الإسكان" and texts[2] == "قطاع عام وجهات حكومية" and texts[5] == "نشط"
        # «تعديل»: تغيير المجموعة مابيغيّرش المدة
        screen.edit_record()
        screen.group.setCurrentIndex(screen.group.findData(env["general"]))
        assert screen.terms.value() == 90 and "1103" in screen.control_account.text()
        screen.cancel()
        # الرقم الضريبي المكرر ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("شركة تانية")
        screen.tax_reg_no.setText("200300400")
        screen.save()
        assert "متسجل قبل كده" in shown[-1] and screen.mode == "new"
        screen.cancel()
        screen.load(int(c["id"]))
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.edit_record()
        db.execute("UPDATE party.customers SET notes = 'من جهاز تاني' WHERE code = '1'")
        screen.name_ar.setText("الإسكان")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and cust(db, "1")["name_ar"] == "وزارة الإسكان"
        screen.cancel()
        # إيقاف من الشريط + الفلتر
        assert screen.toggle_button.isEnabled()
        screen.toggle_active()
        assert cust(db, "1")["is_active"] is False and screen.current_record["is_active"] is False
        active_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "active")
        active_chip.click()
        assert screen.table.rowCount() == 0 and screen.current_id is None
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        assert screen.table.rowCount() == 1
        screen.load(int(c["id"]))
        screen.delete()
        assert cust(db, "1") is None and screen.current_id is None
    finally:
        screen.close()


def test_screen_paging(env, monkeypatch):
    import app.ui.screens.customers_screen as module

    db, general = env["db"], env["general"]
    for i in range(1, 6):
        ins(db, f"عميل {i}", general)
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    screen = make_screen(db)
    try:
        assert screen.page_label.text() == "صفحة 1 من 3" and screen.table.rowCount() == 2
        assert not screen.prev_button.isEnabled() and screen.next_button.isEnabled()
        assert screen.current_record["code"] == "1"
        screen.next_button.click()
        screen.next_button.click()
        assert screen.page_label.text() == "صفحة 3 من 3" and screen.table.rowCount() == 1
        assert not screen.next_button.isEnabled()
        screen.search.setText("عميل 2")                        # بحث جديد ← أول صفحة
        assert screen.page_label.text() == "صفحة 1 من 1" and screen.table.rowCount() == 1
        assert "عدد العملاء: 1" in screen.count_label.text()
        screen.search.clear()
        screen.new_record()
        assert not screen.next_button.isEnabled() and not screen.group_filter.isEnabled()
        screen.cancel()
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("sales", "customers")
    assert target.name_ar == "العملاء"
    assert "customers" in SCREEN_FACTORIES
