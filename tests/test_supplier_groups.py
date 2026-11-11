"""اختبارات «مجموعات الموردين» (0017: القاعدة + المجموعة الجاهزة + حماية الحساب والضريبة + التفريع + الـ Service
+ الصلاحيات + جهازين + الشاشة)."""

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
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.supplier_group_service import SupplierGroupService
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
    acc = {r["code"]: int(r["id"]) for r in temp_db.fetch_all("SELECT id, code FROM acc.accounts")}
    tax = {r["code"]: int(r["id"]) for r in temp_db.fetch_all("SELECT id, code FROM acc.tax_codes")}
    yield {"db": temp_db, "shown": shown, "service": SupplierGroupService(temp_db), "acc": acc, "tax": tax}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.supplier_groups_screen import SupplierGroupsScreen

    return SupplierGroupsScreen(SupplierGroupService(db))


def ins(db, name, account, **extra):
    """INSERT مباشر (عشان نجرّب الـ trigger من غير الـ Service)."""
    cols = {"code": extra.pop("code", str(db.fetch_value(
        "SELECT coalesce(max(code::int), 0) + 1 FROM party.supplier_groups"))),
            "name_ar": name, "control_account_id": account, **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO party.supplier_groups ({names}) VALUES ({marks}) RETURNING id",
                          list(cols.values()))


def grp(db, code):
    return db.fetch_one("SELECT * FROM party.supplier_groups WHERE code = %s", [code])


def payload(env, **extra):
    data = {**env["service"].new_defaults(), "name_ar": "مقاولين باطن وتركيبات"}
    data.update(extra)
    return data


def sub_account(db, acc, code="210102", name="موردين أجانب"):
    """2101 بيتفرّع (0017) + حساب موردين فرعي جديد تحته (مش نظامي)."""
    general = db.fetch_value("SELECT acc.split_account(%s)", [acc["2101"]])
    new_id = db.fetch_value(
        "INSERT INTO acc.accounts (code, name_ar, parent_id, nature, classification, account_type, role_code, "
        "party_kind) VALUES (%s, %s, %s, 'cr', 'current', 'liability', 'suppliers', 'supplier') RETURNING id",
        [code, name, acc["2101"]])
    return int(general), int(new_id)


# ---- القاعدة ---------------------------------------------------------------
def test_seed_general_group(env):
    db, acc, tax = env["db"], env["acc"], env["tax"]
    rows = db.fetch_all("SELECT * FROM party.supplier_groups")
    assert len(rows) == 1
    g = rows[0]
    assert (g["code"], g["name_ar"], g["is_system"], g["is_active"]) == ("1", "موردين — عام", True, True)
    assert g["control_account_id"] == acc["2101"] and g["vat_tax_code_id"] == tax["VAT14"]
    assert g["wht_tax_code_id"] is None
    assert (g["payment_terms_days"], g["currency_code"], g["row_version"]) == (30, "EGP", 1)
    assert "credit_limit" not in g                                                   # مفيش حد ائتمان للمورد


def test_database_rules(env):
    db, acc, tax = env["db"], env["acc"], env["tax"]
    raise_cases = [
        (dict(name="x1", account=acc["1103"]), "من حسابات «الموردين»"),             # عملاء
        (dict(name="x2", account=acc["2102"]), "من حسابات «الموردين»"),             # أوراق دفع
        (dict(name="x3", account=acc["21"]), "فرعي ونشط"),                          # رئيسي
        (dict(name="x4", account=acc["2101"], vat_tax_code_id=tax["WHT1"]), "ض.ق.م الافتراضي"),
        (dict(name="x5", account=acc["2101"], vat_tax_code_id=tax["VAT0"]), "على المشتريات"),   # صفري = مبيعات بس
        (dict(name="x6", account=acc["2101"], wht_tax_code_id=tax["VAT14"]), "خصم المنبع الافتراضي"),
    ]
    for case, msg in raise_cases:
        extra = {k: v for k, v in case.items() if k not in ("name", "account")}
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            ins(db, case["name"], case["account"], **extra)
    db.execute("UPDATE core.currencies SET is_active = false WHERE code = 'EUR'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة"):
        ins(db, "x7", acc["2101"], currency_code="EUR")
    for extra in (dict(payment_terms_days=400), dict(payment_terms_days=-1), dict(code="A1")):
        with pytest.raises(psycopg.errors.CheckViolation):
            ins(db, "x8", acc["2101"], **extra)
    with pytest.raises(psycopg.errors.UniqueViolation):                             # الاسم من غير مسافات/حروف
        ins(db, "  موردين — عام ", acc["2101"])
    gid = ins(db, "مكاتب استشارية", acc["2101"], wht_tax_code_id=tax["WHT5"], vat_tax_code_id=tax["EXEMPT"])
    assert db.fetch_value("SELECT wht_tax_code_id FROM party.supplier_groups WHERE id = %s", [gid]) == tax["WHT5"]
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE party.supplier_groups SET code = '9' WHERE id = %s", [gid])
    with pytest.raises(psycopg.errors.RaiseException, match="مابتتوقفش"):
        db.execute("UPDATE party.supplier_groups SET is_active = false WHERE code = '1'")
    with pytest.raises(psycopg.errors.RaiseException, match="مابتتمسحش"):
        db.execute("DELETE FROM party.supplier_groups WHERE code = '1'")
    with pytest.raises(psycopg.errors.RaiseException, match="من البرنامج بس"):
        db.execute("UPDATE party.supplier_groups SET is_system = true WHERE id = %s", [gid])
    db.execute("UPDATE party.supplier_groups SET payment_terms_days = 60 WHERE id = %s", [gid])
    assert db.fetch_value("SELECT row_version FROM party.supplier_groups WHERE id = %s", [gid]) == 2
    db.execute("DELETE FROM party.supplier_groups WHERE id = %s", [gid])


def test_linked_account_and_tax_protection(env):
    db, acc, tax = env["db"], env["acc"], env["tax"]
    general, sub = sub_account(db, acc)
    gid = ins(db, "موردين أجانب", sub, wht_tax_code_id=tax["WHT3"])
    with pytest.raises(psycopg.errors.RaiseException, match="حساب رقابة لمجموعات الموردين"):
        db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [sub])
    with pytest.raises(psycopg.errors.RaiseException, match="غيّر حساب المجموعة الأول"):
        db.execute("DELETE FROM acc.accounts WHERE id = %s", [sub])
    with pytest.raises(psycopg.errors.RaiseException, match="دوره ونوعه"):
        db.execute("UPDATE acc.accounts SET role_code = NULL WHERE id = %s", [sub])
    with pytest.raises(psycopg.errors.RaiseException, match="افتراضي لمجموعات الموردين"):
        db.execute("UPDATE acc.tax_codes SET is_active = false WHERE id = %s", [tax["WHT3"]])
    with pytest.raises(psycopg.errors.RaiseException, match="افتراضي لمجموعات الموردين"):
        db.execute("UPDATE acc.tax_codes SET applies_to = 'sales' WHERE id = %s", [tax["WHT3"]])
    db.execute("UPDATE acc.tax_codes SET name_ar = 'خصم منبع 3%%' WHERE id = %s", [tax["WHT3"]])   # الاسم عادي
    db.execute("DELETE FROM party.supplier_groups WHERE id = %s", [gid])
    db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [sub])      # مش مربوط ← عادي
    assert general


def test_split_moves_groups_to_general(env):
    """التفريع بيودّي مجموعات الموردين للعام — ومجموعات العملاء لسه بتتنقل (الدالة اتعادت كتابتها في 0017)."""
    db, acc = env["db"], env["acc"]
    before = grp(db, "1")
    general = db.fetch_value("SELECT acc.split_account(%s)", [acc["2101"]])
    after = grp(db, "1")
    assert after["control_account_id"] == general and after["row_version"] == before["row_version"] + 1
    assert db.fetch_one("SELECT code, is_leaf, role_code FROM acc.accounts WHERE id = %s", [general]) == \
           {"code": "210101", "is_leaf": True, "role_code": "suppliers"}
    assert [r["code"] for r in env["service"].account_choices()] == ["210101"]
    assert db.fetch_value("SELECT account_id FROM acc.default_accounts WHERE key = 'suppliers'") == general
    cust_general = db.fetch_value("SELECT acc.split_account(%s)", [acc["1103"]])
    assert db.fetch_value("SELECT control_account_id FROM party.customer_groups WHERE code = '1'") == cust_general


# ---- الـ Service -------------------------------------------------------------
def test_service_defaults_save_and_rules(env):
    db, service, acc, tax = env["db"], env["service"], env["acc"], env["tax"]
    d = service.new_defaults()
    assert (d["code"], d["control_account_id"], d["currency_code"], d["vat_tax_code_id"], d["wht_tax_code_id"],
            d["payment_terms_days"]) == ("2", acc["2101"], "EGP", tax["VAT14"], None, 30)
    assert "credit_limit" not in d
    choices = service.tax_choices()
    assert "VAT0" not in [t["code"] for t in choices["vat"]]                     # صفري للمبيعات بس
    assert [t["code"] for t in choices["wht"]] == ["WHT1", "WHT3", "WHT5"]
    gid = service.save_group(None, payload(env, payment_terms_days=45, wht_tax_code_id=tax["WHT3"]))
    g = service.get_group(gid)
    assert (g["code"], g["payment_terms_days"], g["wht_code"], g["account_code"]) == ("2", 45, "WHT3", "2101")
    audit = db.fetch_one("SELECT action, entity, entity_label, record_label FROM sec.audit_log "
                         "WHERE entity = 'party.supplier_groups' ORDER BY id DESC LIMIT 1")
    assert audit == {"action": "create", "entity": "party.supplier_groups", "entity_label": "مجموعة موردين",
                     "record_label": "2 مقاولين باطن وتركيبات"}
    with pytest.raises(BusinessError, match="بنفس الاسم"):
        service.save_group(None, payload(env, name_ar=" مقاولين باطن وتركيبات "))
    for bad, msg in ((dict(name_ar=""), "اسم المجموعة"), (dict(control_account_id=None), "حساب الرقابة"),
                     (dict(payment_terms_days=500), "مدة السداد"), (dict(payment_terms_days="x"), "رقم أيام"),
                     (dict(currency_code=""), "العملة")):
        with pytest.raises(BusinessError, match=msg):
            service.save_group(None, payload(env, **{"name_ar": "جديدة", **bad}))
    # تعديل بالإصدار + الكود ثابت حتى لو اتبعت غيره
    service.save_group(gid, {**g, "code": "99", "name_ar": "مقاولين باطن", "wht_tax_code_id": tax["WHT1"]},
                       g["row_version"])
    g2 = service.get_group(gid)
    assert (g2["code"], g2["name_ar"], g2["wht_code"], g2["row_version"]) == ("2", "مقاولين باطن", "WHT1", 2)
    # النظامية: مابتتوقفش ولا بتتمسح (رسالة من الـ Service)
    sysg = service.get_group(int(grp(db, "1")["id"]))
    with pytest.raises(BusinessError, match="مابتتوقفش"):
        service.save_group(sysg["id"], {**sysg, "is_active": False}, sysg["row_version"])
    with pytest.raises(BusinessError, match="مابتتمسحش"):
        service.delete_group(sysg["id"], sysg["row_version"])
    # الإيقاف والمسح العادي
    service.save_group(gid, {**g2, "is_active": False}, g2["row_version"])
    rows = service.list_groups()
    assert [r["code"] for r in rows] == ["1", "2"] and service.summary(rows) == \
           {"total": 2, "inactive": 1, "with_wht": 1, "accounts": 1}
    assert [r["code"] for r in service.list_groups("مقاول")] == ["2"]
    assert [r["code"] for r in service.list_groups("WHT1")] == ["2"]
    assert service.repository.supplier_count(gid) == 0                              # لسه مفيش جدول موردين
    service.delete_group(gid, 3)
    assert grp(db, "2") is None
    assert service.new_defaults()["code"] == "2"                       # الرقم بيتحسب من الموجود


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"purch.supplier_groups.view", "purch.supplier_groups.create", "purch.supplier_groups.save"})
    gid = service.save_group(None, payload(env))
    g = service.get_group(gid)
    with pytest.raises(PermissionDenied):
        service.save_group(gid, {**g, "name_ar": "x"}, g["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_group(gid, g["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'purch.supplier_groups.%%'")}
    assert codes == {f"purch.supplier_groups.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · الإضافة في نفس اللحظة بتاخد أرقام مختلفة."""
    db = env["db"]
    dev1, dev2 = SupplierGroupService(db), SupplierGroupService(Database(db.settings))
    try:
        gid = dev1.save_group(None, payload(env))
        seen1, seen2 = dev1.get_group(gid), dev2.get_group(gid)
        dev2.save_group(gid, {**seen2, "payment_terms_days": 45}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_group(gid, {**seen1, "payment_terms_days": 90}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_group(gid, seen1["row_version"])
        fresh = dev1.get_group(gid)
        assert fresh["payment_terms_days"] == 45 and fresh["row_version"] == 2
        # اتنين بيضيفوا في نفس اللحظة: قفل الترقيم ← كودين مختلفين
        d1, d2 = dev1.new_defaults(), dev2.new_defaults()
        assert d1["code"] == d2["code"] == "3"
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_group(None, {**d2, "name_ar": "خدمات نقل وشحن"})
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_code(tx)                  # الجهاز الأول ماسك قفل الترقيم
            worker = threading.Thread(target=second_device)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            dev1.repository.insert_group({**d1, "code": first, "name_ar": "موردين خامات", "name_en": None,
                                          "notes": None}, ex=tx)
        worker.join(10)
        assert "error" not in results
        codes = sorted(r["code"] for r in db.fetch_all("SELECT code FROM party.supplier_groups"))
        assert codes == ["1", "2", "3", "4"]
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_view_and_system_group(env):
    screen = make_screen(env["db"])
    try:
        assert screen.current_record["code"] == "1" and screen.name_ar.text() == "موردين — عام"
        assert screen.control_account.currentText().startswith("2101")
        assert screen.vat_tax.currentText().startswith("VAT14") and screen.wht_tax.currentData() is None
        assert not hasattr(screen, "credit_limit")
        assert not screen.delete_button.isEnabled() and screen.edit_button.isEnabled()
        assert screen.system_badge.isVisibleTo(screen)
        screen.edit_record()
        assert screen.mode == "edit" and not screen.is_active.isEnabled() and screen.name_ar.isEnabled()
        screen.cancel()
        assert "عدد المجموعات: 1" in screen.count_label.text()
        texts = [screen.table.item(0, c).text() for c in range(screen.table.columnCount())]
        assert texts[1] == "موردين — عام 🔒" and texts[2] == "2101 — الموردين" and texts[3] == "30 يوم"
        assert texts[4] == "—"
    finally:
        screen.close()


def test_screen_new_save_edit_delete(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    try:
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "2 (مقترح)"
        assert screen.control_account.currentText().startswith("2101") and screen.terms.value() == 30
        assert screen.currency.currentData() == "EGP" and screen.vat_tax.currentText().startswith("VAT14")
        vat_items = [screen.vat_tax.itemText(i) for i in range(screen.vat_tax.count())]
        assert not any(t.startswith("VAT0") for t in vat_items)              # صفري للمبيعات بس
        assert not screen.system_badge.isVisibleTo(screen)
        screen.name_ar.setText("مكاتب استشارية ومهن حرة")
        screen.terms.setValue(15)
        screen.wht_tax.setCurrentIndex(screen.wht_tax.findData(env["tax"]["WHT5"]))
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "2"
        g = grp(db, "2")
        assert (g["payment_terms_days"], g["wht_tax_code_id"]) == (15, env["tax"]["WHT5"])
        assert screen.delete_button.isEnabled() and not screen.system_badge.isVisibleTo(screen)
        row = next(r for r in range(screen.table.rowCount()) if screen.table.item(r, 0).text() == "2")
        assert screen.table.item(row, 4).text() == "WHT5"
        screen.edit_record()
        assert screen.is_active.isEnabled()
        screen.is_active.setChecked(False)
        screen.save()
        assert grp(db, "2")["is_active"] is False and grp(db, "2")["row_version"] == 2
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.edit_record()
        db.execute("UPDATE party.supplier_groups SET notes = 'من جهاز تاني' WHERE code = '2'")
        screen.name_ar.setText("استشاريين")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and grp(db, "2")["name_ar"] == "مكاتب استشارية ومهن حرة"
        screen.cancel()
        screen.delete()
        assert grp(db, "2") is None and screen.current_id is None
        # الاسم المكرر ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("موردين — عام")
        screen.save()
        assert "بنفس الاسم" in shown[-1] and screen.mode == "new"
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("purch", "supplier_groups")
    assert target.name_ar == "مجموعات الموردين"
    group = reg.section("purch").groups[0]
    assert group.title == "الموردين" and [t.code for t in group.targets] == ["supplier_groups", "suppliers"]
    assert "supplier_groups" in SCREEN_FACTORIES
