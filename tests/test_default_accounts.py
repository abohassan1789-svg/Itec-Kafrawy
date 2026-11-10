"""اختبارات «الحسابات الافتراضية» (سكريبت 0012 + الـ Service + الصلاحيات + جهازين + الشاشة) على قاعدة مؤقتة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import threading
import time

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.account_service import AccountService
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.default_account_service import GROUPS, DefaultAccountService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

raise_ = psycopg.errors.RaiseException


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
    ids = {r["code"]: int(r["id"]) for r in temp_db.fetch_all("SELECT id, code FROM acc.accounts")}
    yield {"db": temp_db, "shown": shown, "service": DefaultAccountService(temp_db), "ids": ids}
    SESSION.clear()
    # الشاشات تتمسح هنا في الـ thread الرئيسي — لو الـ GC لمّها جوه thread اختبار تاني (جهازين) Qt بيقفل البرنامج.
    gc.collect()


def mapping(db) -> dict[str, str | None]:
    return {r["key"]: r["code"] for r in db.fetch_all(
        "SELECT d.key, a.code FROM acc.default_accounts d LEFT JOIN acc.accounts a ON a.id = d.account_id "
        "ORDER BY d.sort_order")}


def make_screen(db):
    from app.ui.screens.default_accounts_screen import DefaultAccountsScreen

    return DefaultAccountsScreen(DefaultAccountService(db))


def plain(text: str) -> str:
    return text.replace("⁦", "").replace("⁩", "")


# ---- القاعدة ---------------------------------------------------------------
def test_seed_keys_and_new_accounts(env):
    db = env["db"]
    m = mapping(db)
    assert len(m) == 29 and all(m.values())                                  # كله متحدد من أول يوم
    assert m == {
        "sales": "4101", "services": "4102", "sales_ret": "42", "disc_allowed": "5303", "customers": "1103",
        "cust_adv": "2105", "suppliers": "2101", "disc_received": "4302", "inventory": "1105", "cogs": "51",
        "inv_adj": "5205", "cost_var": "5206", "cash_box": "110101", "bank": "110201", "notes_rec": "1104",
        "notes_pay": "2102", "bank_charges": "5402", "custody": "1107", "vat_out": "2103", "vat_in": "110601",
        "wht_rec": "110602", "wht_pay": "2104", "fx_gain": "4301", "fx_loss": "5401", "rounding": "5403",
        "opening": "34", "retained": "33", "capital": "31", "depr": "1209"}
    assert db.fetch_value("SELECT count(DISTINCT row_version) FROM acc.default_accounts") == 1    # كله 1
    groups = [r["group_code"] for r in db.fetch_all(
        "SELECT DISTINCT ON (group_code) group_code, sort_order FROM acc.default_accounts ORDER BY group_code, sort_order")]
    assert set(groups) == set(GROUPS)
    # الحسابات الخمسة الجديدة: فرعي نظامي بدورها تحت الأب الصح
    new = db.fetch_all("SELECT a.code, p.code AS parent, a.role_code, a.is_system, a.account_type, a.level "
                       "FROM acc.accounts a JOIN acc.accounts p ON p.id = a.parent_id "
                       "WHERE a.code IN ('5303', '4302', '5205', '5206', '5403') ORDER BY a.code")
    assert [(r["code"], r["parent"], r["role_code"]) for r in new] == [
        ("4302", "43", "disc_received"), ("5205", "52", "inv_adj"), ("5206", "52", "cost_var"),
        ("5303", "53", "disc_allowed"), ("5403", "54", "rounding")]
    assert all(r["is_system"] and r["level"] == 3 for r in new)


def test_database_rules(env):
    db, ids = env["db"], env["ids"]
    upd = "UPDATE acc.default_accounts SET account_id = %s WHERE key = %s"
    with pytest.raises(raise_, match="محتاج حساب من «الإيرادات»"):
        db.execute(upd, [ids["5401"], "sales"])                             # مصروف على مفتاح إيرادات
    with pytest.raises(raise_, match="فرعي ونشط"):
        db.execute(upd, [ids["41"], "sales"])                               # رئيسي
    with pytest.raises(raise_, match="فرعي ونشط"):
        db.execute(upd, [ids["5302"], "disc_allowed"])                      # موقوف
    with pytest.raises(raise_, match="ثابت"):
        db.execute("DELETE FROM acc.default_accounts WHERE key = 'sales'")
    with pytest.raises(raise_, match="ثابتين"):
        db.execute("UPDATE acc.default_accounts SET account_type = 'expense' WHERE key = 'sales'")
    # تعديل حقيقي = الإصدار +1
    db.execute(upd, [ids["110202"], "bank"])
    assert db.fetch_value("SELECT row_version FROM acc.default_accounts WHERE key = 'bank'") == 2
    # الحساب الافتراضي: مايتمسحش ولا يتوقف (110202 مش نظامي — الحماية من الربط بس)
    with pytest.raises(raise_, match="افتراضي لـ \\(البنك الافتراضي\\)"):
        db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [ids["110202"]])
    with pytest.raises(raise_, match="افتراضي لـ"):
        db.execute("DELETE FROM acc.accounts WHERE id = %s", [ids["110202"]])
    # بعد ما يتغيّر: يتوقف ويتمسح عادي
    db.execute(upd, [ids["110201"], "bank"])
    db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [ids["110202"]])
    db.execute("DELETE FROM acc.accounts WHERE id = %s", [ids["110202"]])
    # رسالة «نظامي» بتطلع قبل «افتراضي» (ترتيب الـ triggers)
    with pytest.raises(raise_, match="نظامي"):
        db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [ids["4101"]])


def test_split_moves_default_to_general(env):
    db, ids = env["db"], env["ids"]
    general = db.fetch_value("SELECT acc.split_account(%s)", [ids["4101"]])
    row = db.fetch_one("SELECT account_id, row_version FROM acc.default_accounts WHERE key = 'sales'")
    assert row == {"account_id": general, "row_version": 2}
    general = db.fetch_value("SELECT acc.split_account(%s)", [ids["5402"]])          # مش نظامي
    assert mapping(db)["bank_charges"] == "540201"
    assert db.fetch_value("SELECT current_setting('acc.split_account_id', true)") in (None, "")
    # الاستثناء للتفريع بس: التحويل لرئيسي بإيدك ممنوع
    with pytest.raises(raise_, match="افتراضي لـ"):
        with db.atomic() as tx:
            tx.execute("SELECT set_config('acc.split_account_id', %s, true)", [str(ids["1105"])])   # حساب تاني
            tx.execute("UPDATE acc.accounts SET is_leaf = false WHERE id = %s", [ids["110201"]])


# ---- الـ Service -----------------------------------------------------------
def test_service_read_save_and_account_for(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    rows = service.list_keys("cash")
    assert [r["key"] for r in rows] == ["cash_box", "bank", "notes_rec", "notes_pay", "bank_charges", "custody"]
    assert rows[0]["id"] == "cash_box" and rows[0]["account_code"] == "110101"
    found = service.list_keys("cash", "خصم")                       # البحث في كل الأقسام
    assert {r["key"] for r in found} == {"disc_allowed", "disc_received", "wht_rec", "wht_pay"}
    assert {r["key"] for r in service.list_keys(None, "4301")} == {"fx_gain"}
    summary = service.group_summary()
    assert summary["sales"] == {"total": 6, "missing": 0} and sum(s["total"] for s in summary.values()) == 29
    assert [a["code"] for a in service.account_choices("asset")][:3] == ["110101", "110102", "110201"]
    assert service.suggested_account("bank")["code"] == "110201"
    assert service.suggested_account("services") is None                     # مالوش دور
    # حفظ + سجل التعديلات
    assert service.save_key("bank", ids["110202"], expected_version=1) == "bank"
    assert mapping(db)["bank"] == "110202" and service.account_for("bank") == ids["110202"]
    log = db.fetch_one("SELECT entity, entity_id, action, record_label, details FROM sec.audit_log "
                       "ORDER BY id DESC LIMIT 1")
    assert (log["entity"], log["entity_id"], log["action"], log["record_label"]) == (
        "acc.default_accounts", "bank", "update", "البنك الافتراضي")
    assert "account_id" in log["details"]["changed"]
    # أخطاء
    with pytest.raises(BusinessError, match="اختار الحساب"):
        service.save_key("bank", None, expected_version=2)
    with pytest.raises(BusinessError, match="فرعي نشط من «الإيرادات»"):
        service.save_key("sales", ids["5401"], expected_version=1)
    with pytest.raises(BusinessError, match="فرعي نشط"):
        service.save_key("sales", ids["41"], expected_version=1)
    assert mapping(db)["sales"] == "4101"
    # المفتاح الناقص ← المستند يترفض برسالة
    db.execute("UPDATE acc.default_accounts SET account_id = NULL WHERE key = 'rounding'")
    with pytest.raises(BusinessError, match="«فروق التقريب» مش متحدد — حدده من شاشة «الحسابات الافتراضية»"):
        service.account_for("rounding")
    assert service.group_summary()["fx"] == {"total": 3, "missing": 1}
    with pytest.raises(BusinessError, match="مش معروف"):
        service.account_for("xxx")


def test_chart_of_accounts_respects_defaults(env):
    service, ids = AccountService(env["db"]), env["ids"]
    DefaultAccountService(env["db"]).save_key("bank", ids["110202"], expected_version=1)
    misr = service.get_account(ids["110202"])
    with pytest.raises(BusinessError, match="افتراضي لـ \\(البنك الافتراضي\\).*الحسابات الافتراضية"):
        service.save_account(ids["110202"], {**misr, "is_active": False}, misr["row_version"])
    with pytest.raises(BusinessError, match="افتراضي لـ \\(البنك الافتراضي\\)"):
        service.delete_account(ids["110202"], misr["row_version"])
    plan = service.branch_plan(ids["110202"])
    assert plan["general"]["default_keys"] == ["البنك الافتراضي"]


def test_permissions(env):
    service, ids = env["service"], env["ids"]
    admin_id = SESSION.user["id"]
    SESSION.start({"id": admin_id, "username": "viewer", "is_admin": False, "full_access": False},
                  {"acc.default_accounts.view"})
    assert service.list_keys("sales")                                   # العرض مسموح
    with pytest.raises(PermissionDenied):
        service.save_key("bank", ids["110202"], expected_version=1)
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.default_accounts.view", "acc.default_accounts.edit"})
    with pytest.raises(PermissionDenied):                                # من غير «حفظ»
        service.save_key("bank", ids["110202"], expected_version=1)


# ---- جهازين ----------------------------------------------------------------
def test_two_devices(env):
    """جهازين فاتحين نفس المفتاح: الأول حفظ ← التاني يترفض · وجهاز بيوقف الحساب في نفس اللحظة ← التاني يستنى ويترفض."""
    db, ids = env["db"], env["ids"]
    dev1, dev2 = DefaultAccountService(db), DefaultAccountService(Database(db.settings))
    try:
        seen1, seen2 = dev1.get_key("bank"), dev2.get_key("bank")
        dev2.save_key("bank", ids["110202"], seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_key("bank", ids["110201"], seen1["row_version"])
        assert mapping(db)["bank"] == "110202"
        # جهاز بيوقف «خزينة النثريات» (ماسك الحساب) ← التاني بيختارها افتراضي: بيستنى وبعدين يترفض
        result: dict = {}

        def second_device():
            try:
                dev2.save_key("cash_box", ids["110102"], 1)
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        with db.atomic() as tx:
            tx.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [ids["110102"]])
            worker = threading.Thread(target=second_device)
            worker.start()
            time.sleep(0.7)
            assert worker.is_alive()                                  # مستني القفل
        worker.join(10)
        assert "فرعي ونشط" in messages.error_text(result.get("error") or Exception(""))
        assert mapping(db)["cash_box"] == "110101"
        # والعكس: جهاز اختارها افتراضي (لسه ماحفظش) ← التاني بيوقفها: بيستنى وبعدين يترفض
        db.execute("UPDATE acc.accounts SET is_active = true WHERE id = %s", [ids["110102"]])
        acc_service = AccountService(dev2.db)
        petty = acc_service.get_account(ids["110102"])

        def deactivate():
            try:
                acc_service.save_account(ids["110102"], {**petty, "is_active": False}, petty["row_version"])
                result["ok2"] = True
            except Exception as exc:  # noqa: BLE001
                result["error2"] = exc

        with db.atomic() as tx:
            tx.execute("UPDATE acc.default_accounts SET account_id = %s WHERE key = 'cash_box'", [ids["110102"]])
            worker = threading.Thread(target=deactivate)
            worker.start()
            time.sleep(0.7)
            assert worker.is_alive()
        worker.join(10)
        assert "افتراضي لـ" in messages.error_text(result.get("error2") or Exception(""))
        assert db.fetch_value("SELECT is_active FROM acc.accounts WHERE id = %s", [ids["110102"]])
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_groups_list_and_form(env):
    screen = make_screen(env["db"])
    assert screen.windowTitle().endswith("الحسابات الافتراضية")
    assert not screen.new_button.isVisibleTo(screen) and not screen.delete_button.isVisibleTo(screen)
    assert screen.edit_button.isVisibleTo(screen) and screen.suggest_button.isVisibleTo(screen)
    assert screen.groups_list.count() == 7 and screen.current_group == "sales"
    assert plain(screen.groups_list.item(0).text()).endswith("(6)")
    assert [r["key"] for r in screen.table.rows] == ["sales", "services", "sales_ret", "disc_allowed",
                                                     "customers", "cust_adv"]
    assert screen.current_id == "sales" and screen.key_name.text() == "المبيعات (بضاعة)"
    assert plain(screen.account_combo.currentText()).startswith("4101")
    assert screen.type_name.text().startswith("الإيرادات") and "متحدد" in screen.status_label.text()
    assert not screen.account_combo.isEnabled() and not screen.suggest_button.isEnabled()
    assert plain(screen.count_label.text()) == "«المبيعات»: 6 مفتاح · 6 متحدد · 0 ناقص"
    assert plain(screen.total_label.text()).startswith("29 مفتاح · 29 متحدد")
    assert screen.format_cell("account", None, screen.table.rows[0]) == "⁦4101⁩ مبيعات بضاعة"
    # قسم تاني
    screen.groups_list.setCurrentRow(5)                                   # العملات
    assert screen.current_group == "fx" and [r["key"] for r in screen.table.rows] == ["fx_gain", "fx_loss", "rounding"]
    assert screen.current_id == "fx_gain"
    # البحث في كل الأقسام
    screen.search.setText("خصم")
    assert {r["key"] for r in screen.table.rows} == {"disc_allowed", "disc_received", "wht_rec", "wht_pay"}
    assert plain(screen.count_label.text()).startswith("نتايج البحث: 4 مفتاح")
    screen.search.clear()
    assert len(screen.table.rows) == 3


def test_screen_edit_suggest_and_save(env):
    db, ids, shown = env["db"], env["ids"], env["shown"]
    screen = make_screen(db)
    screen.groups_list.setCurrentRow(3)                                   # الخزينة والبنوك
    screen.load("bank")
    screen.edit_record()
    assert screen.mode == "edit" and screen.account_combo.isEnabled() and not screen.groups_list.isEnabled()
    choices = [screen.account_combo.itemData(i) for i in range(screen.account_combo.count())]
    assert ids["110202"] in choices and ids["41"] not in choices and ids["2101"] not in choices   # أصول فرعي نشط بس
    screen.account_combo.setCurrentIndex(screen.account_combo.findData(ids["110202"]))
    screen.save()
    assert shown[-1] == "saved" and screen.mode == "view" and mapping(db)["bank"] == "110202"
    assert screen.current_version == 2
    # «اقترح من الدور» بيرجّع حساب الدور
    screen.edit_record()
    assert screen.suggest_button.isEnabled()
    screen.suggest_account()
    assert screen.account_combo.currentData() == ids["110201"]
    screen.cancel()
    assert mapping(db)["bank"] == "110202"
    # مفتاح مالوش دور: الزرار مقفول
    screen.load("bank_charges")
    screen.edit_record()
    assert not screen.suggest_button.isEnabled()
    # «— مش متحدد —» ← رسالة ومفيش حفظ
    screen.account_combo.setCurrentIndex(0)
    screen.save()
    assert "اختار الحساب" in shown[-1] and screen.mode == "edit"
    screen.cancel()


def test_screen_conflict_and_missing(env, monkeypatch):
    db, ids, shown = env["db"], env["ids"], env["shown"]
    screen = make_screen(db)
    screen.edit_record()                                                  # «المبيعات» مفتوحة بإصدار 1
    db.execute("UPDATE acc.default_accounts SET account_id = %s WHERE key = 'sales'", [ids["4102"]])   # جهاز تاني
    conflicts: list[str] = []
    monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(str(exc)) or True)
    screen.account_combo.setCurrentIndex(screen.account_combo.findData(ids["4101"]))
    screen.save()
    assert conflicts and "اتعدّل من جهاز تاني" in conflicts[-1]
    assert screen.mode == "view" and plain(screen.account_combo.currentText()).startswith("4102")
    # مفتاح ناقص: أحمر في القائمة والشريط والفورم
    db.execute("UPDATE acc.default_accounts SET account_id = NULL WHERE key = 'disc_allowed'")
    screen.refresh_screen()
    assert plain(screen.groups_list.item(0).text()).endswith("⚠ ناقص 1")
    assert plain(screen.count_label.text()) == "«المبيعات»: 6 مفتاح · 5 متحدد · 1 ناقص"
    assert "⚠ ناقص 1" in screen.total_label.text()
    screen.load("disc_allowed")
    assert "ناقص" in screen.status_label.text() and screen.account_combo.currentData() is None
    row = next(r for r in screen.table.rows if r["key"] == "disc_allowed")
    assert screen.format_cell("account", None, row) == "— مش متحدد —" and screen.format_cell("status", None, row) == "⚠ ناقص"
