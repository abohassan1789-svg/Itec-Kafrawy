"""اختبارات «الموردين» (0020: القاعدة + الكود SUB-001 + المورد النقدي + حماية المجموعة والضريبة + الـ Service +
الصلاحيات + جهازين + الشاشة)."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.repositories.supplier_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.supplier_group_service import SupplierGroupService
from app.services.supplier_service import SupplierService
from app.ui.common import messages

BLANK = {"name_en": None, "tax_reg_no": None, "contact_person": None, "mobile": None, "phone": None, "email": None,
         "address": None, "bank_name": None, "bank_account": None, "notes": None}


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
    general = int(temp_db.fetch_value("SELECT id FROM party.supplier_groups WHERE code = '1'"))
    yield {"db": temp_db, "shown": shown, "service": SupplierService(temp_db), "tax": tax, "general": general}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.suppliers_screen import SuppliersScreen

    return SuppliersScreen(SupplierService(db))


def ins(db, name, group, **extra):
    """INSERT مباشر (عشان نجرّب الـ trigger من غير الـ Service)."""
    cols = {"seq": extra.pop("seq", int(db.fetch_value("SELECT coalesce(max(seq), 0) + 1 FROM party.suppliers"))),
            "name_ar": name, "group_id": group, "currency_code": extra.pop("currency_code", "EGP"), **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO party.suppliers ({names}) VALUES ({marks}) RETURNING id",
                          list(cols.values()))


def sup(db, code):
    return db.fetch_one("SELECT * FROM party.suppliers WHERE code = %s", [code])


def payload(env, **extra):
    data = {**env["service"].new_defaults(), "name_ar": "الشرق لتجارة أجهزة التكييف"}
    data.update(extra)
    return data


def new_group(db, name="مقاولين باطن وتركيبات", **extra):
    service = SupplierGroupService(db)
    data = {**service.new_defaults(), "name_ar": name, **extra}
    return service.save_group(None, data)


# ---- القاعدة ---------------------------------------------------------------
def test_code_format():
    assert [format_code(n) for n in (0, 1, 12, 999, 1000, 1000000)] == \
           ["SUB-000", "SUB-001", "SUB-012", "SUB-999", "SUB-1000", "SUB-1000000"]


def test_database_rules(env):
    db, tax, general = env["db"], env["tax"], env["general"]
    sid = ins(db, "الشرق", general, tax_reg_no="200-300-401")
    s = sup(db, "SUB-001")                                                     # الكود محسوب من المسلسل
    assert s["id"] == sid and s["row_version"] == 1
    for seq, code in ((999, "SUB-999"), (1000, "SUB-1000")):                     # بعد 999 من غير أصفار
        ins(db, f"مورد {seq}", general, seq=seq)
        assert sup(db, code)["seq"] == seq
    with pytest.raises(psycopg.errors.GeneratedAlways):                         # الكود مايتكتبش بإيد
        db.execute("INSERT INTO party.suppliers (seq, code, name_ar, group_id, currency_code) "
                   "VALUES (5, 'X-5', 'x', %s, 'EGP')", [general])
    with pytest.raises(psycopg.errors.UniqueViolation):                         # المسلسل مايتكررش
        ins(db, "تاني", general, seq=1)
    # الرقم الضريبي مايتكررش (من غير مسافات وشُرَط ومن غير فرق كابيتال) وبحروف
    with pytest.raises(psycopg.errors.UniqueViolation):
        ins(db, "تانية", general, tax_reg_no="200300401")
    with pytest.raises(psycopg.errors.UniqueViolation):                         # الاسم مايتكررش
        ins(db, "  الشرق ", general)
    ins(db, "Euro Valves", general, tax_reg_no="de-1234")
    with pytest.raises(psycopg.errors.UniqueViolation):
        ins(db, "Euro 2", general, tax_reg_no="DE 1234")
    ins(db, "من غير رقم 1", general)
    ins(db, "من غير رقم 2", general)                                            # الفاضي يتكرر عادي
    for extra in (dict(payment_terms_days=400), dict(seq=0), dict(seq=-3), dict(tax_reg_no="  ")):
        with pytest.raises(psycopg.errors.CheckViolation):
            ins(db, "x", general, **extra)
    raise_cases = [
        (dict(vat_tax_code_id=tax["WHT1"]), "ض.ق.م للمورد"),
        (dict(wht_tax_code_id=tax["VAT14"]), "خصم المنبع للمورد"),
    ]
    for extra, msg in raise_cases:
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            ins(db, "x", general, **extra)
    db.execute("UPDATE core.currencies SET is_active = false WHERE code = 'EUR'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة"):
        ins(db, "x", general, currency_code="EUR")
    # المجموعة الموقوفة مابتتختارش
    gid = new_group(db)
    db.execute("UPDATE party.supplier_groups SET is_active = false WHERE id = %s", [gid])
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة — مينفعش تختارها"):
        ins(db, "x", gid)
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة — مينفعش تختارها"):
        db.execute("UPDATE party.suppliers SET group_id = %s WHERE id = %s", [gid, sid])
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE party.suppliers SET seq = 77 WHERE id = %s", [sid])
    db.execute("UPDATE party.suppliers SET mobile = '01000000001', bank_name = 'بنك مصر' WHERE id = %s", [sid])
    assert sup(db, "SUB-001")["row_version"] == 2


def test_cash_supplier(env):
    """«مورد نقدي» (0020): نظامي SUB-000 في «موردين — عام» · نقدي · مابيتمسحش ولا بيتوقف · اسمه بيتعدّل."""
    db, service, general, tax = env["db"], env["service"], env["general"], env["tax"]
    s = sup(db, "SUB-000")
    assert (s["name_ar"], s["is_system"], s["seq"], s["group_id"], s["payment_terms_days"], s["tax_reg_no"],
            s["currency_code"], s["vat_tax_code_id"], s["wht_tax_code_id"]) == \
           ("مورد نقدي", True, 0, general, 0, None, "EGP", tax["VAT14"], None)
    with pytest.raises(psycopg.errors.RaiseException, match="مابيتمسحش"):
        db.execute("DELETE FROM party.suppliers WHERE seq = 0")
    with pytest.raises(psycopg.errors.RaiseException, match="مابيتوقفش"):
        db.execute("UPDATE party.suppliers SET is_active = false WHERE seq = 0")
    with pytest.raises(psycopg.errors.RaiseException, match="من البرنامج بس"):
        db.execute("UPDATE party.suppliers SET is_system = false WHERE seq = 0")
    rec = service.get_supplier(int(s["id"]))
    with pytest.raises(BusinessError, match="مابيتمسحش"):
        service.delete_supplier(rec["id"], rec["row_version"])
    with pytest.raises(BusinessError, match="مابيتوقفش"):
        service.set_active(rec["id"], False, rec["row_version"])
    with pytest.raises(BusinessError, match="مابيتوقفش"):
        service.save_supplier(rec["id"], {**rec, "is_active": False}, rec["row_version"])
    service.save_supplier(rec["id"], {**rec, "name_ar": "مورد نقدي — كاش"}, rec["row_version"])
    assert sup(db, "SUB-000")["name_ar"] == "مورد نقدي — كاش"
    assert not SupplierService.needs_tax_warning(rec)                            # النقدي من غير تنبيه
    assert service.new_defaults()["code"] == "SUB-001"                           # العاديين بيبدأوا من 1
    sid = ins(db, "عادي", general)
    db.execute("DELETE FROM party.suppliers WHERE id = %s", [sid])               # العادي بيتمسح عادي


def test_group_and_tax_protection(env):
    db, tax, general = env["db"], env["tax"], env["general"]
    gid = new_group(db, wht_tax_code_id=tax["WHT3"])
    sid = ins(db, "الأمل للتركيبات", gid, wht_tax_code_id=tax["WHT3"])
    with pytest.raises(psycopg.errors.RaiseException, match="فيها 1 مورد"):
        db.execute("DELETE FROM party.supplier_groups WHERE id = %s", [gid])
    with pytest.raises(BusinessError, match="فيها 1 مورد"):
        g = SupplierGroupService(db).get_group(gid)
        SupplierGroupService(db).delete_group(gid, g["row_version"])
    db.execute("UPDATE party.supplier_groups SET wht_tax_code_id = NULL WHERE id = %s", [gid])
    with pytest.raises(psycopg.errors.RaiseException, match="متسجل على 1 مورد"):
        db.execute("UPDATE acc.tax_codes SET is_active = false WHERE id = %s", [tax["WHT3"]])
    with pytest.raises(psycopg.errors.RaiseException, match="غيّره عندهم الأول"):
        db.execute("DELETE FROM acc.tax_codes WHERE id = %s", [tax["WHT3"]])
    # المجموعة تتوقف عادي والمورد الموجود فيها بيفضل
    db.execute("UPDATE party.supplier_groups SET is_active = false WHERE id = %s", [gid])
    db.execute("UPDATE party.suppliers SET notes = 'عادي' WHERE id = %s", [sid])
    db.execute("UPDATE party.suppliers SET group_id = %s, wht_tax_code_id = NULL WHERE id = %s", [general, sid])
    db.execute("DELETE FROM party.supplier_groups WHERE id = %s", [gid])            # فضيت ← تتمسح
    db.execute("UPDATE acc.tax_codes SET is_active = false WHERE id = %s", [tax["WHT3"]])


# ---- الـ Service -------------------------------------------------------------
def test_service_defaults_save_and_rules(env):
    db, service, tax, general = env["db"], env["service"], env["tax"], env["general"]
    d = service.new_defaults()
    assert (d["code"], d["group_id"], d["currency_code"], d["vat_tax_code_id"], d["wht_tax_code_id"],
            d["payment_terms_days"]) == ("SUB-001", general, "EGP", tax["VAT14"], None, 30)
    assert "credit_limit" not in d                                                # مفيش حد ائتمان للمورد
    gid = new_group(db, payment_terms_days=45, wht_tax_code_id=tax["WHT1"])
    gdef = service.group_defaults(next(g for g in service.group_choices() if g["id"] == gid))
    assert (gdef["payment_terms_days"], gdef["wht_tax_code_id"]) == (45, tax["WHT1"])
    sid = service.save_supplier(None, payload(env, tax_reg_no=" 200 300 401 ", mobile="01000001001",
                                              email="info@elshark.com", bank_name="البنك الأهلي",
                                              bank_account="EG00 0003 0000 1001", **gdef))
    s = service.get_supplier(sid)
    assert (s["code"], s["tax_reg_no"], s["group_id"], s["account_code"], s["payment_terms_days"],
            s["bank_name"], s["bank_account"]) == \
           ("SUB-001", "200 300 401", gid, "2101", 45, "البنك الأهلي", "EG00 0003 0000 1001")
    audit = db.fetch_one("SELECT action, entity_label, record_label FROM sec.audit_log "
                         "WHERE entity = 'party.suppliers' ORDER BY id DESC LIMIT 1")
    assert audit == {"action": "create", "entity_label": "مورد", "record_label": "SUB-001 الشرق لتجارة أجهزة التكييف"}
    with pytest.raises(BusinessError, match="بنفس الاسم"):
        service.save_supplier(None, payload(env, name_ar="الشرق  لتجارة أجهزة التكييف"))
    with pytest.raises(BusinessError, match="متسجل قبل كده للمورد «الشرق لتجارة أجهزة التكييف» \\(SUB-001\\)"):
        service.save_supplier(None, payload(env, name_ar="تانية", tax_reg_no="200-300-401"))
    for bad, msg in ((dict(name_ar=""), "اسم المورد"), (dict(group_id=None), "المجموعة"),
                     (dict(payment_terms_days=500), "مدة السداد"), (dict(currency_code=""), "العملة"),
                     (dict(email="no-at"), "الإيميل"), (dict(tax_reg_no="--"), "أرقام أو حروف"),
                     (dict(tax_reg_no="1" * 31), "30 حرف"), (dict(bank_account="1" * 61), "IBAN")):
        with pytest.raises(BusinessError, match=msg):
            service.save_supplier(None, payload(env, **{"name_ar": "جديد", **bad}))
    # أجنبي برقم بحروف + من غير رقم خالص (عليه ض.ق.م ← تنبيه بس، مش منع)
    service.save_supplier(None, payload(env, name_ar="Orient Cooling Co.", tax_reg_no="CN-91440101",
                                        currency_code="USD", vat_tax_code_id=None))
    no_tax = service.save_supplier(None, payload(env, name_ar="فريق الصفوة للتركيبات"))
    assert SupplierService.needs_tax_warning(service.get_supplier(no_tax))
    assert not SupplierService.needs_tax_warning(s)
    # تعديل بالإصدار + الكود ثابت
    service.save_supplier(sid, {**s, "seq": 99, "code": "SUB-099", "payment_terms_days": 60}, s["row_version"])
    s2 = service.get_supplier(sid)
    assert (s2["code"], s2["payment_terms_days"], s2["row_version"]) == ("SUB-001", 60, 2)
    # الإيقاف / التفعيل
    service.set_active(sid, False, s2["row_version"])
    assert service.get_supplier(sid)["is_active"] is False
    service.set_active(sid, False, 3)                                              # نفس الحالة ← مفيش تغيير
    assert service.get_supplier(sid)["row_version"] == 3
    # القائمة: البحث (بالكود بأي شكل) والفلاتر والصفحات والعدد
    codes = lambda rows: [r["code"] for r in rows]                                 # noqa: E731
    assert codes(service.list_suppliers()) == ["SUB-000", "SUB-001", "SUB-002", "SUB-003"]
    for key in ("SUB-002", "sub-002", "002", "orient", "9144"):
        assert codes(service.list_suppliers(key)) == ["SUB-002"], key
    assert codes(service.list_suppliers("2003004")) == ["SUB-001"]                 # الضريبي من غير شُرَط
    assert codes(service.list_suppliers("0100000")) == ["SUB-001"]                 # الموبايل
    assert codes(service.list_suppliers("الصفوة")) == ["SUB-003"]
    assert codes(service.list_suppliers(status="inactive")) == ["SUB-001"]
    assert codes(service.list_suppliers(group_id=general)) == ["SUB-000", "SUB-002", "SUB-003"]
    assert codes(service.list_suppliers(page=2, page_size=2)) == ["SUB-002", "SUB-003"]
    assert service.count_suppliers() == {"total": 4, "active": 3, "inactive": 1, "no_tax": 1}
    assert len(service.lookup_suppliers()) == 4
    service.delete_supplier(sid, 3)
    assert sup(db, "SUB-001") is None
    assert service.new_defaults()["code"] == "SUB-004"                             # بيكمّل بعد الأكبر


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"purch.suppliers.view", "purch.suppliers.create", "purch.suppliers.save"})
    sid = service.save_supplier(None, payload(env))
    s = service.get_supplier(sid)
    with pytest.raises(PermissionDenied):
        service.save_supplier(sid, {**s, "name_ar": "x"}, s["row_version"])
    with pytest.raises(PermissionDenied):
        service.set_active(sid, False, s["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_supplier(sid, s["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'purch.suppliers.%%'")}
    assert codes == {f"purch.suppliers.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · الإضافة في نفس اللحظة بتاخد أكواد مختلفة ·
    نفس الرقم الضريبي من جهازين في نفس اللحظة ← واحد بس اللي بيتسجل."""
    db = env["db"]
    dev1, dev2 = SupplierService(db), SupplierService(Database(db.settings))
    try:
        sid = dev1.save_supplier(None, payload(env))
        seen1, seen2 = dev1.get_supplier(sid), dev2.get_supplier(sid)
        dev2.save_supplier(sid, {**seen2, "payment_terms_days": 45}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_supplier(sid, {**seen1, "payment_terms_days": 90}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(sid, False, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_supplier(sid, seen1["row_version"])
        fresh = dev1.get_supplier(sid)
        assert fresh["payment_terms_days"] == 45 and fresh["row_version"] == 2
        # اتنين بيضيفوا في نفس اللحظة بنفس الرقم الضريبي: قفل الترقيم ← التاني يستنى، وبعدين يترفض
        d1, d2 = dev1.new_defaults(), dev2.new_defaults()
        assert d1["code"] == d2["code"] == "SUB-002"
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_supplier(None, {**d2, "name_ar": "مورد الجهاز التاني",
                                                          "tax_reg_no": "555666777"})
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_seq(tx)                   # الجهاز الأول ماسك قفل الترقيم
            worker = threading.Thread(target=second_device)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            dev1.repository.insert_supplier({**d1, **BLANK, "seq": first, "name_ar": "مورد الجهاز الأول",
                                             "tax_reg_no": "555-666-777"}, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "متسجل قبل كده" in str(results["error"])
        assert sorted(r["code"] for r in db.fetch_all("SELECT code FROM party.suppliers")) == \
               ["SUB-000", "SUB-001", "SUB-002"]
        # من غير تعارض في الرقم: كودين مختلفين
        results.clear()

        def third():
            try:
                results["id"] = dev2.save_supplier(None, {**d2, "name_ar": "مورد تالت"})
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_seq(tx)
            worker = threading.Thread(target=third)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            dev1.repository.insert_supplier({**d1, **BLANK, "seq": first, "name_ar": "مورد رابع"}, ex=tx)
        worker.join(10)
        assert "error" not in results
        assert sorted(int(r["seq"]) for r in db.fetch_all("SELECT seq FROM party.suppliers")) == [0, 1, 2, 3, 4]
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_new_save_edit_toggle_delete(env, monkeypatch):
    db, shown, tax = env["db"], env["shown"], env["tax"]
    gid = new_group(db, payment_terms_days=45, wht_tax_code_id=tax["WHT1"])
    screen = make_screen(db)
    try:
        # المورد النقدي أول واحد: نظامي ← «حذف» و«إيقاف / تفعيل» مقفولين ومن غير تنبيه الضريبة
        assert screen.current_record["code"] == "SUB-000" and screen.name_ar.text() == "مورد نقدي"
        assert screen.system_badge.isVisibleTo(screen) and not screen.delete_button.isEnabled()
        assert not screen.toggle_button.isEnabled() and screen.terms.value() == 0
        assert not screen.tax_warning.isVisibleTo(screen)
        assert screen.table.item(0, 1).text() == "مورد نقدي 🔒" and "عدد الموردين: 1" in screen.count_label.text()
        screen.edit_record()
        assert not screen.is_active.isEnabled() and screen.name_ar.isEnabled()
        screen.cancel()
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "SUB-001 (مقترح)"
        assert screen.group.currentData() == env["general"] and screen.terms.value() == 30
        assert screen.control_account.text().startswith("2101") and screen.vat_tax.currentText().startswith("VAT14")
        assert screen.tax_warning.isVisibleTo(screen)                  # عليه ض.ق.م ومن غير رقم ← تنبيه
        # تغيير المجموعة في «جديد» بينسخ افتراضيها
        screen.group.setCurrentIndex(screen.group.findData(gid))
        assert screen.terms.value() == 45 and screen.wht_tax.currentData() == tax["WHT1"]
        screen.name_ar.setText("الأمل للتركيبات الميكانيكية")
        screen.tax_reg_no.setText("200-300-405")
        assert not screen.tax_warning.isVisibleTo(screen)
        screen.mobile.setText("01000001005")
        screen.bank_name.setText("بنك مصر")
        screen.bank_account.setText("EG00 0002 0000 1005")
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "SUB-001"
        s = sup(db, "SUB-001")
        assert (s["group_id"], s["payment_terms_days"], s["wht_tax_code_id"], s["tax_reg_no"], s["bank_name"],
                s["bank_account"]) == (gid, 45, tax["WHT1"], "200-300-405", "بنك مصر", "EG00 0002 0000 1005")
        texts = [screen.table.item(1, col).text() for col in range(screen.table.columnCount())]
        assert texts[0] == "SUB-001" and texts[1] == "الأمل للتركيبات الميكانيكية"
        assert texts[2] == "مقاولين باطن وتركيبات" and texts[5] == "نشط"
        # «تعديل»: تغيير المجموعة مابيغيّرش المدة
        screen.edit_record()
        screen.group.setCurrentIndex(screen.group.findData(env["general"]))
        assert screen.terms.value() == 45 and "2101" in screen.control_account.text()
        screen.cancel()
        # الرقم الضريبي المكرر ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("شركة تانية")
        screen.tax_reg_no.setText("200300405")
        screen.save()
        assert "متسجل قبل كده" in shown[-1] and screen.mode == "new"
        screen.cancel()
        screen.load(int(s["id"]))
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.edit_record()
        db.execute("UPDATE party.suppliers SET notes = 'من جهاز تاني' WHERE code = 'SUB-001'")
        screen.name_ar.setText("الأمل")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and sup(db, "SUB-001")["name_ar"] == "الأمل للتركيبات الميكانيكية"
        screen.cancel()
        # إيقاف من الشريط + الفلتر
        assert screen.toggle_button.isEnabled()
        screen.toggle_active()
        assert sup(db, "SUB-001")["is_active"] is False and screen.current_record["is_active"] is False
        active_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "active")
        active_chip.click()
        assert screen.table.rowCount() == 1 and screen.current_id is None       # النقدي بس
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        assert screen.table.rowCount() == 2
        screen.load(int(s["id"]))
        screen.delete()
        assert sup(db, "SUB-001") is None and screen.current_id is None
    finally:
        screen.close()


def test_screen_paging(env, monkeypatch):
    import app.ui.screens.suppliers_screen as module

    db, general = env["db"], env["general"]
    for i in range(1, 6):
        ins(db, f"مورد {i}", general)
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    screen = make_screen(db)
    try:
        assert screen.page_label.text() == "صفحة 1 من 3" and screen.table.rowCount() == 2
        assert not screen.prev_button.isEnabled() and screen.next_button.isEnabled()
        assert screen.current_record["code"] == "SUB-000"
        screen.next_button.click()
        screen.next_button.click()
        assert screen.page_label.text() == "صفحة 3 من 3" and screen.table.rowCount() == 2
        assert not screen.next_button.isEnabled()
        screen.search.setText("SUB-002")                      # بحث جديد ← أول صفحة
        assert screen.page_label.text() == "صفحة 1 من 1" and screen.table.rowCount() == 1
        assert "عدد الموردين: 1" in screen.count_label.text()
        screen.search.clear()
        screen.new_record()
        assert not screen.next_button.isEnabled() and not screen.group_filter.isEnabled()
        screen.cancel()
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("purch", "suppliers")
    assert target.name_ar == "الموردين"
    assert "suppliers" in SCREEN_FACTORIES
