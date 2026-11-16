"""اختبارات «أنواع الأصناف» (0028: الشجرة لحد 5 مستويات + الاسم تحت نفس الأب + الحسابات الأربعة والتوريث من الأب
ثم الافتراضي + حماية الحساب من الدليل + التفريع + الـ Service + الصلاحيات + جهازين + الشاشة بالشجرة)."""

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
from app.repositories.item_category_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.item_category_service import KINDS, ItemCategoryService, account_fits, full_path
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

SEED = ["BMS", "Installation", "MCC", "Office", "Panel", "ABB", "HIMEL", "ide", "KSA", "Nara", "Rittal", "Workshop",
        "FIRE ALARM"]


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
    yield {"db": temp_db, "shown": shown, "service": ItemCategoryService(temp_db)}
    SESSION.clear()
    gc.collect()


def cat(db, name, parent=None):
    if parent is None:
        return db.fetch_one("SELECT * FROM inv.item_categories WHERE name = %s AND parent_id IS NULL", [name])
    return db.fetch_one("SELECT c.* FROM inv.item_categories c JOIN inv.item_categories p ON p.id = c.parent_id "
                        "WHERE c.name = %s AND p.name = %s", [name, parent])


def acc(db, code):
    return db.fetch_one("SELECT * FROM acc.accounts WHERE code = %s", [code])


def acc_id(db, code):
    return db.fetch_value("SELECT id FROM acc.accounts WHERE code = %s", [code])


def default_id(db, key):
    return db.fetch_value("SELECT account_id FROM acc.default_accounts WHERE key = %s", [key])


def effective(db, category_id, kind):
    return db.fetch_value("SELECT inv.item_category_account(%s, %s)", [category_id, kind])


def inventory_accounts(db):
    """يفرّع 1105 ← 110501 «المخزون — عام» + 110502 «مخزون لوحات» (بيورث دور المخزون)."""
    db.fetch_value("SELECT acc.split_account(%s)", [acc_id(db, "1105")])
    parent = acc(db, "1105")
    db.execute("INSERT INTO acc.accounts (code, name_ar, parent_id, is_leaf, nature, classification, cash_flow, "
               "role_code, is_active) VALUES ('110502', 'مخزون لوحات', %s, true, 'dr', 'current', 'op', "
               "'inventory', true)", [parent["id"]])
    return acc_id(db, "110501"), acc_id(db, "110502")


def pay(**extra):
    return {"name": "Cables", "name_ar": "كابلات", "parent_id": None, "notes": None, "new_items_service": False,
            "is_active": True, "inventory_account_id": None, "sales_account_id": None, "cogs_account_id": None,
            "sales_return_account_id": None, **extra}


# ---- القاعدة ---------------------------------------------------------------
def test_helpers():
    assert [format_code(n) for n in (1, 12, 999, 1000)] == ["CAT-001", "CAT-012", "CAT-999", "CAT-1000"]
    assert account_fits({"account_type": "asset", "role_code": "inventory"}, "inventory")
    assert not account_fits({"account_type": "asset", "role_code": "cash"}, "inventory")
    assert account_fits({"account_type": "revenue", "classification": "operating_rev"}, "sales")
    assert not account_fits({"account_type": "revenue", "classification": "returns"}, "sales")
    assert account_fits({"account_type": "revenue", "classification": "returns"}, "sales_ret")
    assert account_fits({"account_type": "expense", "classification": "cogs"}, "cogs")
    rows = {1: {"id": 1, "name": "Panel", "parent_id": None}, 2: {"id": 2, "name": "ABB", "parent_id": 1},
            3: {"id": 3, "name": "Small", "parent_id": 2}}
    assert full_path(rows[3], rows) == "Panel / ABB / Small"


def test_seed_and_database_rules(env):
    db = env["db"]
    rows = db.fetch_all("SELECT c.code, c.name, c.access_name, p.name AS parent FROM inv.item_categories c "
                        "LEFT JOIN inv.item_categories p ON p.id = c.parent_id ORDER BY c.seq")
    assert [r["name"] for r in rows] == SEED
    assert rows[0]["code"] == "CAT-001" and rows[-1]["code"] == "CAT-013"
    assert [r["name"] for r in rows if r["parent"] == "Panel"] == ["ABB", "HIMEL", "ide", "KSA", "Nara", "Rittal"]
    assert cat(db, "ABB", "Panel")["access_name"] == "Panel/ABB" and cat(db, "Panel")["access_name"] is None
    assert db.fetch_value("SELECT count(*) FROM inv.item_categories WHERE name ILIKE 'zx%%'") == 0
    assert cat(db, "Installation")["sales_account_id"] == acc_id(db, "4102")          # إيرادات تركيبات وخدمات
    panel, bms = cat(db, "Panel"), cat(db, "BMS")
    # الاسم: متنضف ومايتكررش تحت نفس الأب — ونفس الاسم تحت أب تاني ماشي
    for name, parent, err in (("bms", None, psycopg.errors.UniqueViolation),
                              ("abb", panel["id"], psycopg.errors.UniqueViolation),
                              (" X", None, psycopg.errors.CheckViolation),
                              ("A  B", None, psycopg.errors.CheckViolation)):
        with pytest.raises(err):
            db.execute("INSERT INTO inv.item_categories (seq, name, parent_id) VALUES (99, %s, %s)", [name, parent])
    db.execute("INSERT INTO inv.item_categories (seq, name, parent_id) VALUES (14, 'ABB', %s)", [bms["id"]])
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE inv.item_categories SET seq = 77 WHERE seq = 1")
    # الشجرة: لحد 5 مستويات · مش تحت نفسه · الأب الموقوف · الإيقاف والمسح وتحته أنواع
    parent_id = cat(db, "ABB", "Panel")["id"]                                          # المستوى 2
    for seq in (15, 16, 17):                                                           # 3 · 4 · 5
        parent_id = db.fetch_value("INSERT INTO inv.item_categories (seq, name, parent_id) VALUES (%s, %s, %s) "
                                   "RETURNING id", [seq, f"L{seq}", parent_id])
    with pytest.raises(psycopg.errors.RaiseException, match="5 مستويات"):
        db.execute("INSERT INTO inv.item_categories (seq, name, parent_id) VALUES (18, 'L6', %s)", [parent_id])
    with pytest.raises(psycopg.errors.RaiseException, match="تحت نفسه"):
        db.execute("UPDATE inv.item_categories SET parent_id = %s WHERE id = %s", [parent_id, panel["id"]])
    with pytest.raises(psycopg.errors.RaiseException, match="تحته أنواع نشطة"):
        db.execute("UPDATE inv.item_categories SET is_active = false WHERE id = %s", [panel["id"]])
    with pytest.raises(psycopg.errors.RaiseException, match="تحته أنواع"):
        db.execute("DELETE FROM inv.item_categories WHERE id = %s", [panel["id"]])
    db.execute("UPDATE inv.item_categories SET is_active = false WHERE name = 'Workshop'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوف"):
        db.execute("INSERT INTO inv.item_categories (seq, name, parent_id) VALUES (19, 'Tools', %s)",
                   [cat(db, "Workshop")["id"]])
    # الحسابات: فرعي ومن النوع الصح
    for col, code, msg in (("inventory_account_id", "110101", "مخزون"), ("inventory_account_id", "4101", "مخزون"),
                           ("sales_account_id", "42", "مبيعات"), ("sales_account_id", "41", "رئيسي"),
                           ("cogs_account_id", "5201", "تكلفة"), ("sales_return_account_id", "4101", "مردودات")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(f"UPDATE inv.item_categories SET {col} = %s WHERE id = %s", [acc_id(db, code), bms["id"]])
    v = bms["row_version"]
    db.execute("UPDATE inv.item_categories SET cogs_account_id = %s WHERE id = %s", [acc_id(db, "51"), bms["id"]])
    assert cat(db, "BMS")["row_version"] == v + 1


def test_effective_accounts_and_split(env):
    """الحساب الفعلي: حسابه ← أقرب أب ← الافتراضي · التفريع بينزّل حسابات الأنواع على «— عام» ·
    الحساب المربوط مايتمسحش ولا يتوقف ولا يتحوّل رئيسي من غير التفريع."""
    db = env["db"]
    general, panels = inventory_accounts(db)
    assert default_id(db, "inventory") == general                                     # الافتراضي نزل على العام
    panel, abb = cat(db, "Panel"), cat(db, "ABB", "Panel")
    small = db.fetch_value("INSERT INTO inv.item_categories (seq, name, parent_id) VALUES (14, 'Small', %s) "
                           "RETURNING id", [abb["id"]])
    for kind in KINDS:                                                                 # كله على الافتراضي
        assert effective(db, small, kind) == default_id(db, kind), kind
    db.execute("UPDATE inv.item_categories SET inventory_account_id = %s WHERE id = %s", [panels, panel["id"]])
    assert effective(db, small, "inventory") == panels and effective(db, abb["id"], "inventory") == panels
    assert effective(db, cat(db, "BMS")["id"], "inventory") == general
    db.execute("UPDATE inv.item_categories SET inventory_account_id = %s WHERE id = %s", [general, abb["id"]])
    assert effective(db, small, "inventory") == general                               # أقرب أب
    # حماية الحساب من الدليل
    for sql, msg in (("DELETE FROM acc.accounts WHERE id = %s", "مربوط بنوع الأصناف"),
                     ("UPDATE acc.accounts SET is_active = false WHERE id = %s", "أنواع أصناف نشطة"),
                     ("UPDATE acc.accounts SET is_leaf = false WHERE id = %s", "فرّعه"),
                     ("UPDATE acc.accounts SET role_code = NULL WHERE id = %s", "دوره")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(sql, [panels])
    # التفريع: Panel بينزل على «110502 01 — عام»
    new_general = db.fetch_value("SELECT acc.split_account(%s)", [panels])
    assert acc(db, "11050201")["id"] == new_general
    assert cat(db, "Panel")["inventory_account_id"] == new_general
    assert effective(db, small, "inventory") == general                               # ABB لسه على 110501
    # المبيعات: حساب فرعي جديد تحت 41 بيتقبل · والمردودات على 42
    db.execute("UPDATE inv.item_categories SET sales_account_id = %s, sales_return_account_id = %s WHERE id = %s",
               [acc_id(db, "4102"), acc_id(db, "42"), cat(db, "BMS")["id"]])
    assert effective(db, cat(db, "BMS")["id"], "sales_ret") == acc_id(db, "42")


def test_service(env):
    db, service = env["db"], env["service"]
    assert service.new_defaults()["code"] == "CAT-014"
    panel = cat(db, "Panel")
    db.execute("UPDATE inv.item_categories SET new_items_service = true WHERE id = %s", [panel["id"]])
    assert service.new_defaults(panel["id"])["new_items_service"] is True              # زي أبوه
    cid = service.save_category(None, pay(name="  Cables  ", name_ar=" كابلات "))
    c = service.get_category(cid)
    assert (c["code"], c["name"], c["name_ar"], c["parent_id"]) == ("CAT-014", "Cables", "كابلات", None)
    # فرعي تحت ABB، ونفس الاسم تحت أب تاني ماشي
    abb = cat(db, "ABB", "Panel")
    sub = service.save_category(None, pay(name="Cables", parent_id=abb["id"]))
    assert service.get_category(sub)["parent_name"] == "ABB"
    for bad, msg in ((dict(name="cables "), "بنفس الاسم"), (dict(name="abb", parent_id=panel["id"]), "تحت نفس الأب"),
                     (dict(name="  "), "اسم النوع"), (dict(name="X" * 121), "الاسم"),
                     (dict(inventory_account_id=acc_id(db, "4101")), "مش حساب مخزون"),
                     (dict(sales_account_id=acc_id(db, "41")), "رئيسي"),
                     (dict(cogs_account_id=acc_id(db, "5201")), "تكلفة مبيعات"),
                     (dict(parent_id=cat(db, "Workshop")["id"], is_active=True), None)):
        if msg is None:
            continue
        with pytest.raises(BusinessError, match=msg):
            service.save_category(None, pay(**{"name": "New", **bad}))
    # الحسابات الفعلية في الـ Service = اللي في القاعدة
    db.execute("UPDATE inv.item_categories SET sales_account_id = %s WHERE id = %s", [acc_id(db, "4102"),
                                                                                       panel["id"]])
    rows, defaults = service.lookup_categories(), service.default_accounts()
    for row in rows:
        info = service.resolve_accounts(row, row["parent_id"], rows, defaults)
        for kind in KINDS:
            assert info[kind]["account_id"] == effective(db, row["id"], kind), (row["name"], kind)
    info = service.resolve_accounts(service.get_category(sub), abb["id"], rows, defaults)
    assert (info["sales"]["source"], info["sales"]["source_name"], info["sales"]["code"]) == ("parent", "Panel", "4102")
    assert info["inventory"]["source"] == "default" and info["inventory"]["code"] == "1105"
    # النقل لأب تاني · مايبقاش تحت نفسه · 5 مستويات
    c = service.get_category(cid)
    with pytest.raises(BusinessError, match="تحت نفسه"):
        p = service.get_category(panel["id"])
        service.save_category(panel["id"], {**p, "parent_id": sub}, p["row_version"])
    service.save_category(cid, {**c, "parent_id": cat(db, "BMS")["id"]}, c["row_version"])
    deep = sub
    for n in (3, 4):
        deep = service.save_category(None, pay(name=f"L{n}", parent_id=deep))
    with pytest.raises(BusinessError, match="5 مستويات"):                              # L4 في المستوى الخامس
        service.save_category(None, pay(name="L5", parent_id=deep))
    choices = service.parent_choices()
    assert not any(r["id"] == deep for r in choices)                                    # المستوى الخامس مايبقاش أب
    assert next(r for r in choices if r["name"] == "L3")["path"] == "Panel / ABB / Cables / L3"
    assert not any(r["id"] in service.subtree_ids(panel["id"]) for r in service.parent_choices(panel["id"]))
    # الإيقاف والمسح
    p = service.get_category(panel["id"])
    with pytest.raises(BusinessError, match="تحته أنواع نشطة"):
        service.set_active(panel["id"], False, p["row_version"])
    with pytest.raises(BusinessError, match="تحته أنواع"):
        service.delete_category(panel["id"], p["row_version"])
    w = cat(db, "Workshop")
    service.set_active(w["id"], False, w["row_version"])
    with pytest.raises(BusinessError, match="موقوف"):
        service.save_category(None, pay(name="Tools", parent_id=w["id"]))
    codes = lambda rows: [r["code"] for r in rows]                                       # noqa: E731
    assert codes(service.list_categories(status="inactive")) == ["CAT-012"]
    for key in ("cat-001", "001", "bms", "أنظمة", "Panel/ABB"):
        assert codes(service.list_categories(key))[:1] in (["CAT-001"], ["CAT-006"]), key
    c = service.get_category(cid)
    service.delete_category(cid, c["row_version"])
    assert service.get_category(cid) is None
    audits = db.fetch_all("SELECT action FROM sec.audit_log WHERE entity = 'inv.item_categories' ORDER BY id")
    assert [a["action"] for a in audits][:2] == ["create", "create"] and audits[-1]["action"] == "delete"


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"inv.item_categories.view", "inv.item_categories.create", "inv.item_categories.save"})
    cid = service.save_category(None, pay())
    c = service.get_category(cid)
    for call in (lambda: service.save_category(cid, {**c, "name": "x"}, c["row_version"]),
                 lambda: service.set_active(cid, False, c["row_version"]),
                 lambda: service.delete_category(cid, c["row_version"])):
        with pytest.raises(PermissionDenied):
            call()
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'inv.item_categories.%%'")}
    assert codes == {f"inv.item_categories.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · نوعين جداد في نفس اللحظة بياخدوا كودين مختلفين ·
    نفس الاسم تحت نفس الأب من جهازين في نفس اللحظة ← واحد بس."""
    db = env["db"]
    s1, s2 = ItemCategoryService(db), ItemCategoryService(Database(db.settings))
    try:
        mcc = cat(db, "MCC")
        seen1, seen2 = s1.get_category(mcc["id"]), s2.get_category(mcc["id"])
        s2.save_category(mcc["id"], {**seen2, "notes": "من الجهاز التاني"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            s1.save_category(mcc["id"], {**seen1, "notes": "من الجهاز الأول"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.delete_category(mcc["id"], seen1["row_version"])
        results: dict[str, object] = {}

        def run(payload):
            def target():
                try:
                    results["id"] = s2.save_category(None, payload)
                except Exception as exc:  # noqa: BLE001
                    results["error"] = exc
            return threading.Thread(target=target)

        # نوعين جداد في نفس اللحظة: التاني بيستنى قفل الترقيم ← CAT-014 و CAT-015
        worker = run(pay(name="Lighting"))
        with db.atomic() as tx:
            seq = s1.repository.next_seq(tx)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            clean = s1._clean(pay(name="Cables"))
            clean["seq"] = seq
            s1.repository.insert_category(clean, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        got = {r["code"]: r["name"] for r in db.fetch_all("SELECT code, name FROM inv.item_categories WHERE seq > 13")}
        assert got == {"CAT-014": "Cables", "CAT-015": "Lighting"}
        # نفس الاسم تحت Panel من جهازين في نفس اللحظة ← واحد بس
        results.clear()
        panel = cat(db, "Panel")
        worker = run(pay(name="siemens", parent_id=panel["id"]))
        with db.atomic() as tx:
            seq = s1.repository.next_seq(tx)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            clean = s1._clean(pay(name="Siemens", parent_id=panel["id"]))
            clean["seq"] = seq
            s1.repository.insert_category(clean, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "بنفس الاسم" in str(results["error"])
        assert db.fetch_value("SELECT count(*) FROM inv.item_categories WHERE lower(name) = 'siemens'") == 1
    finally:
        s2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.item_categories_screen import ItemCategoriesScreen

    return ItemCategoriesScreen(ItemCategoryService(db))


def test_screen_tree(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    try:
        assert len(screen.visible_categories()) == 13 and screen.tree.topLevelItemCount() == 7
        panel_item = screen.items[cat(db, "Panel")["id"]]
        assert panel_item.childCount() == 6 and panel_item.text(1) == "CAT-005"
        assert "رئيسي 7" in screen.count_label.text().replace("⁦", "").replace("⁩", "")
        # اختيار ABB ← المسار والحسابات الفعلية
        abb = cat(db, "ABB", "Panel")
        screen._select_item(abb["id"])
        screen._tree_selection()
        assert screen.current_id == abb["id"] and screen.path_label.text() == "Panel / ABB"
        assert "Panel/ABB" in screen.sub_label.text()
        texts = screen.effective_accounts()
        assert "(الافتراضي)" in texts["inventory"] and "1105" in texts["inventory"]
        # Installation: المبيعات خاصة
        screen.load(cat(db, "Installation")["id"])
        assert "حساب خاص" in screen.effective_accounts()["sales"]
        # Panel ← حساب مبيعات خاص، و ABB بياخده «من Panel»
        screen.load(cat(db, "Panel")["id"])
        screen.edit_record()
        assert screen.mode == "edit"
        combo = screen.account_combos["sales"]
        combo.setCurrentIndex(combo.findData(acc_id(db, "4102")))
        assert "حساب خاص" in screen.effective_accounts()["sales"]
        screen.save()
        assert shown[-1] == "saved" and cat(db, "Panel")["sales_account_id"] == acc_id(db, "4102")
        screen.load(abb["id"])
        assert "من «Panel»" in screen.effective_accounts()["sales"]
        # «+ فرعي تحت المحدد» ← نوع تحت ABB
        screen.new_sub_category()
        assert screen.mode == "new" and screen.parent_combo.currentData() == abb["id"]
        assert screen.code_edit.text() == "CAT-014 (مقترح)" and screen.path_label.text() == "Panel / ABB / …"
        assert "من «Panel»" in screen.effective_accounts()["sales"]
        screen.name.setText(" Breakers ")
        screen.save()
        assert shown[-1] == "saved" and cat(db, "Breakers", "ABB") is not None
        assert screen.path_label.text() == "Panel / ABB / Breakers"
        assert screen.items[abb["id"]].childCount() == 1
        # نفس الاسم تحت نفس الأب ← رسالة والفورم بيفضل
        screen.new_sub_category()                                       # تحت Breakers
        screen.parent_combo.setCurrentIndex(screen.parent_combo.findData(abb["id"]))
        screen.name.setText("breakers")
        screen.save()
        assert "بنفس الاسم" in shown[-1] and screen.mode == "new"
        screen.cancel()
        # حساب مش من النوع الصح مايظهرش في الاختيارات أصلاً
        assert screen.account_combos["inventory"].findData(acc_id(db, "4101")) < 0
        # إيقاف Panel وتحته أنواع نشطة ← رسالة
        screen.load(cat(db, "Panel")["id"])
        screen.toggle_active()
        assert "تحته أنواع نشطة" in shown[-1] and cat(db, "Panel")["is_active"] is True
        # البحث بيجيب النوع وآباءه
        screen.search.setText("breakers")
        assert set(screen.visible_categories()) == {cat(db, "Panel")["id"], abb["id"], cat(db, "Breakers", "ABB")["id"]}
        assert "1 من 14" in screen.count_label.text().replace("⁦", "").replace("⁩", "")
        screen.search.clear()
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.load(cat(db, "MCC")["id"])
        screen.edit_record()
        db.execute("UPDATE inv.item_categories SET notes = 'من جهاز تاني' WHERE name = 'MCC'")
        screen.name_ar.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and cat(db, "MCC")["name_ar"] == "لوحات تحكم المحركات"
        screen.cancel()
        # إيقاف نوع من غير ولاد ← فلتر «الموقوفة»
        screen.load(cat(db, "Office")["id"])
        screen.toggle_active()
        assert cat(db, "Office")["is_active"] is False
        chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "inactive")
        chip.click()
        assert screen.visible_categories() == [cat(db, "Office")["id"]]
        # المسح
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        screen.load(cat(db, "Breakers", "ABB")["id"])
        screen.delete()
        assert cat(db, "Breakers", "ABB") is None and screen.current_id is None
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("inv").groups[0]
    assert [t.code for t in group.targets] == ["warehouses", "units_brands", "item_categories"]
    assert reg.find_target("inv", "item_categories").name_ar == "أنواع الأصناف"
    assert "item_categories" in SCREEN_FACTORIES
