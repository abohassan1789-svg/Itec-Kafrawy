"""اختبارات «الأصناف» (0029: الكود ITM-0001 · الاسم مايتكررش في نفس النوع (وفي نوع تاني تحذير) · الوحدة الأساسية
والوحدات التانية بمعامل · الخدمي · المخزن والضريبة · الحسابات من النوع · حماية الوحدة/الماركة/النوع/المخزن/الضريبة
من المسح · الـ Service · الصلاحيات · جهازين · الشاشة بالكروت والصفحات)."""

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
from app.repositories.item_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.item_service import ItemService, parse_amount
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
    asked: list[str] = []
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, text: asked.append(text) or True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "asked": asked, "service": ItemService(temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def cat(db, name):
    return one(db, "SELECT id FROM inv.item_categories WHERE name = %s", name)


def unit(db, code):
    return one(db, "SELECT id FROM inv.units WHERE code = %s", code)


def tax(db, code):
    return one(db, "SELECT id FROM acc.tax_codes WHERE code = %s", code)


def wh(db, code):
    return one(db, "SELECT id FROM inv.warehouses WHERE code = %s", code)


def item(db, code):
    return db.fetch_one("SELECT * FROM inv.items WHERE code = %s", [code])


def pay(db, **extra):
    return {"name": "Contactor 9A", "alt_name": "كونتاكتور 9 أمبير", "description": None,
            "category_id": cat(db, "MCC"), "brand_id": None, "unit_id": unit(db, "PCS"), "is_service": False,
            "default_warehouse_id": None, "tax_code_id": tax(db, "VAT14"), "sale_price": "850",
            "reorder_level": "10", "notes": None, "is_active": True,
            "units": [{"unit_id": unit(db, "BOX"), "factor": "12"}], **extra}


# ---- القاعدة ---------------------------------------------------------------
def test_helpers():
    assert [format_code(n) for n in (1, 12, 9999, 10000)] == ["ITM-0001", "ITM-0012", "ITM-9999", "ITM-10000"]
    assert parse_amount(" 1,250.50 ", "x") == Decimal("1250.50") and parse_amount("", "x") is None
    assert parse_amount("١٢", "x") == Decimal("12")
    for bad in ("-1", "abc", "1e400000"):
        with pytest.raises(BusinessError):
            parse_amount(bad, "السعر")


def test_database_rules(env):
    db = env["db"]
    assert one(db, "SELECT count(*) FROM inv.items") == 0                     # الأصناف مع نقل الأكسس
    ins = ("INSERT INTO inv.items (seq, name, category_id, unit_id, tax_code_id, is_service, default_warehouse_id, "
           "reorder_level) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id")
    mcc, bms, pcs, vat = cat(db, "MCC"), cat(db, "BMS"), unit(db, "PCS"), tax(db, "VAT14")
    first = one(db, ins, 1, "Relay 24V", mcc, pcs, vat, False, None, None)
    assert item(db, "ITM-0001")["name"] == "Relay 24V"
    # الاسم: مايتكررش في نفس النوع (حتى بحروف صغيرة) — وفي نوع تاني ماشي
    with pytest.raises(psycopg.errors.UniqueViolation):
        one(db, ins, 2, "relay 24v", mcc, pcs, vat, False, None, None)
    one(db, ins, 2, "Relay 24V", bms, pcs, vat, False, None, None)
    for bad, err in ((" Relay", psycopg.errors.CheckViolation), ("A  B", psycopg.errors.CheckViolation)):
        with pytest.raises(err):
            one(db, ins, 3, bad, mcc, pcs, vat, False, None, None)
    # الخدمي مالوش مخزن ولا حد طلب · المخزن الوهمي · ضريبة الخصم من المنبع
    with pytest.raises(psycopg.errors.CheckViolation):
        one(db, ins, 3, "Labor", mcc, unit(db, "HR"), vat, True, wh(db, "WH-001"), None)
    with pytest.raises(psycopg.errors.RaiseException, match="وهمي"):
        one(db, ins, 3, "X1", mcc, pcs, vat, False, wh(db, "WH-002"), None)
    with pytest.raises(psycopg.errors.RaiseException, match="خصم من المنبع"):
        one(db, ins, 3, "X2", mcc, pcs, tax(db, "WHT1"), False, None, None)
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE inv.items SET seq = 9 WHERE id = %s", [first])
    # الوحدات التانية: غير الأساسية · مش متكررة · المعامل > 0
    db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 12)", [first, unit(db, "BOX")])
    with pytest.raises(psycopg.errors.RaiseException, match="غير الوحدة الأساسية"):
        db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 1)", [first, pcs])
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 6)", [first, unit(db, "BOX")])
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 0)", [first, unit(db, "SET")])
    with pytest.raises(psycopg.errors.RaiseException, match="الوحدات التانية"):
        db.execute("UPDATE inv.items SET unit_id = %s WHERE id = %s", [unit(db, "BOX"), first])
    # اللي عليه أصناف مايتمسحش (رسالة عربي)
    db.execute("UPDATE inv.items SET default_warehouse_id = %s WHERE id = %s", [wh(db, "WH-001"), first])
    for sql, msg in (("DELETE FROM inv.units WHERE code = 'BOX'", "الوحدة «كرتونة» مستخدم في 1 صنف"),
                     ("DELETE FROM inv.item_categories WHERE name = 'MCC'", "نوع الأصناف «MCC»"),
                     ("DELETE FROM acc.tax_codes WHERE code = 'VAT14'", "كود الضريبة"),
                     ("UPDATE inv.warehouses SET is_virtual = true, is_default = false WHERE code = 'WH-001'",
                      "مايتحوّلش وهمي")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(sql)
    # موقوف مايتختارش
    db.execute("UPDATE inv.units SET is_active = false WHERE code = 'SET'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة"):
        one(db, ins, 3, "X3", mcc, unit(db, "SET"), vat, False, None, None)
    v = item(db, "ITM-0001")["row_version"]
    db.execute("UPDATE inv.items SET notes = 'x' WHERE id = %s", [first])
    assert item(db, "ITM-0001")["row_version"] == v + 1
    db.execute("DELETE FROM inv.items WHERE id = %s", [first])                  # الوحدات التانية بتتمسح معاه
    assert one(db, "SELECT count(*) FROM inv.item_units") == 0


def test_service(env):
    db, service = env["db"], env["service"]
    d = service.new_defaults()
    assert (d["code"], d["unit_id"], d["tax_code_id"], d["is_service"]) == ("ITM-0001", unit(db, "PCS"),
                                                                           tax(db, "VAT14"), False)
    db.execute("UPDATE inv.item_categories SET new_items_service = true WHERE name = 'Installation'")
    assert service.new_defaults(cat(db, "Installation"))["is_service"] is True    # من النوع
    iid = service.save_item(None, pay(db, name="  Contactor   9A ", sale_price="1,250.5"))
    it = service.get_item(iid)
    assert (it["code"], it["name"], it["sale_price"], it["reorder_level"]) == ("ITM-0001", "Contactor 9A",
                                                                              Decimal("1250.5"), Decimal("10"))
    assert [(u["unit_code"], u["factor"]) for u in it["units"]] == [("BOX", Decimal("12"))]
    # نفس الاسم: في نفس النوع ممنوع · في نوع تاني مسموح (والتحذير من same_name_elsewhere)
    with pytest.raises(BusinessError, match="في نفس النوع"):
        service.save_item(None, pay(db, name="contactor 9a"))
    assert service.same_name_elsewhere("CONTACTOR 9A", cat(db, "BMS"))[0]["code"] == "ITM-0001"
    assert service.same_name_elsewhere("Contactor 9A", cat(db, "MCC")) == []
    other = service.save_item(None, pay(db, name="Contactor 9A", category_id=cat(db, "BMS"), units=[]))
    assert service.get_item(other)["code"] == "ITM-0002"
    for bad, msg in ((dict(name=" "), "اسم الصنف"), (dict(unit_id=None), "الوحدة الأساسية"),
                     (dict(tax_code_id=None), "كود الضريبة"), (dict(sale_price="-5"), "بالسالب"),
                     (dict(reorder_level="x"), "رقم"),
                     (dict(units=[{"unit_id": unit(db, "PCS"), "factor": "2"}]), "غير الوحدة الأساسية"),
                     (dict(units=[{"unit_id": unit(db, "BOX"), "factor": "0"}]), "أكبر من صفر"),
                     (dict(units=[{"unit_id": unit(db, "BOX"), "factor": "1"}, {"unit_id": unit(db, "BOX"),
                                                                               "factor": "2"}]), "متكررة"),
                     (dict(default_warehouse_id=wh(db, "WH-002")), "مش موجود"),
                     (dict(tax_code_id=tax(db, "WHT1")), "مش موجود")):
        with pytest.raises(BusinessError, match=msg):
            service.save_item(None, pay(db, **{"name": "New item", **bad}))
    # الخدمي: المخزن وحد الطلب بيتشالوا
    svc = service.save_item(None, pay(db, name="Labor hour", category_id=cat(db, "Installation"), unit_id=unit(db, "HR"),
                                      is_service=True, default_warehouse_id=wh(db, "WH-001"), units=[]))
    s = service.get_item(svc)
    assert s["is_service"] and s["default_warehouse_id"] is None and s["reorder_level"] is None
    # التعديل: الوحدات التانية بس اتغيّرت ← الإصدار بيزيد · الوحدة الأساسية اتغيّرت
    it = service.get_item(iid)
    service.save_item(iid, {**pay(db), "units": [{"unit_id": unit(db, "BOX"), "factor": "10"},
                                                {"unit_id": unit(db, "SET"), "factor": "3"}]}, it["row_version"])
    it2 = service.get_item(iid)
    assert it2["row_version"] == it["row_version"] + 1
    assert sorted((u["unit_code"], u["factor"]) for u in it2["units"]) == [("BOX", Decimal("10")), ("SET", Decimal("3"))]
    # الحسابات من النوع (Installation مبيعاتها 4102) — ومن غير نوع ← الافتراضي
    accs = {r["kind"]: r["code"] for r in service.effective_accounts(cat(db, "Installation"))}
    assert accs == {"inventory": "1105", "sales": "4102", "cogs": "51", "sales_ret": "42"}
    assert {r["kind"]: r["code"] for r in service.effective_accounts(None)}["sales"] == "4101"
    # القائمة والفلاتر والأرقام
    codes = lambda rows: [r["code"] for r in rows]                              # noqa: E731
    assert codes(service.list_items()) == ["ITM-0001", "ITM-0002", "ITM-0003"]
    assert codes(service.list_items(kind="service")) == ["ITM-0003"]
    assert codes(service.list_items(kind="goods")) == ["ITM-0001", "ITM-0002"]
    for key in ("itm-0003", "3", "labor", "كونتاكتور"):
        assert codes(service.list_items(key))[:1] in (["ITM-0003"], ["ITM-0001"]), key
    assert codes(service.list_items(page=2, page_size=2)) == ["ITM-0003"]
    assert service.stats() == {"active": 3, "service": 1, "no_category": 0, "with_reorder": 2,
                               "below_reorder": 2}
    # الإيقاف · النوع الموقوف · المسح
    s = service.get_item(svc)
    service.set_active(svc, False, s["row_version"])
    assert service.count_items(status="inactive") == {"total": 1, "active": 0, "inactive": 1}
    db.execute("UPDATE inv.items SET category_id = NULL WHERE id = %s", [svc])
    db.execute("UPDATE inv.item_categories SET is_active = false WHERE name = 'Workshop'")
    with pytest.raises(BusinessError, match="موقوف"):
        service.save_item(None, pay(db, name="Hammer", category_id=cat(db, "Workshop")))
    it = service.get_item(iid)
    service.delete_item(iid, it["row_version"])
    assert item(db, "ITM-0001") is None
    assert service.new_defaults()["code"] == "ITM-0004"                          # الكود مابيرجعش
    actions = [r["action"] for r in db.fetch_all(
        "SELECT action FROM sec.audit_log WHERE entity = 'inv.items' ORDER BY id")]
    assert actions == ["create", "create", "create", "update", "update", "delete"]


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"inv.items.view", "inv.items.create", "inv.items.save"})
    iid = service.save_item(None, pay(db))
    it = service.get_item(iid)
    for call in (lambda: service.save_item(iid, {**pay(db), "name": "x"}, it["row_version"]),
                 lambda: service.set_active(iid, False, it["row_version"]),
                 lambda: service.delete_item(iid, it["row_version"])):
        with pytest.raises(PermissionDenied):
            call()
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'inv.items.%%'")}
    assert codes == {f"inv.items.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · صنفين جداد في نفس اللحظة بياخدوا كودين مختلفين ·
    نفس الاسم في نفس النوع من جهازين في نفس اللحظة ← واحد بس."""
    db = env["db"]
    s1, s2 = ItemService(db), ItemService(Database(db.settings))
    try:
        iid = s1.save_item(None, pay(db))
        seen1, seen2 = s1.get_item(iid), s2.get_item(iid)
        s2.save_item(iid, {**pay(db), "notes": "من الجهاز التاني"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            s1.save_item(iid, {**pay(db), "notes": "من الجهاز الأول"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.delete_item(iid, seen1["row_version"])
        results: dict[str, object] = {}

        def run(payload):
            def target():
                try:
                    results["id"] = s2.save_item(None, payload)
                except Exception as exc:  # noqa: BLE001
                    results["error"] = exc
            return threading.Thread(target=target)

        for name2, expect_error in (("Relay 24V", False), ("cable lug", True)):
            results.clear()
            worker = run(pay(db, name=name2, units=[]))
            with db.atomic() as tx:
                seq = s1.repository.next_seq(tx)                   # الجهاز الأول ماسك قفل الترقيم
                worker.start()
                worker.join(0.7)
                assert worker.is_alive()
                clean = s1._clean(pay(db, name="Cable lug", units=[]))
                clean["seq"] = seq
                s1.repository.insert_item(clean, ex=tx)
            worker.join(10)
            if expect_error:
                assert isinstance(results.get("error"), BusinessError) and "في نفس النوع" in str(results["error"])
            else:
                assert "error" not in results, results
            if name2 == "Relay 24V":                               # اتمسح عشان اللفة التانية تتعمل بنفس الاسم
                db.execute("DELETE FROM inv.items WHERE lower(name) = 'cable lug'")
        assert one(db, "SELECT count(*) FROM inv.items WHERE lower(name) = 'cable lug'") == 1
        assert one(db, "SELECT count(*) FROM inv.items WHERE name = 'Relay 24V'") == 1
    finally:
        s2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.items_screen import ItemsScreen

    return ItemsScreen(ItemService(db))


def test_screen(env, monkeypatch):
    db, shown, asked, service = env["db"], env["shown"], env["asked"], env["service"]
    service.save_item(None, pay(db))
    screen = make_screen(db)
    try:
        assert screen.table.rowCount() == 1 and screen.current_record["code"] == "ITM-0001"
        assert screen.kpi_values["active"].text() == "1" and screen.kpi_values["no_category"].text() == "0"
        assert screen.units_table.rowCount() == 1 and screen.units_table.cellWidget(0, 1).text() == "12"
        assert "4101" in screen.account_views["sales"].text() and "من النوع" in screen.account_views["sales"].text()
        assert screen.sale_price.text() == "850" and not screen.units_table.cellWidget(0, 0).isEnabled()
        # صنف جديد خدمي من نوع Installation (المبيعات 4102)
        db.execute("UPDATE inv.item_categories SET new_items_service = true WHERE name = 'Installation'")
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "ITM-0002 (مقترح)"
        assert screen.unit.currentData() == unit(db, "PCS") and screen.tax.currentData() == tax(db, "VAT14")
        screen.category.setCurrentIndex(screen.category.findData(cat(db, "Installation")))
        assert screen.is_service.isChecked() and not screen.warehouse.isEnabled()
        assert "4102" in screen.account_views["sales"].text()
        screen.name.setText("Installation hour")
        screen.unit.setCurrentIndex(screen.unit.findData(unit(db, "HR")))
        screen.save()
        assert shown[-1] == "saved" and item(db, "ITM-0002")["is_service"] is True
        # نفس الاسم في نوع تاني ← تحذير (سؤال) وبيتحفظ
        screen.new_record()
        screen.name.setText("Contactor 9A")
        screen.name.textEdited.emit("Contactor 9A")
        assert "نوع تاني" in screen.name_hint.text()
        screen.category.setCurrentIndex(screen.category.findData(cat(db, "MCC")))
        assert "نفس النوع" in screen.name_hint.text()
        screen.save()
        assert "في نفس النوع" in shown[-1] and screen.mode == "new"
        screen.category.setCurrentIndex(screen.category.findData(cat(db, "BMS")))
        screen._add_unit_row()
        combo, edit = screen.units_table.cellWidget(0, 0), screen.units_table.cellWidget(0, 1)
        combo.setCurrentIndex(combo.findData(unit(db, "BOX")))
        edit.setText("20")
        before_asked = len(asked)
        screen.save()
        assert len(asked) == before_asked + 1 and "نوع تاني" in asked[-1]
        assert shown[-1] == "saved" and item(db, "ITM-0003")["category_id"] == cat(db, "BMS")
        assert one(db, "SELECT factor FROM inv.item_units WHERE item_id = %s", item(db, "ITM-0003")["id"]) == 20
        # فلتر «خدمي» والكروت
        chip = next(b for b in screen.kind_buttons.buttons() if b.property("value") == "service")
        chip.click()
        assert [r["code"] for r in screen.table.rows] == ["ITM-0002"]
        assert screen.kpi_values["service"].text() == "1"
        all_chip = next(b for b in screen.kind_buttons.buttons() if b.property("value") == "all")
        all_chip.click()
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.load(item(db, "ITM-0001")["id"])
        screen.table.select_id(item(db, "ITM-0001")["id"])
        screen.edit_record()
        db.execute("UPDATE inv.items SET notes = 'من جهاز تاني' WHERE code = 'ITM-0001'")
        screen.description.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and item(db, "ITM-0001")["description"] is None
        screen.cancel()
        # إيقاف ← فلتر الموقوفة
        screen.toggle_active()
        assert item(db, "ITM-0001")["is_active"] is False
        chip = next(b for b in screen.status_buttons.buttons() if b.property("value") == "inactive")
        chip.click()
        assert [r["code"] for r in screen.table.rows] == ["ITM-0001"]
        # المسح
        screen.load(item(db, "ITM-0001")["id"])
        screen.delete()
        assert item(db, "ITM-0001") is None and screen.current_id is None
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("inv").groups[0]
    assert [t.code for t in group.targets] == ["warehouses", "units_brands", "item_categories", "items",
                                                       "opening_stock"]
    assert reg.find_target("inv", "items").name_ar == "الأصناف"
    assert "items" in SCREEN_FACTORIES
