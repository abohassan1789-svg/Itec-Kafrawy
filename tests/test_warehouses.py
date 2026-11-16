"""اختبارات «المخازن» (0026: القاعدة + الكود WH-001 + الأمين + الافتراضي الواحد + الوهمي + الـ Service +
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
from app.repositories.warehouse_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.warehouse_service import WarehouseService
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
    yield {"db": temp_db, "shown": shown, "service": WarehouseService(temp_db)}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.warehouses_screen import WarehousesScreen

    return WarehousesScreen(WarehouseService(db))


def emp(db, name, keeper=True, active=True):
    seq = int(db.fetch_value("SELECT coalesce(max(seq), 0) + 1 FROM party.employees"))
    return db.fetch_value("INSERT INTO party.employees (seq, name_ar, is_storekeeper, is_active) "
                          "VALUES (%s, %s, %s, %s) RETURNING id", [seq, name, keeper, active])


def wh(db, code):
    return db.fetch_one("SELECT * FROM inv.warehouses WHERE code = %s", [code])


def default_code(db):
    return db.fetch_value("SELECT string_agg(code, ',') FROM inv.warehouses WHERE is_default")


def payload(**extra):
    return {"name_ar": "مخزن الورشة", "name_en": None, "address": None, "phone": None, "notes": None,
            "keeper_id": None, "is_virtual": False, "is_default": False, "is_active": True, **extra}


# ---- القاعدة ---------------------------------------------------------------
def test_code_format():
    assert [format_code(n) for n in (1, 12, 999, 1000)] == ["WH-001", "WH-012", "WH-999", "WH-1000"]


def test_seed_and_database_rules(env):
    db = env["db"]
    rows = db.fetch_all("SELECT code, name_ar, is_virtual, is_default, keeper_id FROM inv.warehouses ORDER BY seq")
    assert rows == [{"code": "WH-001", "name_ar": "المخزن الرئيسي", "is_virtual": False, "is_default": True,
                     "keeper_id": None},
                    {"code": "WH-002", "name_ar": "المخزن الوهمي", "is_virtual": True, "is_default": False,
                     "keeper_id": None}]
    for sql, msg in (("UPDATE inv.warehouses SET seq = 77 WHERE code = 'WH-002'", "ثابت"),
                     ("UPDATE inv.warehouses SET is_default = true WHERE code = 'WH-002'", "الوهمي"),
                     ("UPDATE inv.warehouses SET is_active = false WHERE code = 'WH-001'", "الافتراضي"),
                     ("DELETE FROM inv.warehouses WHERE code = 'WH-001'", "الافتراضي")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(sql)
    with pytest.raises(psycopg.errors.UniqueViolation):                    # افتراضي واحد بس
        db.execute("INSERT INTO inv.warehouses (seq, name_ar, is_default) VALUES (3, 'تاني', true)")
    with pytest.raises(psycopg.errors.UniqueViolation):                    # الاسم مايتكررش
        db.execute("INSERT INTO inv.warehouses (seq, name_ar) VALUES (3, ' المخزن الرئيسي ')")
    db.execute("INSERT INTO inv.warehouses (seq, name_ar) VALUES (3, 'مخزن الورشة')")
    # الأمين: موظف نشط عليه دور «أمين مخزن»
    clerk, keeper, off = emp(db, "موظف عادي", keeper=False), emp(db, "أ/ هاني سعد"), emp(db, "موقوف", active=False)
    for who, msg in ((clerk, "مش عليه دور"), (off, "موقوف")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute("UPDATE inv.warehouses SET keeper_id = %s WHERE code = 'WH-003'", [who])
    db.execute("UPDATE inv.warehouses SET keeper_id = %s WHERE code IN ('WH-001', 'WH-003')", [keeper])
    for sql, msg in ((f"UPDATE party.employees SET is_storekeeper = false WHERE id = {keeper}", "قبل ما تشيل الدور"),
                     (f"UPDATE party.employees SET is_active = false WHERE id = {keeper}", "قبل ما توقفه"),
                     (f"DELETE FROM party.employees WHERE id = {keeper}", "غيّر الأمين")):
        with pytest.raises(psycopg.errors.RaiseException, match=msg):
            db.execute(sql)
    db.execute("UPDATE party.employees SET mobile = '010' WHERE id = %s", [keeper])      # تعديل عادي ماشي
    # المخزن الموقوف مابيمنعش شيل الدور (إلا عند المسح)
    db.execute("UPDATE inv.warehouses SET keeper_id = NULL WHERE code = 'WH-001'")
    db.execute("UPDATE inv.warehouses SET is_active = false WHERE code = 'WH-003'")
    db.execute("UPDATE party.employees SET is_storekeeper = false WHERE id = %s", [keeper])
    with pytest.raises(psycopg.errors.RaiseException, match="مش عليه دور"):          # تنشيطه بأمين مش أمين
        db.execute("UPDATE inv.warehouses SET is_active = true WHERE code = 'WH-003'")
    # row_version بيزيد
    v = wh(db, "WH-002")["row_version"]
    db.execute("UPDATE inv.warehouses SET notes = 'x' WHERE code = 'WH-002'")
    assert wh(db, "WH-002")["row_version"] == v + 1


def test_service_default_virtual_keeper_and_rules(env):
    db, service = env["db"], env["service"]
    d = service.new_defaults()
    assert (d["code"], d["is_virtual"], d["is_default"]) == ("WH-003", False, False)
    assert service.inventory_account() == {"code": "1105", "name_ar": "المخزون"}
    assert service.stats() == {"active": 2, "virtual": 1, "no_keeper": 1, "default_code": "WH-001",
                               "default_name": "المخزن الرئيسي"}
    keeper = emp(db, "أ/ هاني سعد")
    wid = service.save_warehouse(None, payload(name_en="Workshop", keeper_id=keeper, address="العبور",
                                               phone="0100", is_default=True))
    w = service.get_warehouse(wid)
    assert (w["code"], w["keeper_name"], w["is_default"], w["address"], w["phone"]) == \
           ("WH-003", "أ/ هاني سعد", True, "العبور", "0100")
    assert default_code(db) == "WH-003" and wh(db, "WH-001")["is_default"] is False   # الافتراضي اتنقل
    audits = db.fetch_all("SELECT action, record_label FROM sec.audit_log WHERE entity = 'inv.warehouses' "
                          "ORDER BY id")
    assert audits == [{"action": "update", "record_label": "WH-001 المخزن الرئيسي"},
                      {"action": "create", "record_label": "WH-003 مخزن الورشة"}]
    assert [k["name_ar"] for k in service.keepers()] == ["أ/ هاني سعد"]
    # التحقق
    clerk = emp(db, "موظف عادي", keeper=False)
    for bad, msg in ((dict(name_ar=""), "اسم المخزن"), (dict(name_ar="المخزن  الرئيسي"), "بنفس الاسم"),
                     (dict(keeper_id=clerk), "مش عليه دور"), (dict(is_virtual=True, is_default=True), "الوهمي"),
                     (dict(is_default=True, is_active=False), "لازم يبقى نشط"),
                     (dict(phone="1" * 31), "التليفون")):
        with pytest.raises(BusinessError, match=msg):
            service.save_warehouse(None, payload(**{"name_ar": "جديد", **bad}))
    assert default_code(db) == "WH-003"                                      # الغلط مانقلش الافتراضي
    # الأمين الواحد يمسك كذا مخزن · رجّع الافتراضي للرئيسي من «تعديل»
    main = service.get_warehouse(1)
    service.save_warehouse(1, {**main, "keeper_id": keeper, "is_default": True}, main["row_version"])
    assert default_code(db) == "WH-001" and service.stats()["no_keeper"] == 0
    # شيل علامة الافتراضي ← مفيش افتراضي (مسموح)
    main = service.get_warehouse(1)
    service.save_warehouse(1, {**main, "is_default": False}, main["row_version"])
    assert default_code(db) is None and service.stats()["default_code"] is None
    main = service.get_warehouse(1)
    service.save_warehouse(1, {**main, "is_default": True}, main["row_version"])
    # الافتراضي مايتوقفش ولا يتمسح
    main = service.get_warehouse(1)
    with pytest.raises(BusinessError, match="المخزن الافتراضي"):
        service.set_active(1, False, main["row_version"])
    with pytest.raises(BusinessError, match="المخزن الافتراضي"):
        service.save_warehouse(1, {**main, "is_active": False}, main["row_version"])
    with pytest.raises(BusinessError, match="المخزن الافتراضي"):
        service.delete_warehouse(1, main["row_version"])
    # الإيقاف · الأمين اللي اتشال دوره بيمنع إعادة التفعيل
    w = service.get_warehouse(wid)
    service.set_active(wid, False, w["row_version"])
    db.execute("UPDATE inv.warehouses SET keeper_id = NULL WHERE id = 1")
    db.execute("UPDATE party.employees SET is_storekeeper = false WHERE id = %s", [keeper])   # المخزن موقوف ← مسموح
    w = service.get_warehouse(wid)
    with pytest.raises(BusinessError, match="مابقاش أمين مخزن"):
        service.set_active(wid, True, w["row_version"])
    # الوهمي: ينفع من غير أمين، وتعديله لعادي مسموح (لسه مفيش حركة)
    v = service.get_warehouse(2)
    service.save_warehouse(2, {**v, "notes": "الصرف الوهمي"}, v["row_version"])
    # القائمة: البحث والفلاتر والصفحات والعدد
    codes = lambda rows: [r["code"] for r in rows]                                 # noqa: E731
    assert codes(service.list_warehouses()) == ["WH-001", "WH-002", "WH-003"]
    for key in ("WH-003", "wh-003", "003", "الورشة", "Workshop", "العبور", "0100", "هاني"):
        assert "WH-003" in codes(service.list_warehouses(key)), key
    assert codes(service.list_warehouses(status="inactive")) == ["WH-003"]
    assert codes(service.list_warehouses(page=2, page_size=2)) == ["WH-003"]
    assert service.count_warehouses() == {"total": 3, "active": 2, "inactive": 1}
    assert len(service.lookup_warehouses()) == 3
    # المسح
    service.delete_warehouse(wid, w["row_version"])
    assert wh(db, "WH-003") is None
    assert db.fetch_value("SELECT count(*) FROM sec.audit_log WHERE entity = 'inv.warehouses' "
                          "AND action = 'delete'") == 1


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"inv.warehouses.view", "inv.warehouses.create", "inv.warehouses.save"})
    wid = service.save_warehouse(None, payload())
    w = service.get_warehouse(wid)
    with pytest.raises(PermissionDenied):
        service.save_warehouse(wid, {**w, "name_ar": "x"}, w["row_version"])
    with pytest.raises(PermissionDenied):
        service.set_active(wid, False, w["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_warehouse(wid, w["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'inv.warehouses.%%'")}
    assert codes == {f"inv.warehouses.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · مخزنين جداد في نفس اللحظة بياخدوا كودين مختلفين ·
    جهازين بيعلّموا مخزنين مختلفين «افتراضي» في نفس اللحظة ← التاني بيستنى والآخر هو اللي بيفضل (واحد بس)."""
    db = env["db"]
    dev1, dev2 = WarehouseService(db), WarehouseService(Database(db.settings))
    try:
        seen1, seen2 = dev1.get_warehouse(1), dev2.get_warehouse(1)
        dev2.save_warehouse(1, {**seen2, "address": "الدور التاني"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_warehouse(1, {**seen1, "address": "الدور الأول"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(2, False, 99)
        assert dev1.get_warehouse(1)["address"] == "الدور التاني"
        # مخزنين جداد في نفس اللحظة: التاني بيستنى قفل الترقيم ← WH-003 و WH-004
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_warehouse(None, payload(name_ar="مخزن الجهاز التاني"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=second_device)
        with db.atomic() as tx:
            seq = dev1.repository.next_seq(tx)                     # الجهاز الأول ماسك قفل الترقيم
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            clean = dev1._clean(payload(name_ar="مخزن الجهاز الأول"), None)
            clean["seq"] = seq
            dev1.repository.insert_warehouse(clean, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        got = {r["code"]: r["name_ar"] for r in db.fetch_all("SELECT code, name_ar FROM inv.warehouses WHERE seq > 2")}
        assert got == {"WH-003": "مخزن الجهاز الأول", "WH-004": "مخزن الجهاز التاني"}
        # «افتراضي» من جهازين على مخزنين مختلفين: التاني بيستنى قفل الافتراضي وبعدين بياخدها من الأول
        w4 = dev2.get_warehouse(4)
        results.clear()

        def default_second():
            try:
                dev2.save_warehouse(4, {**w4, "is_default": True}, w4["row_version"])
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=default_second)
        with db.atomic() as tx:
            dev1._take_default(tx, 3)                              # الجهاز الأول ماسك قفل الافتراضي
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            w3 = dev1.repository.get_warehouse(3, ex=tx, for_update=True)
            dev1.repository.update_warehouse(3, {**w3, "is_default": True}, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        assert default_code(db) == "WH-004"                        # واحد بس — الأخير
        assert wh(db, "WH-003")["is_default"] is False and wh(db, "WH-001")["is_default"] is False
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_new_save_edit_toggle_delete(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    keeper = emp(db, "أ/ رامي عادل")
    screen = make_screen(db)
    try:
        assert screen.table.rowCount() == 2 and screen.current_record["code"] == "WH-001"
        texts = [screen.table.item(r, 1).text() for r in range(2)]
        assert texts == ["المخزن الرئيسي ⭐", "👻 المخزن الوهمي"]
        assert screen.kpi_values["active"].text() == "2  (منهم 1 وهمي)"
        assert screen.kpi_values["default"].text() == "⁦WH-001⁩ المخزن الرئيسي"
        assert screen.kpi_values["no_keeper"].text() == "1"
        assert "1105" in screen.account_view.text() and "1105" in screen.dim_label.text()
        assert screen.is_default.isChecked() and not screen.is_default.isEnabled()
        # الوهمي: المحاسبة «من غير قيد»
        screen.load(int(wh(db, "WH-002")["id"]))
        assert screen.is_virtual.isChecked() and "وهمي" in screen.account_view.text()
        # جديد ← افتراضي + أمين
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "WH-003 (مقترح)"
        assert screen.is_default.isEnabled() and screen.keeper.count() == 2
        screen.name_ar.setText("مخزن الورشة")
        screen.keeper.setCurrentIndex(screen.keeper.findData(keeper))
        screen.is_default.setChecked(True)
        screen.is_virtual.setChecked(True)                         # الوهمي بيشيل الافتراضي ويقفله
        assert not screen.is_default.isChecked() and not screen.is_default.isEnabled()
        screen.is_virtual.setChecked(False)
        assert screen.is_default.isEnabled()
        screen.is_default.setChecked(True)
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "WH-003"
        w = wh(db, "WH-003")
        assert (w["keeper_id"], w["is_default"]) == (keeper, True) and default_code(db) == "WH-003"
        assert screen.kpi_values["default"].text() == "⁦WH-003⁩ مخزن الورشة"
        assert screen.table.item(0, 1).text() == "المخزن الرئيسي"
        # الاسم المتكرر ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("المخزن الوهمي")
        screen.save()
        assert "بنفس الاسم" in shown[-1] and screen.mode == "new"
        screen.cancel()
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.load(int(w["id"]))
        screen.edit_record()
        assert screen.keeper.currentData() == keeper
        db.execute("UPDATE inv.warehouses SET notes = 'من جهاز تاني' WHERE code = 'WH-003'")
        screen.address.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and wh(db, "WH-003")["address"] is None
        screen.cancel()
        # الافتراضي مايتوقفش
        screen.toggle_active()
        assert "المخزن الافتراضي" in shown[-1] and wh(db, "WH-003")["is_active"] is True
        # إيقاف مخزن عادي ← يختفي من «النشطة»
        screen.load(int(wh(db, "WH-002")["id"]))
        screen.toggle_active()
        assert wh(db, "WH-002")["is_active"] is False
        active_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "active")
        active_chip.click()
        assert screen.table.rowCount() == 2 and screen.current_id is None
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        assert "موقوفة 1" in screen.count_label.text()
        screen.load(int(wh(db, "WH-002")["id"]))
        screen.delete()
        assert wh(db, "WH-002") is None and screen.current_id is None
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("inv").groups[0]
    assert group.title == "الأصناف + المخازن" and [t.code for t in group.targets][:1] == ["warehouses"]
    assert reg.find_target("inv", "warehouses").name_ar == "المخازن"
    assert "warehouses" in SCREEN_FACTORIES
