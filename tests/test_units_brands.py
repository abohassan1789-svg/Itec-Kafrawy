"""اختبارات «الوحدات والماركات» (0027: القاعدة + الرمز + الافتراضية الواحدة + الماركة BRD-001 والاسم المتنضف +
الـ Services + الصلاحيات + جهازين + الشاشة بتبويبين)."""

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
from app.repositories.brand_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.brand_service import BrandService, clean_name
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.unit_service import UnitService, clean_code
from app.ui.common import messages

SEED_CODES = ["PCS", "SET", "BOX", "ROLL", "M", "CM", "KG", "M2", "L", "HR"]


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
    yield {"db": temp_db, "shown": shown, "units": UnitService(temp_db), "brands": BrandService(temp_db)}
    SESSION.clear()
    gc.collect()


def unit(db, code):
    return db.fetch_one("SELECT * FROM inv.units WHERE code = %s", [code])


def brand(db, code):
    return db.fetch_one("SELECT * FROM inv.item_brands WHERE code = %s", [code])


def default_unit(db):
    return db.fetch_value("SELECT string_agg(code, ',') FROM inv.units WHERE is_default")


def upay(**extra):
    return {"code": "TON", "name_ar": "طن", "name_en": "Ton", "category": "weight", "allows_fraction": True,
            "is_default": False, "is_active": True, "notes": None, **extra}


def bpay(**extra):
    return {"name": "SIEMENS", "name_ar": "سيمنز", "country": "ألمانيا", "notes": None, "is_active": True, **extra}


# ---- القاعدة ---------------------------------------------------------------
def test_helpers():
    assert [format_code(n) for n in (1, 12, 999, 1000)] == ["BRD-001", "BRD-012", "BRD-999", "BRD-1000"]
    assert clean_code(" k g ") == "KG" and clean_code(None) == ""
    assert clean_name(" Himel  ") == "Himel" and clean_name("CONTREL   ELETTRONICA") == "CONTREL ELETTRONICA"


def test_seed_and_database_rules(env):
    db = env["db"]
    rows = db.fetch_all("SELECT code, category, allows_fraction, is_default FROM inv.units ORDER BY id")
    assert [r["code"] for r in rows] == SEED_CODES
    assert default_unit(db) == "PCS"
    assert {r["code"] for r in rows if r["allows_fraction"]} == {"M", "CM", "KG", "M2", "L", "HR"}
    assert db.fetch_value("SELECT count(*) FROM inv.item_brands") == 0          # الماركات مع نقل الأكسس
    # الوحدات
    for sql, err in (("INSERT INTO inv.units (code, name_ar) VALUES ('kg2', 'س')", psycopg.errors.CheckViolation),
                     ("INSERT INTO inv.units (code, name_ar) VALUES ('A B', 'س')", psycopg.errors.CheckViolation),
                     ("INSERT INTO inv.units (code, name_ar, category) VALUES ('X', 'س', 'xx')",
                      psycopg.errors.CheckViolation),
                     ("INSERT INTO inv.units (code, name_ar) VALUES ('KG', 'كيلو تاني')", psycopg.errors.UniqueViolation),
                     ("INSERT INTO inv.units (code, name_ar) VALUES ('X', ' عدد ')", psycopg.errors.UniqueViolation),
                     ("INSERT INTO inv.units (code, name_ar, is_default) VALUES ('X', 'س', true)",
                      psycopg.errors.UniqueViolation)):
        with pytest.raises(err):
            db.execute(sql)
    for sql in ("UPDATE inv.units SET is_active = false WHERE code = 'PCS'", "DELETE FROM inv.units WHERE code = 'PCS'"):
        with pytest.raises(psycopg.errors.RaiseException, match="الافتراضية"):
            db.execute(sql)
    v = unit(db, "KG")["row_version"]
    db.execute("UPDATE inv.units SET notes = 'x' WHERE code = 'KG'")
    assert unit(db, "KG")["row_version"] == v + 1
    # الماركات: الاسم متنضف ومايتكررش من غير حروف كبيرة/صغيرة
    db.execute("INSERT INTO inv.item_brands (seq, name) VALUES (1, 'Himel')")
    assert brand(db, "BRD-001")["name"] == "Himel"
    for name, err in (("HIMEL", psycopg.errors.UniqueViolation), (" Himel", psycopg.errors.CheckViolation),
                      (" ABB", psycopg.errors.CheckViolation), ("A  B", psycopg.errors.CheckViolation),
                      ("", psycopg.errors.CheckViolation)):
        with pytest.raises(err):
            db.execute("INSERT INTO inv.item_brands (seq, name) VALUES (2, %s)", [name])
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE inv.item_brands SET seq = 9 WHERE seq = 1")
    v = brand(db, "BRD-001")["row_version"]
    db.execute("UPDATE inv.item_brands SET country = 'إسبانيا' WHERE seq = 1")
    assert brand(db, "BRD-001")["row_version"] == v + 1


def test_unit_service(env):
    db, service = env["db"], env["units"]
    assert service.new_defaults()["category"] == "count"
    uid = service.save_unit(None, upay(code=" ton ", is_default=True))
    u = service.get_unit(uid)
    assert (u["code"], u["category"], u["allows_fraction"], u["is_default"]) == ("TON", "weight", True, True)
    assert default_unit(db) == "TON"                                           # الافتراضية اتنقلت
    audits = db.fetch_all("SELECT action, record_label FROM sec.audit_log WHERE entity = 'inv.units' ORDER BY id")
    assert audits == [{"action": "update", "record_label": "PCS عدد"}, {"action": "create", "record_label": "TON طن"}]
    for bad, msg in ((dict(code=""), "رمز الوحدة مطلوب"), (dict(code="kg"), "موجود على وحدة"),
                     (dict(code="ABCDEFGHIJK"), "أطول"), (dict(name_ar=""), "اسم الوحدة"),
                     (dict(name_ar=" متر "), "بنفس الاسم"), (dict(category="xx"), "نوع الوحدة"),
                     (dict(is_default=True, is_active=False), "لازم تبقى نشطة")):
        with pytest.raises(BusinessError, match=msg):
            service.save_unit(None, upay(**{"code": "NEW", "name_ar": "جديدة", **bad}))
    assert default_unit(db) == "TON"
    # الافتراضية مابتتوقفش ولا تتمسح
    u = service.get_unit(uid)
    for call in (lambda: service.set_active(uid, False, u["row_version"]),
                 lambda: service.save_unit(uid, {**u, "is_active": False}, u["row_version"]),
                 lambda: service.delete_unit(uid, u["row_version"])):
        with pytest.raises(BusinessError, match="الوحدة الافتراضية"):
            call()
    # رجّع الافتراضية لـ «عدد» من «تعديل»، وتعديل الرمز مسموح
    pcs = unit(db, "PCS")
    service.save_unit(pcs["id"], {**pcs, "is_default": True}, pcs["row_version"])
    assert default_unit(db) == "PCS"
    u = service.get_unit(uid)
    service.save_unit(uid, {**u, "code": "T"}, u["row_version"])
    assert unit(db, "T")["name_ar"] == "طن"
    # الإيقاف · الفلاتر · البحث
    u = service.get_unit(uid)
    service.set_active(uid, False, u["row_version"])
    codes = lambda rows: [r["code"] for r in rows]                              # noqa: E731
    assert codes(service.list_units()) == ["PCS", "SET", "BOX", "ROLL", "M", "CM", "KG", "T", "M2", "L", "HR"]
    assert codes(service.list_units(status="inactive")) == ["T"]
    assert "T" not in codes(service.active_units())
    assert codes(service.list_units(category="length")) == ["M", "CM"]
    for key in ("kg", "كيلو", "Kilogram"):
        assert "KG" in codes(service.list_units(key)), key
    assert service.count_units() == {"total": 11, "active": 10, "inactive": 1}
    # المسح
    u = service.get_unit(uid)
    service.delete_unit(uid, u["row_version"])
    assert unit(db, "T") is None
    assert db.fetch_value("SELECT count(*) FROM sec.audit_log WHERE entity = 'inv.units' AND action = 'delete'") == 1


def test_brand_service(env):
    db, service = env["db"], env["brands"]
    assert service.new_defaults()["code"] == "BRD-001"
    bid = service.save_brand(None, bpay(name="  SIEMENS  ", country=" ألمانيا "))
    b = service.get_brand(bid)
    assert (b["code"], b["name"], b["country"]) == ("BRD-001", "SIEMENS", "ألمانيا")
    service.save_brand(None, bpay(name="Himel", name_ar=None, country=None))
    for name in ("siemens", " Siemens ", "SIEMENS "):
        with pytest.raises(BusinessError, match="الماركة دي موجودة"):
            service.save_brand(None, bpay(name=name))
    assert service.find_by_name("himel ")["code"] == "BRD-002" and service.find_by_name("ABB") is None
    for bad, msg in ((dict(name="  "), "اسم الماركة"), (dict(name="X" * 121), "الاسم"),
                     (dict(country="x" * 61), "بلد المنشأ")):
        with pytest.raises(BusinessError, match=msg):
            service.save_brand(None, bpay(**bad))
    # تعديل نفس الماركة بنفس الاسم بحروف تانية ماشي
    b = service.get_brand(bid)
    service.save_brand(bid, {**b, "name": "Siemens"}, b["row_version"])
    assert service.get_brand(bid)["name"] == "Siemens"
    # القائمة مترتبة بالاسم + البحث + الصفحات
    service.save_brand(None, bpay(name="ABB", name_ar="إيه بي بي", country="سويسرا"))
    codes = lambda rows: [r["code"] for r in rows]                              # noqa: E731
    assert codes(service.list_brands()) == ["BRD-003", "BRD-002", "BRD-001"]   # ABB · Himel · Siemens
    for key in ("BRD-003", "brd-003", "003", "abb", "إيه", "سويسرا"):
        assert codes(service.list_brands(key)) == ["BRD-003"], key
    assert codes(service.list_brands(page=2, page_size=2)) == ["BRD-001"]
    b = service.get_brand(bid)
    service.set_active(bid, False, b["row_version"])
    assert codes(service.list_brands(status="inactive")) == ["BRD-001"]
    assert service.count_brands() == {"total": 3, "active": 2, "inactive": 1}
    assert len(service.lookup_brands()) == 3
    b = service.get_brand(bid)
    service.delete_brand(bid, b["row_version"])
    assert brand(db, "BRD-001") is None
    assert service.new_defaults()["code"] == "BRD-004"                          # الكود مابيرجعش
    assert db.fetch_value("SELECT count(*) FROM sec.audit_log WHERE entity = 'inv.item_brands'") == 6


def test_permissions(env):
    db, units, brands = env["db"], env["units"], env["brands"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"inv.units_brands.view", "inv.units_brands.create", "inv.units_brands.save"})
    uid = units.save_unit(None, upay())
    bid = brands.save_brand(None, bpay())
    u, b = units.get_unit(uid), brands.get_brand(bid)
    for call in (lambda: units.save_unit(uid, {**u, "name_ar": "x"}, u["row_version"]),
                 lambda: units.set_active(uid, False, u["row_version"]),
                 lambda: units.delete_unit(uid, u["row_version"]),
                 lambda: brands.save_brand(bid, {**b, "name": "x"}, b["row_version"]),
                 lambda: brands.set_active(bid, False, b["row_version"]),
                 lambda: brands.delete_brand(bid, b["row_version"])):
        with pytest.raises(PermissionDenied):
            call()
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'inv.units_brands.%%'")}
    assert codes == {f"inv.units_brands.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · ماركتين جداد في نفس اللحظة بياخدوا كودين مختلفين ·
    «افتراضية» على وحدتين مختلفتين في نفس اللحظة ← التاني بيستنى والأخير هو اللي بيفضل (واحدة بس)."""
    db = env["db"]
    u1, u2 = UnitService(db), UnitService(Database(db.settings))
    b1, b2 = BrandService(db), BrandService(u2.db)
    try:
        kg = unit(db, "KG")
        seen1, seen2 = u1.get_unit(kg["id"]), u2.get_unit(kg["id"])
        u2.save_unit(kg["id"], {**seen2, "notes": "من الجهاز التاني"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            u1.save_unit(kg["id"], {**seen1, "notes": "من الجهاز الأول"}, seen1["row_version"])
        bid = b1.save_brand(None, bpay())
        s1, s2 = b1.get_brand(bid), b2.get_brand(bid)
        b2.save_brand(bid, {**s2, "country": "ألمانيا الغربية"}, s2["row_version"])
        with pytest.raises(ConcurrentEditError):
            b1.delete_brand(bid, s1["row_version"])
        # ماركتين جداد في نفس اللحظة: التاني بيستنى قفل الترقيم ← BRD-002 و BRD-003
        results: dict[str, object] = {}

        def second_brand():
            try:
                results["id"] = b2.save_brand(None, bpay(name="ABB"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=second_brand)
        with db.atomic() as tx:
            seq = b1.repository.next_seq(tx)                       # الجهاز الأول ماسك قفل الترقيم
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            clean = b1._clean(bpay(name="CHINT"))
            clean["seq"] = seq
            b1.repository.insert_brand(clean, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        got = {r["code"]: r["name"] for r in db.fetch_all("SELECT code, name FROM inv.item_brands WHERE seq > 1")}
        assert got == {"BRD-002": "CHINT", "BRD-003": "ABB"}
        # نفس اسم الماركة من جهازين في نفس اللحظة ← واحدة بس (القفل + unique index)
        results.clear()

        def same_name():
            try:
                b2.save_brand(None, bpay(name="kinco"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=same_name)
        with db.atomic() as tx:
            seq = b1.repository.next_seq(tx)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            clean = b1._clean(bpay(name="Kinco"))
            clean["seq"] = seq
            b1.repository.insert_brand(clean, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "موجودة" in str(results["error"])
        assert db.fetch_value("SELECT count(*) FROM inv.item_brands WHERE lower(name) = 'kinco'") == 1
        # «افتراضية» من جهازين على وحدتين مختلفتين
        m = u2.get_unit(unit(db, "M")["id"])
        results.clear()

        def default_second():
            try:
                u2.save_unit(m["id"], {**m, "is_default": True}, m["row_version"])
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=default_second)
        with db.atomic() as tx:
            box = unit(db, "BOX")
            u1._take_default(tx, box["id"])                         # الجهاز الأول ماسك قفل الافتراضية
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            locked = u1.repository.get_unit(box["id"], ex=tx, for_update=True)
            u1.repository.update_unit(box["id"], {**locked, "is_default": True}, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        assert default_unit(db) == "M"                              # واحدة بس — الأخيرة
    finally:
        u2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.units_brands_screen import UnitsBrandsScreen

    return UnitsBrandsScreen(UnitService(db), BrandService(db))


def test_screen_tabs_units_and_brands(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    try:
        units, brands = screen.units, screen.brands
        assert screen.tabs.count() == 2
        assert screen.tabs.tabText(0) == "📏 الوحدات  (10)" and screen.tabs.tabText(1) == "🏷️ الماركات  (0)"
        # الوحدات: أول سجل مفتوح والافتراضية عليها ⭐
        assert units.table.rowCount() == 10 and units.current_record["code"] == "PCS"
        assert units.table.item(0, 1).text() == "عدد ⭐" and units.table.item(0, 2).text() == "عدد"
        assert units.table.item(4, 3).text() == "بكسور" and units.table.item(0, 3).text() == "صحيح بس"
        assert units.is_default.isChecked() and not units.code_edit.isEnabled()
        # وحدة جديدة + افتراضية
        units.new_record()
        assert units.mode == "new" and units.category.currentData() == "count"
        units.code_edit.setText("ton")
        units.name_ar.setText("طن")
        units.category.setCurrentIndex(units.category.findData("weight"))
        units.allows_fraction.setChecked(True)
        units.is_default.setChecked(True)
        units.save()
        assert shown[-1] == "saved" and units.mode == "view" and units.code_edit.text() == "TON"
        assert default_unit(db) == "TON" and screen.tabs.tabText(0) == "📏 الوحدات  (11)"
        # الرمز المتكرر ← رسالة والفورم بيفضل
        units.new_record()
        units.code_edit.setText("kg")
        units.name_ar.setText("كيلو تاني")
        units.save()
        assert "موجود على وحدة" in shown[-1] and units.mode == "new"
        units.cancel()
        # الافتراضية مابتتوقفش
        units.load(int(unit(db, "TON")["id"]))
        units.toggle_active()
        assert "الوحدة الافتراضية" in shown[-1] and unit(db, "TON")["is_active"] is True
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        units.load(int(unit(db, "KG")["id"]))
        units.edit_record()
        db.execute("UPDATE inv.units SET notes = 'من جهاز تاني' WHERE code = 'KG'")
        units.name_en.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        units.save()
        assert conflicts and units.mode == "edit" and unit(db, "KG")["name_en"] == "Kilogram"
        units.cancel()
        # إيقاف ← يختفي من «النشطة»
        units.toggle_active()
        assert unit(db, "KG")["is_active"] is False
        chip = next(b for b in units.status_buttons.buttons() if b.property("status") == "inactive")
        chip.click()
        assert units.table.rowCount() == 1 and "موقوفة 1" in units.count_label.text()
        # الماركات: جديدة بالتنبيه وقت الكتابة
        assert brands.current_id is None and brands.table.rowCount() == 0
        brands.new_record()
        assert brands.code_edit.text() == "BRD-001 (مقترح)"
        brands.name.setText("  Himel ")
        brands.country.setText("إسبانيا")
        brands.save()
        assert shown[-1] == "saved" and brand(db, "BRD-001")["name"] == "Himel"
        assert screen.tabs.tabText(1) == "🏷️ الماركات  (1)" and brands.table.item(0, 2).text() == "—"
        brands.new_record()
        brands.name.textEdited.emit("HIMEL ")
        assert "موجودة" in brands.name_hint.text()
        brands.name.textEdited.emit("ABB")
        assert "مفيش" in brands.name_hint.text()
        brands.name.setText("HIMEL")
        brands.save()
        assert "الماركة دي موجودة" in shown[-1] and brands.mode == "new"
        brands.cancel()
        assert brands.name_hint.text() == ""
        brands.load(int(brand(db, "BRD-001")["id"]))
        brands.delete()
        assert brand(db, "BRD-001") is None and brands.current_id is None
        assert screen.tabs.tabText(1) == "🏷️ الماركات  (0)"
        # «خروج» من أي تبويب بيقفل الشاشة كلها
        screen.show()
        brands.exit_button.click()
        assert not screen.isVisible()
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("inv").groups[0]
    assert [t.code for t in group.targets][:2] == ["warehouses", "units_brands"]
    assert reg.find_target("inv", "units_brands").name_ar == "الوحدات والماركات"
    assert "units_brands" in SCREEN_FACTORIES
