"""اختبارات «شجرة الحسابات» (القاعدة + الدليل الجاهز + الـ Service + جهازين + الشاشة) على قاعدة مؤقتة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.account_service import AccountService, matching_ids, next_child_code, visible_ids
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
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
    service = AccountService(temp_db)
    ids = {r["code"]: int(r["id"]) for r in service.list_accounts()}
    yield {"db": temp_db, "shown": shown, "service": service, "ids": ids}
    SESSION.clear()


def make_screen(db):
    from app.ui.screens.chart_of_accounts_screen import ChartOfAccountsScreen

    return ChartOfAccountsScreen(AccountService(db))


def ins(db, code, parent, **extra):
    """INSERT مباشر في القاعدة (عشان نجرّب الـ trigger من غير الـ Service)."""
    cols = {"code": code, "name_ar": f"حساب {code}", "parent_id": parent, "nature": "dr",
            "classification": "current", "account_type": "asset", **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO acc.accounts ({names}) VALUES ({marks}) RETURNING id", list(cols.values()))


def row(db, code):
    return db.fetch_one("SELECT * FROM acc.accounts WHERE code = %s", [code])


# ---- الدليل الجاهز ----------------------------------------------------------
def test_seeded_chart_shape(env):
    db, ids = env["db"], env["ids"]
    assert len(ids) == 56                       # 51 من 0009 + 5 من 0012 (الحسابات الافتراضية)
    roots = db.fetch_all("SELECT code, account_type, statement FROM acc.accounts WHERE parent_id IS NULL ORDER BY code")
    assert [(r["code"], r["account_type"], r["statement"]) for r in roots] == [
        ("1", "asset", "bs"), ("2", "liability", "bs"), ("3", "equity", "bs"),
        ("4", "revenue", "is"), ("5", "expense", "is")]
    # كل حساب: الكود بيبدأ بكود أبوه، والمستوى = أبوه + 1، والنوع زي الجذر، والأب رئيسي
    bad = db.fetch_value("""
        SELECT count(*) FROM acc.accounts c JOIN acc.accounts p ON p.id = c.parent_id
        WHERE left(c.code, length(p.code)) <> p.code OR c.level <> p.level + 1
           OR c.account_type <> p.account_type OR p.is_leaf""")
    assert bad == 0
    assert db.fetch_value("SELECT max(level) FROM acc.accounts") == 4
    # الرئيسي كله تحته حسابات، والفرعي مفيش تحته
    assert db.fetch_value("""SELECT count(*) FROM acc.accounts a WHERE NOT a.is_leaf
                             AND NOT EXISTS (SELECT 1 FROM acc.accounts c WHERE c.parent_id = a.id)""") == 0
    # الأدوار: كل دور من الأساسيين على حساب، والنظامي = الجذور + اللي ليها دور
    roles = {r["role_code"]: r["code"] for r in db.fetch_all(
        "SELECT DISTINCT ON (role_code) role_code, code FROM acc.accounts WHERE role_code IS NOT NULL "
        "ORDER BY role_code, length(code)")}
    assert roles["customers"] == "1103" and roles["suppliers"] == "2101" and roles["vat_in"] == "110601"
    assert roles["vat_out"] == "2103" and roles["partner"] == "32" and roles["cash"] == "1101"
    assert db.fetch_value("SELECT count(*) FROM acc.account_roles") == 28           # 23 + 5 من 0012
    assert row(db, "1103")["party_kind"] == "customer" and row(db, "2101")["party_kind"] == "supplier"
    assert row(db, "1103")["is_system"] and row(db, "1")["is_system"] and not row(db, "110101")["is_system"]
    assert row(db, "1209")["nature"] == "cr" and row(db, "42")["nature"] == "dr"            # مجمع إهلاك / مردودات
    assert row(db, "51")["classification"] == "cogs" and row(db, "42")["classification"] == "returns"
    assert row(db, "5302")["is_active"] is False and row(db, "4101")["requires_cost_center"]
    assert db.fetch_value("SELECT count(*) FROM acc.accounts WHERE currency_code <> 'EGP'") == 0
    assert db.fetch_value("SELECT row_version FROM acc.accounts WHERE code = '1103'") == 1


# ---- قواعد القاعدة (كل رسالة trigger) ----------------------------------------
def test_database_rules(env):
    db, ids = env["db"], env["ids"]
    raise_ = psycopg.errors.RaiseException
    cases = [
        (lambda: ins(db, "110301", ids["1103"]), "فرعي — مينفعش يبقى تحته"),
        (lambda: ins(db, "120301", ids["1101"]), "لازم يبدأ بكود الأب «1101»"),
        (lambda: ins(db, "1101", ids["1101"]), "لازم يبدأ بكود الأب"),
        (lambda: ins(db, "999", None, account_type=None), "لازم يتحدد نوعه"),
        (lambda: ins(db, "110103", ids["1101"], role_code="suppliers"), "الدور ده مايتحطش"),
        (lambda: db.execute("UPDATE acc.accounts SET is_leaf = true WHERE code = '1106'"), "مينفعش يتحوّل فرعي"),
        (lambda: db.execute("UPDATE acc.accounts SET code = '1196' WHERE code = '1106'"),
         "مينفعش تغيّر كوده ولا مكانه ولا نوعه"),
        (lambda: db.execute("UPDATE acc.accounts SET is_active = false WHERE code = '52'"), "تحته حسابات نشطة"),
        (lambda: db.execute("UPDATE acc.accounts SET code = '1199' WHERE code = '1103'"), "نظامي — كوده ومكانه"),
        (lambda: db.execute("UPDATE acc.accounts SET role_code = NULL WHERE code = '1103'"), "نظامي"),
        (lambda: db.execute("UPDATE acc.accounts SET is_active = false WHERE code = '1103'"), "نظامي — مايتوقفش"),
        (lambda: db.execute("UPDATE acc.accounts SET is_system = true WHERE code = '1201'"), "حساب نظامي بإيدك"),
        (lambda: db.execute("DELETE FROM acc.accounts WHERE code = '1103'"), "نظامي — مايتمسحش"),
        (lambda: db.execute("DELETE FROM acc.accounts WHERE code = '52'"), "تحته حسابات — امسحهم الأول"),
    ]
    for action, text in cases:
        with pytest.raises(raise_, match=text):
            action()
    # أقصى 5 مستويات: المستوى الخامس آخر مستوى (لازم فرعي — فمفيش حاجة تحته)
    l4 = ins(db, "110103", ids["1101"], is_leaf=False)
    with pytest.raises(raise_, match="لازم يبقى فرعي"):
        ins(db, "11010301", l4, is_leaf=False)
    l5 = ins(db, "11010301", l4)
    assert db.fetch_value("SELECT level FROM acc.accounts WHERE id = %s", [l5]) == 5
    with pytest.raises(raise_, match="لازم يبقى فرعي"):
        db.execute("UPDATE acc.accounts SET is_leaf = false WHERE id = %s", [l5])
    with pytest.raises(raise_, match="فرعي — مينفعش يبقى تحته"):
        ins(db, "1101030101", l5)
    # تحت أب موقوف: مينفعش تضيف
    db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [l5])
    db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [l4])
    with pytest.raises(raise_, match="موقوف"):
        ins(db, "11010302", l4)
    db.execute("UPDATE acc.accounts SET name_ar = 'تعديل اسم تحت أب موقوف' WHERE id = %s", [l5])   # عادي
    with pytest.raises(raise_, match="موقوف"):
        db.execute("UPDATE acc.accounts SET is_active = true WHERE id = %s", [l5])
    # النوع والقائمة بيتورثوا (حتى لو اتكتب غلط)، والتصنيف لازم يناسب النوع
    child = ins(db, "5207", ids["52"], account_type="asset", classification="operating_exp")
    assert db.fetch_one("SELECT account_type, statement, level FROM acc.accounts WHERE id = %s", [child]) == {
        "account_type": "expense", "statement": "is", "level": 3}
    with pytest.raises(psycopg.errors.CheckViolation):
        ins(db, "5208", ids["52"], classification="current")
    with pytest.raises(psycopg.errors.UniqueViolation):
        ins(db, "5207", ids["52"], classification="operating_exp")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        ins(db, "5209", ids["52"], classification="operating_exp", currency_code="XXX")
    # row_version: تعديل = +1 · من غير تغيير = زي ما هو
    db.execute("UPDATE acc.accounts SET name_ar = 'الإيجار والمرافق' WHERE code = '5202'")
    db.execute("UPDATE acc.accounts SET name_ar = 'الإيجار والمرافق' WHERE code = '5202'")
    assert row(db, "5202")["row_version"] == 2


# ---- الـ Service -------------------------------------------------------------
def test_code_suggestion_helpers():
    assert next_child_code("1", 1, ["11", "12"]) == "13"
    assert next_child_code("11", 2, ["1101", "1102", "1107"]) == "1108"
    assert next_child_code("1103", 3, []) == "110301"
    assert next_child_code("5", 1, ["51", "52", "53", "54"]) == "55"
    assert next_child_code("1", 1, [f"1{i}" for i in range(1, 10)]) == ""                   # خلصوا
    assert next_child_code("11", 2, ["1199", "1101"]) == "1102"                            # أول فاضي
    rows = [{"id": 1, "code": "1", "name_ar": "الأصول", "parent_id": None},
            {"id": 2, "code": "11", "name_ar": "المتداولة", "parent_id": 1},
            {"id": 3, "code": "1103", "name_ar": "العملاء", "name_en": "Customers", "parent_id": 2},
            {"id": 4, "code": "12", "name_ar": "الثابتة", "parent_id": 1}]
    assert visible_ids(rows, "") is None
    assert visible_ids(rows, "customers") == {1, 2, 3}
    assert visible_ids(rows, "1103") == {1, 2, 3}
    assert visible_ids(rows, "مش موجود") == set()
    assert matching_ids(rows, "customers") == {3} and matching_ids(rows, "") == set()


def test_service_defaults_save_and_rules(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    assert service.suggest_code(ids["1102"]) == "110203"
    assert service.suggest_code(ids["1"]) == "13"
    d = service.new_defaults(ids["1102"])
    assert (d["code"], d["level"], d["account_type"], d["statement"], d["nature"], d["role_code"]) == (
        "110203", 4, "asset", "bs", "dr", "bank")                                         # الوراثة من الأب
    assert d["classification"] == "current" and d["cash_flow"] == "cash" and d["is_leaf"]
    new_id = service.save_account(None, {**d, "name_ar": "بنك القاهرة — جاري", "name_en": "Banque du Caire"})
    acc = service.get_account(new_id)
    assert (acc["code"], acc["level"], acc["role_code"], acc["is_system"], acc["parent_code"]) == (
        "110203", 4, "bank", False, "1102")
    assert service.suggest_code(ids["1102"]) == "110204"
    base = {**d, "name_ar": "حساب"}
    for data, text in (
            ({**base, "code": "11a"}, "أرقام بس"),
            ({**base, "name_ar": "  "}, "مطلوب"),
            ({**base, "parent_id": None}, "تحت حساب"),
            ({**base, "parent_id": ids["1103"], "code": "110301"}, "فرعي — مينفعش"),
            ({**base, "code": "120301"}, "لازم يبدأ بكود الأب"),
            ({**base, "code": "110203"}, "متسجل قبل كده"),
            ({**base, "classification": "cogs"}, "مايناسبش"),
            ({**base, "role_code": "suppliers"}, "بيتحط على حسابات «خصوم» بس"),
            ({**base, "nature": "x"}, "مدين"),
            ({**base, "party_kind": "boss"}, "عميل أو مورد"),
            ({**base, "currency_code": "XXX"}, "مش موجودة")):
        with pytest.raises(BusinessError, match=text):
            service.save_account(None, data)
    # تعديل: الاسم والطبيعة يتغيّروا · النظامي ثابت · اللي تحته حسابات
    acc = service.get_account(new_id)
    service.save_account(new_id, {**acc, "name_ar": "بنك القاهرة", "nature": "cr"}, acc["row_version"])
    assert service.get_account(new_id)["nature"] == "cr"
    customers = service.get_account(ids["1103"])
    service.save_account(ids["1103"], {**customers, "name_ar": "العملاء (محلي)"}, customers["row_version"])
    customers = service.get_account(ids["1103"])
    for change, text in (({"code": "1199"}, "نظامي — كوده"), ({"role_code": None}, "نظامي"),
                         ({"is_leaf": False}, "نظامي"), ({"is_active": False}, "مايتوقفش")):
        with pytest.raises(BusinessError, match=text):
            service.save_account(ids["1103"], {**customers, **change})
    g_and_a = service.get_account(ids["52"])
    for change, text in (({"is_leaf": True}, "يتحوّل «فرعي»"), ({"code": "5299"}, "مينفعش تغيّر كوده"),
                         ({"is_active": False}, "حسابات نشطة")):
        with pytest.raises(BusinessError, match=text):
            service.save_account(ids["52"], {**g_and_a, **change})
    # فرعي ← رئيسي (مفيش تحته) وبعدين حساب تحته
    rent = service.get_account(ids["5202"])
    service.save_account(ids["5202"], {**rent, "is_leaf": False}, rent["row_version"])
    sub = service.new_defaults(ids["5202"])
    assert sub["code"] == "520201" and sub["classification"] == "operating_exp"
    sub_id = service.save_account(None, {**sub, "name_ar": "إيجار المخزن"})
    # المسح
    for target, text in ((ids["1103"], "نظامي — مايتمسحش"), (ids["5202"], "امسحهم الأول")):
        with pytest.raises(BusinessError, match=text):
            service.delete_account(target)
    service.delete_account(sub_id)
    assert service.get_account(sub_id) is None
    logs = db.fetch_all("SELECT action, record_label, details FROM sec.audit_log "
                        "WHERE entity = 'acc.accounts' ORDER BY id")
    assert [log["action"] for log in logs] == ["create", "update", "update", "update", "create", "delete"]
    assert logs[0]["record_label"] == "110203 بنك القاهرة — جاري"
    assert set(logs[1]["details"]["changed"]) >= {"name_ar", "nature"}
    totals = service.summary(service.list_accounts())
    assert totals["asset"] == {"total": 20, "leaf": 14, "main": 6, "inactive": 0}
    assert totals["expense"]["inactive"] == 1


def test_permissions(env):
    service, ids, db = env["service"], env["ids"], env["db"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.chart_of_accounts.view", "acc.chart_of_accounts.edit", "acc.chart_of_accounts.save"})
    rent = service.get_account(ids["5202"])
    service.save_account(ids["5202"], {**rent, "name_ar": "إيجار"}, rent["row_version"])
    with pytest.raises(PermissionDenied):
        service.save_account(None, {**service.new_defaults(ids["52"]), "name_ar": "جديد"})
    with pytest.raises(PermissionDenied):
        service.delete_account(ids["5204"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.chart_of_accounts.%%'")}
    assert codes == {f"acc.chart_of_accounts.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين فتحوا نفس الحساب: الأول حفظ ← التاني يترفض ومافيش حاجة اتمسحت."""
    db, ids = env["db"], env["ids"]
    dev1, dev2 = AccountService(db), AccountService(Database(db.settings))
    try:
        seen1, seen2 = dev1.get_account(ids["5203"]), dev2.get_account(ids["5203"])
        dev2.save_account(ids["5203"], {**seen2, "name_ar": "كهرباء"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_account(ids["5203"], {**seen1, "name_ar": "مياه"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_account(ids["5203"], seen1["row_version"])
        fresh = dev1.get_account(ids["5203"])
        assert fresh["name_ar"] == "كهرباء" and fresh["row_version"] == 2
        dev2.delete_account(ids["5203"], fresh["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتمسح من جهاز تاني"):
            dev1.save_account(ids["5203"], {**fresh, "name_ar": "مياه"}, fresh["row_version"])
        # اتنين بيضيفوا تحت نفس الأب بنفس الكود المقترح: واحد بس يتسجل
        d1, d2 = dev1.new_defaults(ids["52"]), dev2.new_defaults(ids["52"])
        assert d1["code"] == d2["code"] == "5207"                  # 5205 / 5206 من 0012
        dev1.save_account(None, {**d1, "name_ar": "تليفونات"})
        with pytest.raises(BusinessError, match="متسجل قبل كده"):
            dev2.save_account(None, {**d2, "name_ar": "إنترنت"})
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def tab_counts(screen) -> list[str]:
    return [b.text().replace("⁦", "").replace("⁩", "") for b in screen.type_tabs.values()]


def test_screen_tabs_tree_and_expand_collapse(env):
    screen = make_screen(env["db"])
    assert tab_counts(screen) == ["1 الأصول (19)", "2 الخصوم (7)", "3 حقوق الملكية (5)",
                                  "4 الإيرادات (8)", "5 المصروفات (17)"]
    assert screen.current_type == "asset" and screen.type_tabs["asset"].isChecked()
    assert screen.code_edit.text() == "1" and screen.type_edit.text().startswith("أصول")
    assert screen.statement_edit.text().startswith("الميزانية") and screen.system_label.isVisibleTo(screen)
    assert screen.tree.topLevelItemCount() == 1 and len(screen.items) == 19
    ids = env["ids"]
    item = screen.items[ids["1103"]]
    assert "1103" in item.text(0) and "العملاء" in item.text(0) and "📄" in item.text(0)
    assert item.text(1) == "العملاء" and "نظامي" in item.text(2)
    assert "📁" in screen.items[ids["1101"]].text(0)
    # أول فتح: المستوى 1 و2 مفتوحين
    assert screen.items[ids["11"]].isExpanded() and not screen.items[ids["1101"]].isExpanded()
    screen.expand_all()
    assert all(it.isExpanded() for it in screen.items.values() if it.childCount())
    screen.collapse_all()
    assert not any(it.isExpanded() for it in screen.items.values())
    assert not screen.delete_button.isEnabled()                                     # الجذر نظامي
    # تبويب تاني ← شجرته بس، وبيفتح الجذر بتاعه
    screen.type_tabs["expense"].click()
    assert screen.current_type == "expense" and len(screen.items) == 17 and screen.code_edit.text() == "5"
    assert screen.class_combo.currentData() == "operating_exp"
    assert "موقوف" in screen.items[ids["5302"]].text(2)
    assert screen.count_label.text().replace("⁦", "").replace("⁩", "") == "17 حساب · 13 فرعي (بيقبل قيود) · 4 رئيسي · موقوف 1"
    # الرجوع للأصول بيفتح آخر حساب كان مفتوح فيها + الطي بيفضل
    screen.type_tabs["asset"].click()
    assert screen.code_edit.text() == "1" and not screen.items[ids["11"]].isExpanded()


def test_screen_new_under_selected_save_and_delete(env):
    screen, ids, service = make_screen(env["db"]), env["ids"], env["service"]
    screen.load(ids["1102"])                                     # رئيسي ← الجديد تحته
    screen.new_record()
    assert screen.mode == "new" and not screen.tree.isEnabled() and not screen.type_tabs["equity"].isEnabled()
    assert screen.parent_combo.currentData() == ids["1102"] and screen.code_edit.text() == "110203"
    assert screen.level_edit.text() == "4" and screen.role_combo.currentData() == "bank"
    assert screen.cash_flow_combo.currentData() == "cash" and screen.leaf_radio.isChecked()
    screen.name_ar.setText("بنك القاهرة — جاري")
    screen.save()
    assert env["shown"] == ["saved"]
    new_id = screen.current_id
    assert service.get_account(new_id)["code"] == "110203" and new_id in screen.items
    assert screen.tree.currentItem() is screen.items[new_id] and screen.items[ids["1102"]].isExpanded()
    assert tab_counts(screen)[0] == "1 الأصول (20)"
    # فرعي محدد ← الجديد جنبه (تحت أبوه)
    screen.new_record()
    assert screen.parent_combo.currentData() == ids["1102"] and screen.code_edit.text() == "110204"
    # تغيير «تحت حساب» بيغيّر الكود المقترح والوراثة
    screen.parent_combo.setCurrentIndex(screen.parent_combo.findData(ids["1101"]))
    assert screen.code_edit.text() == "110103" and screen.role_combo.currentData() == "cash"
    screen.parent_combo.setCurrentIndex(screen.parent_combo.findData(ids["1106"]))
    assert screen.code_edit.text() == "110603" and screen.role_combo.currentData() is None
    # الدور بيحط الطرف بتاعه لوحده
    screen.role_combo.setCurrentIndex(screen.role_combo.findData("customers"))
    assert screen.party_combo.currentData() == "customer"
    screen.cancel()
    assert screen.mode == "view" and screen.current_id == new_id                   # «إلغاء» رجع للي كان مفتوح
    # رسالة غلط: كود مش بيبدأ بكود الأب
    screen.new_record()
    screen.code_edit.setText("999")
    screen.name_ar.setText("غلط")
    screen.save()
    assert "لازم يبدأ بكود الأب" in env["shown"][-1] and screen.mode == "new"
    screen.cancel()
    # الحذف ← بيفتح الأب
    screen.load(new_id)
    assert screen.delete_button.isEnabled()
    screen.delete()
    assert service.get_account(new_id) is None and screen.current_id == ids["1102"]
    assert new_id not in screen.items


def test_screen_edit_locks_system_and_parent(env):
    screen, ids = make_screen(env["db"]), env["ids"]
    screen.load(ids["1103"])                                     # نظامي
    screen.edit_record()
    assert screen.mode == "edit" and screen.name_ar.isEnabled()
    assert not screen.code_edit.isEnabled() and not screen.parent_combo.isEnabled()
    assert not screen.role_combo.isEnabled() and not screen.is_active.isEnabled()
    screen.name_ar.setText("العملاء المحليين")
    screen.save()
    assert env["service"].get_account(ids["1103"])["name_ar"] == "العملاء المحليين"
    screen.load(ids["52"])                                       # تحته حسابات
    assert not screen.delete_button.isEnabled()
    screen.edit_record()
    assert not screen.code_edit.isEnabled() and not screen.leaf_radio.isEnabled() and screen.role_combo.isEnabled()
    screen.cancel()


def test_screen_f1_and_search(env, monkeypatch):
    screen, ids = make_screen(env["db"]), env["ids"]
    from app.ui.dialogs import lookup_dialog

    class FakeLookup:
        def __init__(self, _title, _columns, rows, _fmt, _parent):
            assert len(rows) == 56                               # الدليل كله
            self.selected_id = ids["2103"]

        def exec(self):
            return True

    monkeypatch.setattr(lookup_dialog, "LookupDialog", FakeLookup)
    screen.open_lookup()
    assert screen.current_type == "liability" and screen.type_tabs["liability"].isChecked()
    assert screen.code_edit.text() == "2103" and screen.tree.currentItem() is screen.items[ids["2103"]]
    # البحث: اللي بيطابق + آباءه، وعدد كل تبويب
    screen.type_tabs["asset"].click()
    screen.search.setText("خصم من المنبع")
    assert set(screen.items) == {ids["1"], ids["11"], ids["1106"], ids["110602"]}
    assert tab_counts(screen) == ["1 الأصول (1)", "2 الخصوم (1)", "3 حقوق الملكية (0)",
                                  "4 الإيرادات (0)", "5 المصروفات (0)"]
    assert "نتايج البحث" in screen.count_label.text()
    # F1 لحساب مش ظاهر في البحث ← البحث بيتمسح
    screen.load(ids["1103"])
    assert screen.search.text() == "" and ids["1103"] in screen.items and len(screen.items) == 19


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("acc", "chart_of_accounts")
    assert target.name_ar == "شجرة الحسابات" and target.icon == "🌳"
    assert reg.section("acc").groups[0].title == "دليل الحسابات"
    assert "chart_of_accounts" in SCREEN_FACTORIES


# ---- تفريع أي حساب فرعي (0011 — «+ فرعي تحت المحدد») ---------------------------
def tax_accounts(db, code):
    return db.fetch_one("SELECT sales_account_id AS s, purchase_account_id AS p FROM acc.tax_codes WHERE code = %s",
                        [code])


def test_split_account_database(env):
    db, ids = env["db"], env["ids"]
    raise_ = psycopg.errors.RaiseException
    # حساب عادي (5402 مصاريف بنكية): رئيسي + «540201 مصاريف بنكية — عام» (مع كل تفريع — قرار المستخدم)
    general = db.fetch_value("SELECT acc.split_account(%s)", [ids["5402"]])
    bank = row(db, "5402")
    assert bank["is_leaf"] is False and bank["row_version"] == 2 and bank["is_system"] is False
    g = row(db, "540201")
    assert (g["id"], g["name_ar"], g["name_en"], g["is_system"], g["role_code"], g["level"], g["classification"]) == (
        general, "مصاريف بنكية — عام", "Bank Charges — General", False, None, 4, "finance_exp")
    ins(db, "540202", ids["5402"], account_type="expense", classification="finance_exp", name_ar="بنك أهلي")
    assert row(db, "540202")["level"] == 4
    # نظامي بدور (4101 مبيعات بضاعة): «410101 — عام» بالدور والنظامي ومركز التكلفة، والأب بيفضل نظامي بدوره
    general = db.fetch_value("SELECT acc.split_account(%s)", [ids["4101"]])
    g = db.fetch_one("SELECT * FROM acc.accounts WHERE id = %s", [general])
    assert (g["code"], g["name_ar"], g["name_en"], g["role_code"], g["is_system"], g["is_leaf"], g["level"]) == (
        "410101", "مبيعات بضاعة — عام", "Goods Sales — General", "sales", True, True, 4)
    assert g["requires_cost_center"] and g["classification"] == "operating_rev" and g["nature"] == "cr"
    sales = row(db, "4101")
    assert sales["is_leaf"] is False and sales["is_system"] and sales["role_code"] == "sales"
    # مستوى 2 (51 تكلفة المبيعات) ← 5101
    db.fetch_value("SELECT acc.split_account(%s)", [ids["51"]])
    assert row(db, "5101")["role_code"] == "cogs" and row(db, "5101")["name_ar"] == "تكلفة المبيعات — عام"
    # مربوط بكود ضريبة (2103 ض.ق.م مخرجات ← VAT14 / VAT0): الربط بينزل للعام
    vat14_version = db.fetch_value("SELECT row_version FROM acc.tax_codes WHERE code = 'VAT14'")
    general = db.fetch_value("SELECT acc.split_account(%s)", [ids["2103"]])
    assert row(db, "210301")["id"] == general and row(db, "210301")["role_code"] == "vat_out"
    assert tax_accounts(db, "VAT14")["s"] == general and tax_accounts(db, "VAT0")["s"] == general
    assert tax_accounts(db, "VAT14")["p"] == ids["110601"]                                # الناحية التانية زي ما هي
    assert db.fetch_value("SELECT row_version FROM acc.tax_codes WHERE code = 'VAT14'") > vat14_version
    # مستوى 4 مربوط (110601 ض.ق.م مدخلات) ← 11060101 مستوى خامس فرعي
    general = db.fetch_value("SELECT acc.split_account(%s)", [ids["110601"]])
    assert row(db, "11060101")["level"] == 5 and tax_accounts(db, "VAT14")["p"] == general
    # كود «العام» اللي الـ Service بيبعته
    general = db.fetch_value("SELECT acc.split_account(%s, %s)", [ids["1103"], "110305"])
    assert row(db, "110305")["id"] == general and row(db, "110305")["party_kind"] == "customer"
    # الأخطاء
    for target, text in ((ids["52"], "رئيسي أصلاً"), (ids["5302"], "موقوف"),
                         (row(db, "11060101")["id"], "المستوى الخامس"), (999999, "مش موجود")):
        with pytest.raises(raise_, match=text):
            db.fetch_value("SELECT acc.split_account(%s)", [target])
    # القواعد العادية زي ما هي: النظامي مايتحوّلش رئيسي بـ UPDATE عادي (ولا بالإعداد لحساب تاني)
    with pytest.raises(raise_, match="نظامي — كوده ومكانه"):
        db.execute("UPDATE acc.accounts SET is_leaf = false WHERE code = '2101'")
    with pytest.raises(raise_, match="نظامي"):
        with db.atomic() as tx:
            tx.execute("SELECT set_config('acc.split_account_id', %s, true)", [str(ids["1103"])])
            tx.execute("UPDATE acc.accounts SET is_leaf = false WHERE code = '2101'")
    db.execute("UPDATE acc.tax_codes SET purchase_account_id = %s WHERE code = 'VAT0'", [ids["1201"]])
    with pytest.raises(raise_, match="مربوط بأكواد الضريبة"):
        db.execute("UPDATE acc.accounts SET is_leaf = false WHERE code = '1201'")
    # فرعي عادي (مش نظامي) بس مربوط بضريبة ← بيتعمله «عام» برضه (مش نظامي) والربط بينزل له
    general = db.fetch_value("SELECT acc.split_account(%s)", [ids["1201"]])
    assert row(db, "120101")["id"] == general and not row(db, "120101")["is_system"]
    assert tax_accounts(db, "VAT0")["p"] == general and row(db, "120101")["name_ar"] == "أجهزة وحاسبات — عام"
    with pytest.raises(raise_, match="فرّع منه"):
        ins(db, "520401", ids["5204"], account_type="expense", classification="operating_exp")
    assert db.fetch_value("SELECT current_setting('acc.split_account_id', true)") in (None, "")


def test_split_account_service(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    # حساب عادي: «540201 — عام» + الجديد 540202 بيورث من الأب
    plan = service.branch_plan(ids["5402"])
    assert plan["general"] == {"code": "540201", "name_ar": "مصاريف بنكية — عام", "role_code": None,
                               "role_name": None, "is_system": False, "tax_links": [],
                               "default_keys": ["مصاريف بنكية"]}
    assert plan["defaults"]["code"] == "540202" and plan["defaults"]["level"] == 4
    assert plan["defaults"]["classification"] == "finance_exp" and plan["expected_version"] == 1
    assert row(db, "5402")["is_leaf"]                                                   # الخطة مابتحفظش حاجة
    nbe = service.branch_account(ids["5402"], {**plan["defaults"], "name_ar": "مصاريف بنكية بنك أهلي"},
                                 plan["expected_version"])
    acc = service.get_account(nbe)
    assert (acc["code"], acc["parent_code"], acc["level"], acc["is_leaf"]) == ("540202", "5402", 4, True)
    assert not service.get_account(ids["5402"])["is_leaf"] and row(db, "540201")["name_ar"] == "مصاريف بنكية — عام"
    misr = service.save_account(None, {**service.new_defaults(ids["5402"]), "name_ar": "مصاريف بنكية بنك مصر"})
    assert service.get_account(misr)["code"] == "540203"
    # نظامي بدور: «عام» 5101 + الجديد 5102
    plan = service.branch_plan(ids["51"])
    assert plan["general"] == {"code": "5101", "name_ar": "تكلفة المبيعات — عام", "role_code": "cogs",
                               "role_name": "تكلفة المبيعات", "is_system": True, "tax_links": [],
                               "default_keys": ["تكلفة المبيعات"]}
    assert plan["defaults"]["code"] == "5102" and plan["defaults"]["role_code"] == "cogs"
    new_id = service.branch_account(ids["51"], {**plan["defaults"], "name_ar": "تكلفة مبيعات خدمات"}, 1)
    assert service.get_account(new_id)["code"] == "5102" and not service.get_account(new_id)["is_system"]
    assert row(db, "5101")["is_system"] and row(db, "5101")["role_code"] == "cogs"
    # مربوط بضريبة (110602 خصم المنبع لينا ← WHT1/3/5): الربط بينزل للعام
    plan = service.branch_plan(ids["110602"])
    assert plan["general"]["tax_links"] == ["WHT1", "WHT3", "WHT5"] and plan["defaults"]["code"] == "11060202"
    service.branch_account(ids["110602"], {**plan["defaults"], "name_ar": "خصم منبع — عملاء حكومة"}, 1)
    general_id = row(db, "11060201")["id"]
    assert {tax_accounts(db, c)["s"] for c in ("WHT1", "WHT3", "WHT5")} == {general_id}
    assert service.repository.tax_links(ids["110602"]) == []
    # الكود المقترح بيتخطى كود واخده حساب جنبه (520101 تحت 52 مباشرةً)
    ins(db, "520101", ids["52"], account_type="expense", classification="operating_exp")
    plan = service.branch_plan(ids["5201"])
    assert plan["general"]["code"] == "520102" and plan["defaults"]["code"] == "520103"
    # الأخطاء: الرئيسي والمستوى الخامس والموقوف + غلط في الجديد = ولا حاجة اتحفظت
    for target, text in ((ids["52"], "رئيسي أصلاً"), (ids["5302"], "موقوف"), (general_id, "المستوى الخامس")):
        with pytest.raises(BusinessError, match=text):
            service.branch_plan(target)
    plan = service.branch_plan(ids["1103"])
    with pytest.raises(BusinessError, match="مطلوب"):
        service.branch_account(ids["1103"], {**plan["defaults"], "name_ar": " "}, 1)
    with pytest.raises(BusinessError, match="متسجل قبل كده"):
        service.branch_account(ids["1103"], {**plan["defaults"], "code": "110301", "name_ar": "نفس كود العام"}, 1)
    assert row(db, "1103")["is_leaf"] and row(db, "110301") is None and row(db, "1103")["row_version"] == 1
    # رسايل «تعديل» بتدل على الزرار
    customers = service.get_account(ids["1103"])
    with pytest.raises(BusinessError, match="فرعي تحت المحدد"):
        service.save_account(ids["1103"], {**customers, "is_leaf": False}, 1)
    with pytest.raises(BusinessError, match="فرّع منه"):
        service.save_account(None, {**plan["defaults"], "parent_id": ids["5204"], "code": "520401", "name_ar": "x"})
    # سجل التعديلات (آخر تفريع): الأب + العام + أكواد الضريبة الـ 3 + الحساب الافتراضي + الجديد — كله في نفس العملية
    logs = db.fetch_all("SELECT entity, action, record_label, details FROM sec.audit_log "
                        "WHERE id > (SELECT max(id) FROM sec.audit_log WHERE record_label LIKE '5102 %%') ORDER BY id")
    assert [(log["entity"], log["action"], log["record_label"]) for log in logs] == [
        ("acc.accounts", "update", "110602 خصم من المنبع (لينا)"),
        ("acc.accounts", "create", "11060201 خصم من المنبع (لينا) — عام"),
        ("acc.tax_codes", "update", "WHT1"), ("acc.tax_codes", "update", "WHT3"), ("acc.tax_codes", "update", "WHT5"),
        ("acc.default_accounts", "update", "خصم من المنبع (لينا)"),                     # المفتاح نزل للعام (0012)
        ("acc.accounts", "create", "11060202 خصم منبع — عملاء حكومة")]
    assert "is_leaf" in logs[0]["details"]["changed"]
    assert logs[0]["details"]["extra"]["split"] == {"general_code": "11060201",
                                                    "tax_codes": ["WHT1", "WHT3", "WHT5"],
                                                    "default_accounts": ["wht_rec"]}
    assert "sales_account_id" in logs[2]["details"]["changed"]


def test_split_account_permissions(env):
    service, ids, db = env["service"], env["ids"], env["db"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.chart_of_accounts.view", "acc.chart_of_accounts.create", "acc.chart_of_accounts.save"})
    plan = service.branch_plan(ids["5402"])
    with pytest.raises(PermissionDenied):                         # من غير «تعديل» مينفعش يحوّل الأب رئيسي
        service.branch_account(ids["5402"], {**plan["defaults"], "name_ar": "بنك أهلي"}, 1)
    assert row(db, "5402")["is_leaf"]


def test_split_account_two_devices(env):
    """جهازين على نفس الحساب الفرعي: واحد بس يفرّع، والتاني يترفض ومافيش حاجة اتعملت مرتين."""
    import threading
    import time

    db, ids = env["db"], env["ids"]
    dev1, dev2 = AccountService(db), AccountService(Database(db.settings))
    try:
        p1, p2 = dev1.branch_plan(ids["4101"]), dev2.branch_plan(ids["4101"])
        assert p1["defaults"]["code"] == p2["defaults"]["code"] == "410102"
        dev1.branch_account(ids["4101"], {**p1["defaults"], "name_ar": "مبيعات أجهزة"}, p1["expected_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev2.branch_account(ids["4101"], {**p2["defaults"], "name_ar": "مبيعات قطع غيار"}, p2["expected_version"])
        assert db.fetch_value("SELECT count(*) FROM acc.accounts WHERE parent_id = %s", [ids["4101"]]) == 2
        assert db.fetch_value("SELECT count(*) FROM acc.accounts WHERE code LIKE '4101%%' AND is_system") == 2
        # في نفس اللحظة: جهاز ماسك الحساب (بيعدّل اسمه) ← التاني بيستنى القفل وبعدين يترفض
        p2 = dev2.branch_plan(ids["5203"])
        result: dict = {}

        def second_device():
            try:
                dev2.branch_account(ids["5203"], {**p2["defaults"], "name_ar": "كهرباء المخزن"}, 1)
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        with db.atomic() as tx:
            tx.execute("SELECT 1 FROM acc.accounts WHERE id = %s FOR UPDATE", [ids["5203"]])
            tx.execute("UPDATE acc.accounts SET name_ar = 'كهرباء ومياه وغاز' WHERE id = %s", [ids["5203"]])
            worker = threading.Thread(target=second_device)
            worker.start()
            time.sleep(0.7)
            assert worker.is_alive()                              # مستني القفل
        worker.join(10)
        assert isinstance(result.get("error"), ConcurrentEditError)
        fresh = row(db, "5203")
        assert fresh["is_leaf"] and fresh["name_ar"] == "كهرباء ومياه وغاز"
        assert db.fetch_value("SELECT count(*) FROM acc.accounts WHERE parent_id = %s", [ids["5203"]]) == 0
    finally:
        dev2.db.close()


def test_screen_branch_button(env, monkeypatch):
    screen, ids, service, db = make_screen(env["db"]), env["ids"], env["service"], env["db"]
    questions: list[str] = []
    answer = {"value": True}
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, text: questions.append(text) or answer["value"])
    toolbar = screen.new_button.parentWidget().layout()
    assert toolbar.indexOf(screen.branch_button) == toolbar.indexOf(screen.new_button) + 1   # جنب «حساب جديد»
    assert screen.branch_button.text() == "+ فرعي تحت المحدد" and screen.branch_button.isVisibleTo(screen)
    # رئيسي ← جديد تحته على طول (من غير سؤال)
    screen.load(ids["1102"])
    screen.branch_record()
    assert screen.mode == "new" and screen.parent_combo.currentData() == ids["1102"] and not questions
    assert screen.code_edit.text() == "110203" and not screen.split_note.isVisibleTo(screen)
    screen.cancel()
    # فرعي عادي (5402): السؤال (فيه «العام») ← «لا» = ولا حاجة
    screen.type_tabs["expense"].click()
    screen.load(ids["5402"])
    answer["value"] = False
    screen.branch_record()
    assert screen.mode == "view" and "فرعي — عايز تحوّله رئيسي وتفرّع منه؟" in questions[-1]
    assert "⁦5402⁩ مصاريف بنكية" in questions[-1]
    assert "⁦540201⁩ مصاريف بنكية — عام» وينقل له: الحسابات الافتراضية (مصاريف بنكية)" in questions[-1]
    assert "⁦540202⁩" in questions[-1]
    # «نعم» ← جديد تحته بالكود المقترح، والأب مقفول، و«إلغاء» = ولا حاجة اتغيّرت
    answer["value"] = True
    screen.branch_record()
    assert screen.mode == "new" and screen.code_edit.text() == "540202" and screen.level_edit.text() == "4"
    assert screen.parent_combo.currentData() == ids["5402"] and not screen.parent_combo.isEnabled()
    assert screen.split_note.isVisibleTo(screen) and "هيتحوّل رئيسي" in screen.split_note.text()
    assert screen.class_combo.currentData() == "finance_exp"
    screen.cancel()
    assert screen.mode == "view" and screen.current_id == ids["5402"] and row(db, "5402")["is_leaf"]
    assert not screen.split_note.isVisibleTo(screen)
    # «نعم» + «حفظ»
    screen.branch_record()
    screen.name_ar.setText("مصاريف بنكية بنك أهلي")
    screen.save()
    assert env["shown"][-1] == "saved" and screen.mode == "view"
    new_id = screen.current_id
    assert service.get_account(new_id)["code"] == "540202" and not service.get_account(ids["5402"])["is_leaf"]
    assert row(db, "540201")["id"] in screen.items                                     # «العام» ظاهر في الشجرة
    assert "📁" in screen.items[ids["5402"]].text(0) and screen.items[new_id].parent() is screen.items[ids["5402"]]
    # بعدها «حساب جديد» على 540202 = جنبه (540203)
    screen.new_record()
    assert screen.code_edit.text() == "540203" and screen._split is None
    screen.cancel()
    # نظامي بدور (51 تكلفة المبيعات): السؤال بيقول «العام» وبينقل له إيه
    screen.load(ids["51"])
    screen.branch_record()
    text = questions[-1]
    assert "⁦5101⁩ تكلفة المبيعات — عام" in text and "الدور (تكلفة المبيعات)" in text and "النظامي" in text
    assert screen.code_edit.text() == "5102" and screen.role_combo.currentData() == "cogs"
    assert "عام" in screen.split_note.text()
    screen.name_ar.setText("تكلفة مبيعات خدمات")
    screen.save()
    assert row(db, "5101")["is_system"] and service.get_account(screen.current_id)["code"] == "5102"
    assert "نظامي" in screen.items[row(db, "5101")["id"]].text(2)
    # مربوط بضريبة: الأكواد في السؤال
    screen.type_tabs["liability"].click()
    screen.load(ids["2103"])
    screen.branch_record()
    assert "ربط أكواد الضريبة (⁦VAT0⁩، ⁦VAT14⁩)" in questions[-1]
    screen.cancel()
    # جهاز تاني عدّل الحساب بعد السؤال ← رسالة تعارض و«تحديث» بيفتح الحساب بآخر نسخة
    screen.load(ids["2104"])
    screen.branch_record()
    screen.name_ar.setText("خصم منبع — مقاولين")
    other = AccountService(Database(db.settings))
    try:
        acc = other.get_account(ids["2104"])
        other.save_account(ids["2104"], {**acc, "name_ar": "خصم من المنبع (مستحق للمصلحة)"}, acc["row_version"])
    finally:
        other.db.close()
    monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: env["shown"].append(str(exc)) or True)
    screen.save()
    assert "اتعدّل من جهاز تاني" in env["shown"][-1] and screen.mode == "view"
    assert screen.current_id == ids["2104"] and screen.name_ar.text() == "خصم من المنبع (مستحق للمصلحة)"
    assert row(db, "2104")["is_leaf"] and row(db, "210401") is None
    # الزرار مقفول: الموقوف
    screen.type_tabs["expense"].click()
    screen.load(ids["5302"])
    assert not screen.branch_button.isEnabled() and "موقوف" in screen.branch_button.toolTip()
