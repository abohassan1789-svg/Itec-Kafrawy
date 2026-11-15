"""اختبارات «الخزائن» (0023: القاعدة + الكود TRS-001 + حساب الخزنة تحت 1101 + الأمين + الخزينة الافتراضية +
الـ Service + الصلاحيات + جهازين + الشاشة)."""

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
from app.repositories.treasury_repository import format_code
from app.security.session import SESSION
from app.services.account_service import AccountService
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.treasury_service import TreasuryService
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
    yield {"db": temp_db, "shown": shown, "service": TreasuryService(temp_db)}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.treasuries_screen import TreasuriesScreen

    return TreasuriesScreen(TreasuryService(db))


def emp(db, name, treasurer=True, active=True):
    seq = int(db.fetch_value("SELECT coalesce(max(seq), 0) + 1 FROM party.employees"))
    return db.fetch_value("INSERT INTO party.employees (seq, name_ar, is_treasurer, is_active) VALUES (%s, %s, %s, %s) "
                          "RETURNING id", [seq, name, treasurer, active])


def trs(db, code):
    return db.fetch_one("SELECT * FROM cash.treasuries WHERE code = %s", [code])


def acc(db, code):
    return db.fetch_one("SELECT * FROM acc.accounts WHERE code = %s", [code])


def payload(**extra):
    return {"name_ar": "خزينة موقع العاشر", "name_en": None, "currency_code": "EGP", "location": None,
            "notes": None, "treasurer_id": None, "max_balance": "", "is_active": True, "account_mode": "auto",
            **extra}


# ---- القاعدة ---------------------------------------------------------------
def test_code_format():
    assert [format_code(n) for n in (1, 12, 999, 1000)] == ["TRS-001", "TRS-012", "TRS-999", "TRS-1000"]


def test_seed_and_database_rules(env):
    db = env["db"]
    rows = db.fetch_all("SELECT t.code, t.name_ar, t.currency_code, a.code AS acc, t.owns_account "
                        "FROM cash.treasuries t JOIN acc.accounts a ON a.id = t.account_id ORDER BY t.seq")
    assert rows == [{"code": "TRS-001", "name_ar": "الخزينة الرئيسية", "currency_code": "EGP", "acc": "110101",
                     "owns_account": False},
                    {"code": "TRS-002", "name_ar": "خزينة النثريات", "currency_code": "EGP", "acc": "110102",
                     "owns_account": False}]
    main = trs(db, "TRS-001")
    # الحساب: فرعي بدور «خزينة» وبنفس العملة ومايتربطش بخزنتين
    a1101, a1102 = acc(db, "1101"), acc(db, "110201")
    for account_id, msg in ((a1101["id"], "رئيسي"), (a1102["id"], "مش بدور «خزينة»")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute("INSERT INTO cash.treasuries (seq, name_ar, currency_code, account_id) VALUES (9, 'x', 'EGP', %s)",
                       [account_id])
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("INSERT INTO cash.treasuries (seq, name_ar, currency_code, account_id) VALUES (9, 'x', 'EGP', %s)",
                   [main["account_id"]])
    usd = db.fetch_value("INSERT INTO acc.accounts (code, name_ar, parent_id, is_leaf, account_type, nature, "
                         "classification, cash_flow, role_code, currency_code) VALUES ('110103', 'خزينة الدولار', %s, "
                         "true, 'asset', 'dr', 'current', 'cash', 'cash', 'USD') RETURNING id", [a1101["id"]])
    with pytest.raises(psycopg.errors.RaiseException, match="غير عملة الخزنة"):
        db.execute("INSERT INTO cash.treasuries (seq, name_ar, currency_code, account_id) VALUES (9, 'x', 'EGP', %s)",
                   [usd])
    db.execute("INSERT INTO cash.treasuries (seq, name_ar, currency_code, account_id) VALUES (3, 'خزينة الدولار', "
               "'USD', %s)", [usd])
    for sql, msg in (("UPDATE cash.treasuries SET seq = 77 WHERE code = 'TRS-003'", "ثابت"),
                     (f"UPDATE cash.treasuries SET account_id = {a1102['id']} WHERE code = 'TRS-003'", "ثابت"),
                     ("UPDATE cash.treasuries SET is_active = false WHERE code = 'TRS-001'", "الافتراضية"),
                     ("DELETE FROM cash.treasuries WHERE code = 'TRS-001'", "الافتراضية")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(sql)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE cash.treasuries SET max_balance = 0 WHERE code = 'TRS-003'")
    # الأمين: موظف نشط عليه دور «أمين خزينة»
    clerk, keeper, off = emp(db, "موظف عادي", treasurer=False), emp(db, "أ/ محمود سعيد"), emp(db, "موقوف",
                                                                                              active=False)
    for who, msg in ((clerk, "مش عليه دور"), (off, "موقوف")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute("UPDATE cash.treasuries SET treasurer_id = %s WHERE code = 'TRS-002'", [who])
    db.execute("UPDATE cash.treasuries SET treasurer_id = %s WHERE code = 'TRS-002'", [keeper])
    for sql, msg in ((f"UPDATE party.employees SET is_treasurer = false WHERE id = {keeper}", "قبل ما تشيل الدور"),
                     (f"UPDATE party.employees SET is_active = false WHERE id = {keeper}", "قبل ما توقفه"),
                     (f"DELETE FROM party.employees WHERE id = {keeper}", "غيّر الأمين")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(sql)
    db.execute("UPDATE party.employees SET mobile = '010' WHERE id = %s", [keeper])      # تعديل عادي ماشي
    # حماية حساب الخزنة من شجرة الحسابات
    a = acc(db, "110102")
    for sql, msg in ((f"DELETE FROM acc.accounts WHERE id = {a['id']}", "امسح الخزنة"),
                     (f"UPDATE acc.accounts SET is_active = false WHERE id = {a['id']}", "وقّف الخزنة"),
                     (f"UPDATE acc.accounts SET currency_code = 'USD' WHERE id = {a['id']}", "من شاشة الخزائن"),
                     (f"UPDATE acc.accounts SET role_code = NULL WHERE id = {a['id']}", "دوره"),
                     (f"SELECT acc.split_account({a['id']})", "يتفرّع")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(sql)
    # «الخزينة الافتراضية» لازم تبقى حساب خزنة نشطة
    free = db.fetch_value("INSERT INTO acc.accounts (code, name_ar, parent_id, is_leaf, account_type, nature, "
                          "classification, cash_flow, role_code) VALUES ('110104', 'حرة', %s, true, 'asset', 'dr', "
                          "'current', 'cash', 'cash') RETURNING id", [a1101["id"]])
    set_box = "UPDATE acc.default_accounts SET account_id = %s WHERE key = 'cash_box'"
    with pytest.raises(psycopg.errors.RaiseException, match="مش مربوط بخزنة"):
        db.execute(set_box, [free])
    db.execute("UPDATE cash.treasuries SET is_active = false WHERE code = 'TRS-003'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة"):
        db.execute(set_box, [usd])
    db.execute(set_box, [a["id"]])                                                 # النثريات بقت الافتراضية
    with pytest.raises(psycopg.errors.RaiseException, match="الافتراضية"):
        db.execute("UPDATE cash.treasuries SET is_active = false WHERE code = 'TRS-002'")
    db.execute(set_box, [main["account_id"]])
    # تغيير العملة والاسم من الخزنة بيغيّر الحساب معاها
    db.execute("UPDATE cash.treasuries SET currency_code = 'USD', name_ar = 'نثريات دولار' WHERE code = 'TRS-002'")
    assert (acc(db, "110102")["currency_code"], acc(db, "110102")["name_ar"]) == ("USD", "نثريات دولار")
    assert trs(db, "TRS-002")["row_version"] == 3


# ---- الـ Service -------------------------------------------------------------
def test_service_auto_account_pick_and_rules(env):
    db, service = env["db"], env["service"]
    d = service.new_defaults()
    assert (d["code"], d["currency_code"], d["account_mode"]) == ("TRS-003", "EGP", "auto")
    assert d["suggested_account"] == {"code": "110103", "parent_code": "1101", "parent_name": "النقدية بالخزائن"}
    keeper = emp(db, "أ/ كريم فوزي")
    tid = service.save_treasury(None, payload(name_en="10th City Site", treasurer_id=keeper,
                                              max_balance="50,000", location="العاشر من رمضان"))
    t = service.get_treasury(tid)
    assert (t["code"], t["account_code"], t["account_name"], t["parent_code"], t["treasurer_name"],
            t["max_balance"], t["owns_account"], t["is_default"]) == \
           ("TRS-003", "110103", "خزينة موقع العاشر", "1101", "أ/ كريم فوزي", Decimal("50000.00"), True, False)
    a = acc(db, "110103")
    assert (a["role_code"], a["currency_code"], a["nature"], a["is_leaf"], a["is_system"], a["name_en"]) == \
           ("cash", "EGP", "dr", True, False, "10th City Site")
    audits = db.fetch_all("SELECT entity, action, record_label FROM sec.audit_log WHERE entity IN "
                          "('cash.treasuries', 'acc.accounts') ORDER BY id")
    assert audits == [{"entity": "acc.accounts", "action": "create", "record_label": "110103 خزينة موقع العاشر"},
                      {"entity": "cash.treasuries", "action": "create", "record_label": "TRS-003 خزينة موقع العاشر"}]
    # الافتراضية = اللي حسابها cash_box
    assert [r["is_default"] for r in service.list_treasuries()] == [True, False, False]
    # التحقق
    clerk = emp(db, "موظف عادي", treasurer=False)
    for bad, msg in ((dict(name_ar=""), "اسم الخزنة"), (dict(name_ar="الخزينة  الرئيسية"), "بنفس الاسم"),
                     (dict(currency_code="XXX"), "مش موجودة"), (dict(currency_code=""), "عملة"),
                     (dict(max_balance="abc"), "رقم"), (dict(max_balance="-5"), "سالب"),
                     (dict(treasurer_id=clerk), "مش عليه دور"), (dict(account_mode="x"), "يتعمل لوحده"),
                     (dict(account_mode="pick", account_id=None), "اختار الحساب"),
                     (dict(account_mode="pick", account_id=acc(db, "110101")["id"]), "مربوط بالفعل"),
                     (dict(account_mode="pick", account_id=acc(db, "1101")["id"]), "رئيسي"),
                     (dict(account_mode="pick", account_id=acc(db, "110201")["id"]), "مش بدور")):
        with pytest.raises(BusinessError, match=msg):
            service.save_treasury(None, payload(**{"name_ar": "جديدة", **bad}))
    assert acc(db, "110104") is None                                        # الحساب مااتعملش مع الغلط
    # «حساب موجود»: حساب دولار حر تحت 1101
    accounts = AccountService(db)
    usd_id = accounts.save_account(None, {**accounts.new_defaults(acc(db, "1101")["id"]), "name_ar": "خزينة الدولار",
                                          "currency_code": "USD"})
    assert [a["code"] for a in service.free_accounts()] == ["110104"]
    with pytest.raises(BusinessError, match="غير عملة الخزنة"):
        service.save_treasury(None, payload(name_ar="خزينة الدولار", account_mode="pick", account_id=usd_id))
    usd_t = service.save_treasury(None, payload(name_ar="خزينة الدولار", currency_code="USD", account_mode="pick",
                                                account_id=usd_id))
    assert (service.get_treasury(usd_t)["owns_account"], service.free_accounts()) == (False, [])
    # تعديل: الاسم والعملة بيتغيّروا في الحساب (لو اسمه كان زي الخزنة) + سطر audit للحساب
    t = service.get_treasury(tid)
    service.save_treasury(tid, {**t, "name_ar": "خزينة العاشر", "currency_code": "EUR", "account_id": 999,
                                "max_balance": None}, t["row_version"])
    t2 = service.get_treasury(tid)
    assert (t2["code"], t2["account_code"], t2["account_name"], t2["currency_code"], t2["max_balance"]) == \
           ("TRS-003", "110103", "خزينة العاشر", "EUR", None)
    assert acc(db, "110103")["currency_code"] == "EUR"
    assert db.fetch_value("SELECT count(*) FROM sec.audit_log WHERE entity = 'acc.accounts' AND action = 'update'") == 1
    # الإيقاف: الافتراضية لأ · الأمين اللي اتشال دوره بيمنع إعادة التفعيل
    main = service.get_treasury(1)
    with pytest.raises(BusinessError, match="الخزينة الافتراضية"):
        service.set_active(main["id"], False, main["row_version"])
    with pytest.raises(BusinessError, match="الخزينة الافتراضية"):
        service.save_treasury(main["id"], {**main, "is_active": False}, main["row_version"])
    with pytest.raises(BusinessError, match="الخزينة الافتراضية"):
        service.delete_treasury(main["id"], main["row_version"])
    service.set_active(tid, False, t2["row_version"])
    db.execute("UPDATE party.employees SET is_treasurer = false WHERE id = %s", [keeper])   # الخزنة موقوفة ← مسموح
    with pytest.raises(BusinessError, match="مابقاش أمين خزينة"):
        service.set_active(tid, True, t2["row_version"] + 1)
    # القائمة: البحث والفلاتر والصفحات والعدد
    codes = lambda rows: [r["code"] for r in rows]                                 # noqa: E731
    assert codes(service.list_treasuries()) == ["TRS-001", "TRS-002", "TRS-003", "TRS-004"]
    for key in ("TRS-003", "trs-003", "003", "العاشر", "110103", "كريم"):
        assert "TRS-003" in codes(service.list_treasuries(key)), key
    assert codes(service.list_treasuries(status="inactive")) == ["TRS-003"]
    assert codes(service.list_treasuries(page=2, page_size=3)) == ["TRS-004"]
    assert service.count_treasuries() == {"total": 4, "active": 3, "inactive": 1}
    assert len(service.lookup_treasuries()) == 4
    # المسح: الخزنة + حسابها اللي اتعمل معاها · الحساب اللي اتختار بيفضل
    service.delete_treasury(tid, t2["row_version"] + 1)
    assert trs(db, "TRS-003") is None and acc(db, "110103") is None
    service.delete_treasury(usd_t, 1)
    assert acc(db, "110104") is not None
    assert service.new_defaults()["suggested_account"]["code"] == "110105"         # بعد الأكبر


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"cash.treasuries.view", "cash.treasuries.create", "cash.treasuries.save"})
    tid = service.save_treasury(None, payload())
    t = service.get_treasury(tid)
    with pytest.raises(PermissionDenied):
        service.save_treasury(tid, {**t, "name_ar": "x"}, t["row_version"])
    with pytest.raises(PermissionDenied):
        service.set_active(tid, False, t["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_treasury(tid, t["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'cash.treasuries.%%'")}
    assert codes == {f"cash.treasuries.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · خزنتين جداد في نفس اللحظة بياخدوا كود خزنة وكود حساب
    مختلفين · نفس الحساب الموجود من جهازين ← واحد بس اللي بيتربط."""
    db = env["db"]
    dev1, dev2 = TreasuryService(db), TreasuryService(Database(db.settings))
    try:
        tid = 1
        seen1, seen2 = dev1.get_treasury(tid), dev2.get_treasury(tid)
        dev2.save_treasury(tid, {**seen2, "location": "الدور التاني"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_treasury(tid, {**seen1, "location": "الدور الأول"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(2, False, 99)
        assert dev1.get_treasury(tid)["location"] == "الدور التاني"
        # خزنتين جداد في نفس اللحظة: التاني بيستنى قفل الترقيم ← TRS-004 و 110104
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_treasury(None, payload(name_ar="خزينة الجهاز التاني"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=second_device)
        with db.atomic() as tx:
            seq = dev1.repository.next_seq(tx)                     # الجهاز الأول ماسك قفل الترقيم
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            clean = dev1._clean(payload(name_ar="خزينة الجهاز الأول"), None)
            clean.update(seq=seq, account_id=dev1._create_account(tx, clean), owns_account=True)
            dev1.repository.insert_treasury(clean, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        got = {r["code"]: r["acc"] for r in db.fetch_all(
            "SELECT t.code, a.code AS acc FROM cash.treasuries t JOIN acc.accounts a ON a.id = t.account_id "
            "WHERE t.seq > 2")}
        assert got == {"TRS-003": "110103", "TRS-004": "110104"}
        # نفس الحساب الموجود من جهازين: التاني بيستنى قفل الحساب وبعدين بيلاقيه مربوط
        accounts = AccountService(db)
        free = accounts.save_account(None, {**accounts.new_defaults(db.fetch_value(
            "SELECT id FROM acc.accounts WHERE code = '1101'")), "name_ar": "خزينة حرة"})
        results.clear()

        def pick_second():
            try:
                results["id"] = dev2.save_treasury(None, payload(name_ar="حرة 2", account_mode="pick",
                                                                 account_id=free))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=pick_second)
        with db.atomic() as tx:
            dev1.accounts.get_account(free, ex=tx, for_update=True)  # الجهاز الأول ماسك الحساب
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            dev1.repository.insert_treasury({**payload(name_ar="حرة 1"), "seq": 50, "account_id": free,
                                             "owns_account": False, "max_balance": None}, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "مربوط بالفعل" in str(results["error"])
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_new_save_edit_toggle_delete(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    keeper = emp(db, "أ/ ياسر نبيل")
    screen = make_screen(db)
    try:
        assert screen.table.rowCount() == 2 and screen.current_record["code"] == "TRS-001"
        assert "⭐" in screen.default_label.text() and screen.account_view.text().endswith("الخزينة الرئيسية")
        texts = [screen.table.item(0, col).text() for col in range(screen.table.columnCount())]
        assert texts == ["TRS-001", "الخزينة الرئيسية ⭐", "EGP", "⁦110101⁩", "—", "نشطة"]
        assert not screen._account_form.isRowVisible(screen.modes_widget) and not screen.account_pick.isEnabled()
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "TRS-003 (مقترح)"
        assert "110103" in screen.account_view.text() and "1101" in screen.account_parent.text()
        assert screen.mode_auto.isEnabled() and not screen.account_pick.isEnabled()
        assert screen._account_form.isRowVisible(screen.modes_widget)
        screen.name_ar.setText("خزينة موقع العاشر")
        screen.treasurer.setCurrentIndex(screen.treasurer.findData(keeper))
        screen.max_balance.setText("50000")
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "TRS-003"
        t = trs(db, "TRS-003")
        assert (t["treasurer_id"], t["max_balance"], t["owns_account"]) == (keeper, 50000, True)
        assert screen.account_view.text() == "⁦110103⁩ خزينة موقع العاشر"
        # «حساب موجود» ← العملة بتمشي مع الحساب
        accounts = AccountService(db)
        accounts.save_account(None, {**accounts.new_defaults(acc(db, "1101")["id"]), "name_ar": "خزينة الدولار",
                                     "currency_code": "USD"})
        screen.new_record()
        screen.mode_pick.setChecked(True)
        assert screen.account_pick.isEnabled() and screen.account_pick.count() == 1
        assert screen.currency.currentData() == "USD" and screen.name_ar.text() == "خزينة الدولار"
        screen.save()
        assert trs(db, "TRS-004")["currency_code"] == "USD" and trs(db, "TRS-004")["owns_account"] is False
        # الاسم المتكرر ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("خزينة النثريات")
        screen.save()
        assert "بنفس الاسم" in shown[-1] and screen.mode == "new"
        screen.cancel()
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.load(int(t["id"]))
        screen.edit_record()
        assert screen.treasurer.currentData() == keeper
        db.execute("UPDATE cash.treasuries SET notes = 'من جهاز تاني' WHERE code = 'TRS-003'")
        screen.location.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and trs(db, "TRS-003")["location"] is None
        screen.cancel()
        # الافتراضية ماتتوقفش
        screen.load(int(trs(db, "TRS-001")["id"]))
        screen.toggle_active()
        assert "الخزينة الافتراضية" in shown[-1] and trs(db, "TRS-001")["is_active"] is True
        # إيقاف خزنة ← تختفي من «النشطة»
        screen.load(int(t["id"]))
        screen.toggle_active()
        assert trs(db, "TRS-003")["is_active"] is False
        active_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "active")
        active_chip.click()
        assert screen.table.rowCount() == 3 and screen.current_id is None
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        assert "موقوفة 1" in screen.count_label.text()
        screen.load(int(t["id"]))
        screen.delete()
        assert trs(db, "TRS-003") is None and acc(db, "110103") is None and screen.current_id is None
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("cash").groups[0]
    assert group.title == "الخزائن والبنوك" and [t.code for t in group.targets] == ["treasuries", "banks"]
    assert reg.find_target("cash", "treasuries").name_ar == "الخزائن"
    assert "treasuries" in SCREEN_FACTORIES
