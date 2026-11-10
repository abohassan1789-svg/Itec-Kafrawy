"""اختبارات «مراكز التكلفة» (0013: القاعدة + المراكز المبدئية + فلتر الأب + التفريع + الـ Service + جهازين + الشاشة)."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.cost_center_service import CostCenterService
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
    service = CostCenterService(temp_db)
    ids = {r["code"]: int(r["id"]) for r in service.list_centers()}
    yield {"db": temp_db, "shown": shown, "service": service, "ids": ids}
    SESSION.clear()
    # الشاشات تتمسح هنا في الـ thread الرئيسي — لو الـ GC لمّها جوه thread اختبار تاني (جهازين) Qt بيقفل البرنامج.
    gc.collect()


def make_screen(db):
    from app.ui.screens.cost_centers_screen import CostCentersScreen

    return CostCentersScreen(CostCenterService(db))


def ins(db, code, parent, **extra):
    """INSERT مباشر في القاعدة (عشان نجرّب الـ trigger من غير الـ Service)."""
    cols = {"code": code, "name_ar": f"مركز {code}", "parent_id": parent, **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO acc.cost_centers ({names}) VALUES ({marks}) RETURNING id", list(cols.values()))


def row(db, code):
    return db.fetch_one("SELECT * FROM acc.cost_centers WHERE code = %s", [code])


def plain(text: str) -> str:
    return text.replace("⁦", "").replace("⁩", "")


# ---- المراكز المبدئية ----------------------------------------------------------
def test_seeded_centers_shape(env):
    db, ids = env["db"], env["ids"]
    assert len(ids) == 21
    roots = db.fetch_all("SELECT code, name_ar, center_type FROM acc.cost_centers WHERE parent_id IS NULL ORDER BY code")
    assert [(r["code"], r["name_ar"], r["center_type"]) for r in roots] == [
        ("1", "الإدارة العامة", "admin"), ("2", "الأنشطة الفنية", "ops"),
        ("3", "المبيعات والتسويق", "sales"), ("4", "المخازن والنقل", "serv")]
    bad = db.fetch_value("""
        SELECT count(*) FROM acc.cost_centers c JOIN acc.cost_centers p ON p.id = c.parent_id
        WHERE left(c.code, length(p.code)) <> p.code OR c.level <> p.level + 1 OR p.is_leaf""")
    assert bad == 0
    # الرئيسي كله تحته مراكز، والفرعي مفيش تحته
    assert db.fetch_value("""SELECT count(*) FROM acc.cost_centers a WHERE a.is_leaf <> NOT EXISTS
                             (SELECT 1 FROM acc.cost_centers c WHERE c.parent_id = a.id)""") == 0
    assert db.fetch_value("SELECT max(level) FROM acc.cost_centers") == 3
    assert row(db, "2101")["name_ar"] == "BMS — مشروعات" and row(db, "2201")["center_type"] == "ops"
    assert db.fetch_value("SELECT count(*) FROM acc.cost_centers WHERE NOT is_active OR row_version <> 1") == 0
    # فهرس على الأب (FK) وعلى الاسم
    indexes = {r["indexname"] for r in db.fetch_all(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'acc' AND tablename = 'cost_centers'")}
    assert {"cost_centers_parent_idx", "cost_centers_name_idx", "cost_centers_code_unique"} <= indexes


def test_parent_filter_brings_children(env):
    """فلتر الأب بيجيب الأبناء: acc.cost_center_subtree (Recursive CTE)."""
    service, ids = env["service"], env["ids"]
    codes = lambda center: sorted(c for c, i in ids.items() if i in service.subtree_ids(ids[center]))  # noqa: E731
    assert codes("2") == ["2", "21", "2101", "2102", "22", "2201", "2202", "23", "24"]
    assert codes("21") == ["21", "2101", "2102"]
    assert codes("2101") == ["2101"]
    assert codes("4") == ["4", "41", "42"]
    assert service.subtree_ids(999999) == []
    # بيشتغل في أي استعلام: «WHERE … IN (SELECT id FROM acc.cost_center_subtree(…))»
    assert env["db"].fetch_value(
        "SELECT count(*) FROM acc.cost_centers WHERE is_leaf AND id IN (SELECT id FROM acc.cost_center_subtree(%s))",
        [ids["2"]]) == 6


# ---- قواعد القاعدة (كل رسالة trigger) ----------------------------------------
def test_database_rules(env):
    db, ids = env["db"], env["ids"]
    raise_ = psycopg.errors.RaiseException
    cases = [
        (lambda: ins(db, "210101", ids["2101"]), "فرعي — مينفعش يبقى تحته"),
        (lambda: ins(db, "3101", ids["2"]), "لازم يبدأ بكود الأب «2»"),
        (lambda: ins(db, "2", ids["2"]), "لازم يبدأ بكود الأب"),
        (lambda: db.execute("UPDATE acc.cost_centers SET is_leaf = true WHERE code = '21'"), "مينفعش يتحوّل فرعي"),
        (lambda: db.execute("UPDATE acc.cost_centers SET code = '29' WHERE code = '21'"),
         "مينفعش تغيّر كوده ولا مكانه"),
        (lambda: db.execute("UPDATE acc.cost_centers SET is_active = false WHERE code = '22'"), "تحته مراكز نشطة"),
        (lambda: db.execute("DELETE FROM acc.cost_centers WHERE code = '22'"), "تحته مراكز — امسحهم الأول"),
    ]
    for action, text in cases:
        with pytest.raises(raise_, match=text):
            action()
    for sql, text in (("UPDATE acc.cost_centers SET center_type = 'x' WHERE code = '23'", "cost_centers_type_chk"),
                      ("UPDATE acc.cost_centers SET code = '2a' WHERE code = '23'", "cost_centers_code_format"),
                      ("UPDATE acc.cost_centers SET name_ar = ' ' WHERE code = '23'", "cost_centers_name_not_blank")):
        with pytest.raises(psycopg.errors.CheckViolation, match=text):
            db.execute(sql)
    with pytest.raises(psycopg.errors.UniqueViolation):
        ins(db, "2101", ids["21"])
    # أقصى 5 مستويات: المستوى الخامس آخر مستوى (لازم فرعي)
    l4 = ins(db, "210301", ids["21"], is_leaf=False)
    assert row(db, "210301")["level"] == 3
    l4 = ins(db, "21030101", l4, is_leaf=False)
    with pytest.raises(raise_, match="لازم يبقى فرعي"):
        ins(db, "2103010101", l4, is_leaf=False)
    l5 = ins(db, "2103010101", l4)
    assert db.fetch_value("SELECT level FROM acc.cost_centers WHERE id = %s", [l5]) == 5
    with pytest.raises(raise_, match="فرعي — مينفعش يبقى تحته"):
        ins(db, "210301010101", l5)
    # تحت أب موقوف: مينفعش تضيف
    db.execute("UPDATE acc.cost_centers SET is_active = false WHERE id = %s", [l5])
    db.execute("UPDATE acc.cost_centers SET is_active = false WHERE id = %s", [l4])
    with pytest.raises(raise_, match="موقوف"):
        ins(db, "2103010102", l4)
    with pytest.raises(raise_, match="موقوف"):
        db.execute("UPDATE acc.cost_centers SET is_active = true WHERE id = %s", [l5])
    # row_version بيزيد مع أي تعديل
    db.execute("UPDATE acc.cost_centers SET name_ar = 'التركيبات' WHERE code = '23'")
    assert row(db, "23")["row_version"] == 2


# ---- الـ Service ---------------------------------------------------------------
def test_service_defaults_save_and_rules(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    d = service.new_defaults(ids["22"])
    assert (d["code"], d["level"], d["center_type"], d["is_leaf"]) == ("2203", 3, "ops", True)
    assert service.new_defaults(ids["2"])["code"] == "25"
    root = service.new_defaults(None)
    assert (root["code"], root["level"], root["parent_id"], root["is_leaf"]) == ("5", 1, None, False)
    new_id = service.save_center(None, {**d, "name_ar": "MCC — صيانة لوحات", "name_en": "MCC Service",
                                        "manager_name": "  م. ياسر كمال ", "notes": ""})
    c = service.get_center(new_id)
    assert (c["code"], c["level"], c["parent_code"], c["manager_name"], c["notes"]) == (
        "2203", 3, "22", "م. ياسر كمال", None)
    assert service.new_defaults(ids["22"])["code"] == "2204"
    base = {**d, "name_ar": "مركز"}
    for data, text in (
            ({**base, "code": "22a"}, "أرقام بس"),
            ({**base, "name_ar": "  "}, "مطلوب"),
            ({**base, "parent_id": ids["2101"], "code": "210101"}, "فرعي — مينفعش"),
            ({**base, "code": "3101"}, "لازم يبدأ بكود الأب"),
            ({**base, "code": "2203"}, "متسجل قبل كده"),
            ({**base, "center_type": "boss"}, "إداري أو تشغيلي"),
            ({**base, "manager_name": "م" * 121}, "المسؤول")):
        with pytest.raises(BusinessError, match=text):
            service.save_center(None, data)
    # مركز رئيسي جديد (تبويب جديد)
    root_id = service.save_center(None, {**root, "name_ar": "المشروعات الخاصة", "center_type": "ops"})
    assert service.get_center(root_id)["level"] == 1
    # تعديل: الاسم والنوع · اللي تحته مراكز
    c = service.get_center(new_id)
    service.save_center(new_id, {**c, "name_ar": "MCC — صيانة", "center_type": "serv"}, c["row_version"])
    assert service.get_center(new_id)["center_type"] == "serv"
    bms = service.get_center(ids["21"])
    for change, text in (({"is_leaf": True}, "يتحوّل «فرعي»"), ({"code": "29"}, "مينفعش تغيّر كوده"),
                         ({"is_active": False}, "مراكز نشطة")):
        with pytest.raises(BusinessError, match=text):
            service.save_center(ids["21"], {**bms, **change})
    # فرعي ← رئيسي (مفيش تحته) وبعدين مركز تحته
    inst = service.get_center(ids["23"])
    service.save_center(ids["23"], {**inst, "is_leaf": False}, inst["row_version"])
    sub = service.new_defaults(ids["23"])
    assert sub["code"] == "2301"
    sub_id = service.save_center(None, {**sub, "name_ar": "تركيبات كابلات"})
    # المسح
    with pytest.raises(BusinessError, match="امسحهم الأول"):
        service.delete_center(ids["23"])
    service.delete_center(sub_id)
    assert service.get_center(sub_id) is None
    logs = db.fetch_all("SELECT action, record_label, details FROM sec.audit_log "
                        "WHERE entity = 'acc.cost_centers' ORDER BY id")
    assert [log["action"] for log in logs] == ["create", "create", "update", "update", "create", "delete"]
    assert logs[0]["record_label"] == "2203 MCC — صيانة لوحات"
    assert set(logs[2]["details"]["changed"]) >= {"name_ar", "center_type"}
    totals = service.summary(service.list_centers())
    assert totals[ids["2"]] == {"total": 10, "leaf": 6, "main": 4, "inactive": 0}
    assert totals[root_id]["total"] == 1


def test_split_center(env):
    """«+ فرعي تحت المحدد» على مركز فرعي: رئيسي + «— عام» + الجديد في transaction واحدة."""
    db, service, ids = env["db"], env["service"], env["ids"]
    plan = service.branch_plan(ids["2101"])
    assert plan["general"] == {"code": "210101", "name_ar": "BMS — مشروعات — عام"}
    assert plan["defaults"]["code"] == "210102" and plan["defaults"]["center_type"] == "ops"
    new_id = service.branch_center(ids["2101"], {**plan["defaults"], "name_ar": "BMS — مستشفيات"},
                                   plan["expected_version"])
    parent, general = row(db, "2101"), row(db, "210101")
    assert not parent["is_leaf"] and parent["row_version"] == 2
    assert general["is_leaf"] and general["parent_id"] == ids["2101"] and general["center_type"] == "ops"
    assert general["name_en"] == "BMS Projects — General"
    assert service.get_center(new_id)["code"] == "210102"
    logs = db.fetch_all("SELECT action, record_label, details FROM sec.audit_log "
                        "WHERE entity = 'acc.cost_centers' ORDER BY id")
    assert [(log["action"], log["record_label"]) for log in logs] == [
        ("update", "2101 BMS — مشروعات"), ("create", "210101 BMS — مشروعات — عام"),
        ("create", "210102 BMS — مستشفيات")]
    assert logs[0]["details"]["extra"]["split"]["general_code"] == "210101"
    # رئيسي / موقوف / المستوى الخامس ← رسالة
    for target, text in ((ids["21"], "رئيسي أصلاً"),):
        with pytest.raises(BusinessError, match=text):
            service.branch_plan(target)
    db.execute("UPDATE acc.cost_centers SET is_active = false WHERE code = '24'")
    with pytest.raises(BusinessError, match="موقوف"):
        service.branch_plan(ids["24"])
    with pytest.raises(psycopg.errors.RaiseException, match="رئيسي أصلاً"):
        db.fetch_value("SELECT acc.split_cost_center(%s)", [ids["21"]])
    # غلط في المركز الجديد ← ولا حاجة اتحفظت (المركز لسه فرعي ومفيش «عام»)
    plan = service.branch_plan(ids["2202"])
    with pytest.raises(BusinessError, match="مطلوب"):
        service.branch_center(ids["2202"], {**plan["defaults"], "name_ar": ""}, plan["expected_version"])
    assert row(db, "2202")["is_leaf"] and row(db, "220201") is None


def test_permissions(env):
    service, ids, db = env["service"], env["ids"], env["db"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.cost_centers.view", "acc.cost_centers.create", "acc.cost_centers.save"})
    service.save_center(None, {**service.new_defaults(ids["3"]), "name_ar": "مبيعات الصعيد"})
    c = service.get_center(ids["31"])
    with pytest.raises(PermissionDenied):
        service.save_center(ids["31"], {**c, "name_ar": "x"}, c["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_center(ids["33"])
    plan = service.branch_plan(ids["33"])
    with pytest.raises(PermissionDenied):                         # من غير «تعديل» مينفعش يحوّل المركز رئيسي
        service.branch_center(ids["33"], {**plan["defaults"], "name_ar": "مناقصات حكومية"}, 1)
    assert row(db, "33")["is_leaf"]
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.cost_centers.%%'")}
    assert codes == {f"acc.cost_centers.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين على نفس المركز: الأول يسبق ← التاني يترفض، ومفيش حاجة اتعملت مرتين."""
    db, ids = env["db"], env["ids"]
    dev1, dev2 = CostCenterService(db), CostCenterService(Database(db.settings))
    try:
        seen1, seen2 = dev1.get_center(ids["13"]), dev2.get_center(ids["13"])
        dev2.save_center(ids["13"], {**seen2, "name_ar": "شؤون العاملين"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_center(ids["13"], {**seen1, "name_ar": "الموارد البشرية والتدريب"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_center(ids["13"], seen1["row_version"])
        fresh = dev1.get_center(ids["13"])
        assert fresh["name_ar"] == "شؤون العاملين" and fresh["row_version"] == 2
        dev2.delete_center(ids["13"], fresh["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتمسح من جهاز تاني"):
            dev1.save_center(ids["13"], {**fresh, "name_ar": "x"}, fresh["row_version"])
        # اتنين بيضيفوا تحت نفس الأب بنفس الكود المقترح: واحد بس يتسجل
        d1, d2 = dev1.new_defaults(ids["4"]), dev2.new_defaults(ids["4"])
        assert d1["code"] == d2["code"] == "43"
        dev1.save_center(None, {**d1, "name_ar": "مخزن العاشر"})
        with pytest.raises(BusinessError, match="متسجل قبل كده"):
            dev2.save_center(None, {**d2, "name_ar": "مخزن أكتوبر"})
        # تفريع في نفس الوقت: واحد بس يفرّع
        p1, p2 = dev1.branch_plan(ids["2102"]), dev2.branch_plan(ids["2102"])
        dev1.branch_center(ids["2102"], {**p1["defaults"], "name_ar": "صيانة مولات"}, p1["expected_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev2.branch_center(ids["2102"], {**p2["defaults"], "name_ar": "صيانة مستشفيات"}, p2["expected_version"])
        assert db.fetch_value("SELECT count(*) FROM acc.cost_centers WHERE parent_id = %s", [ids["2102"]]) == 2
        # في نفس اللحظة: جهاز ماسك المركز (بيعدّل اسمه) ← التاني بيستنى القفل وبعدين يترفض
        p2 = dev2.branch_plan(ids["41"])
        result: dict = {}

        def second_device():
            try:
                dev2.branch_center(ids["41"], {**p2["defaults"], "name_ar": "رفوف المخزن"}, 1)
                result["ok"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = exc

        with db.atomic() as tx:
            tx.execute("SELECT 1 FROM acc.cost_centers WHERE id = %s FOR UPDATE", [ids["41"]])
            tx.execute("UPDATE acc.cost_centers SET name_ar = 'المخزن الرئيسي — العبور' WHERE id = %s", [ids["41"]])
            worker = threading.Thread(target=second_device)
            worker.start()
            time.sleep(0.7)
            assert worker.is_alive()                              # مستني القفل
        worker.join(10)
        assert isinstance(result.get("error"), ConcurrentEditError)
        fresh = row(db, "41")
        assert fresh["is_leaf"] and fresh["name_ar"] == "المخزن الرئيسي — العبور"
        assert db.fetch_value("SELECT count(*) FROM acc.cost_centers WHERE parent_id = %s", [ids["41"]]) == 0
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def tab_texts(screen) -> list[str]:
    return [plain(b.text()) for b in screen.root_tabs.values()]


def test_screen_tabs_tree_and_form(env):
    screen, ids = make_screen(env["db"]), env["ids"]
    assert tab_texts(screen) == ["1 الإدارة العامة (5)", "2 الأنشطة الفنية (9)", "3 المبيعات والتسويق (4)",
                                 "4 المخازن والنقل (3)"]
    assert screen.current_root == ids["1"] and screen.root_tabs[ids["1"]].isChecked()
    assert screen.code_edit.text() == "1" and screen.main_radio.isChecked()
    assert screen.type_combo.currentData() == "admin" and plain(screen.path_label.text()) == "1 الإدارة العامة"
    assert screen.tree.topLevelItemCount() == 1 and len(screen.items) == 5
    # تبويب «الأنشطة الفنية»
    screen.root_tabs[ids["2"]].click()
    assert screen.current_root == ids["2"] and len(screen.items) == 9 and screen.code_edit.text() == "2"
    item = screen.items[ids["2101"]]
    assert "2101" in item.text(0) and "BMS — مشروعات" in item.text(0) and "📄" in item.text(0)
    assert item.text(1) == "تشغيلي (فني)" and "📁" in screen.items[ids["21"]].text(0)
    assert "8 مركز (6 فرعي)" in plain(screen.kids_label.text())
    assert plain(screen.count_label.text()) == "9 مركز · 6 فرعي (بيتكتب في القيد) · 3 رئيسي · موقوف 0"
    assert screen.items[ids["21"]].isExpanded() and not screen.items[ids["2101"]].childCount()
    screen.load(ids["2201"])
    assert plain(screen.path_label.text()) == "2 الأنشطة الفنية ◂ 22 MCC — لوحات التحكم ◂ 2201 MCC — ورشة التصنيع"
    assert "مفيش — مركز فرعي" in screen.kids_label.text() and "4101" in plain(screen.accounts_label.text())
    screen.collapse_all()
    assert not any(it.isExpanded() for it in screen.items.values())
    screen.expand_all()
    assert all(it.isExpanded() for it in screen.items.values() if it.childCount())
    # الرجوع لتبويب بيفتح آخر مركز كان مفتوح فيه
    screen.root_tabs[ids["4"]].click()
    assert screen.code_edit.text() == "4"
    screen.root_tabs[ids["2"]].click()
    assert screen.code_edit.text() == "2201"
    assert screen.delete_button.isEnabled()                                        # فرعي ← الحذف شغال
    screen.load(ids["22"])
    assert not screen.delete_button.isEnabled()                                    # تحته مراكز


def test_screen_new_save_root_tab_and_delete(env):
    screen, ids, service = make_screen(env["db"]), env["ids"], env["service"]
    screen.root_tabs[ids["2"]].click()
    screen.load(ids["22"])                                       # رئيسي ← الجديد تحته
    screen.new_record()
    assert screen.mode == "new" and not screen.tree.isEnabled() and not screen.root_tabs[ids["3"]].isEnabled()
    assert screen.parent_combo.currentData() == ids["22"] and screen.code_edit.text() == "2203"
    assert screen.level_edit.text() == "3" and screen.type_combo.currentData() == "ops" and screen.leaf_radio.isChecked()
    screen.name_ar.setText("MCC — صيانة لوحات")
    screen.manager_edit.setText("م. ياسر كمال")
    screen.save()
    assert env["shown"] == ["saved"]
    new_id = screen.current_id
    assert service.get_center(new_id)["code"] == "2203" and new_id in screen.items
    assert screen.tree.currentItem() is screen.items[new_id] and tab_texts(screen)[1] == "2 الأنشطة الفنية (10)"
    # فرعي محدد ← الجديد جنبه (تحت أبوه)، وتغيير «تحت مركز» بيغيّر الكود والنوع
    screen.new_record()
    assert screen.parent_combo.currentData() == ids["22"] and screen.code_edit.text() == "2204"
    assert plain(screen.path_label.text()) == "2 الأنشطة الفنية ◂ 22 MCC — لوحات التحكم ◂ (المركز الجديد)"
    screen.parent_combo.setCurrentIndex(screen.parent_combo.findData(ids["4"]))
    assert screen.code_edit.text() == "43" and screen.type_combo.currentData() == "serv" and screen.level_edit.text() == "2"
    assert plain(screen.path_label.text()) == "4 المخازن والنقل ◂ (المركز الجديد)"
    screen.cancel()
    assert screen.mode == "view" and screen.current_id == new_id                   # «إلغاء» رجع للي كان مفتوح
    # مركز رئيسي جديد ← تبويب جديد
    screen.new_record()
    screen.parent_combo.setCurrentIndex(screen.parent_combo.findData(None))
    assert screen.code_edit.text() == "5" and screen.level_edit.text() == "1" and screen.main_radio.isChecked()
    screen.name_ar.setText("المشروعات الخاصة")
    screen.save()
    root_id = screen.current_id
    assert tab_texts(screen)[-1] == "5 المشروعات الخاصة (1)" and screen.current_root == root_id
    assert screen.root_tabs[root_id].isChecked() and len(screen.items) == 1
    # رسالة غلط: كود مش بيبدأ بكود الأب
    screen.load(ids["22"])
    screen.new_record()
    screen.code_edit.setText("999")
    screen.name_ar.setText("غلط")
    screen.save()
    assert "لازم يبدأ بكود الأب" in env["shown"][-1] and screen.mode == "new"
    screen.cancel()
    # الحذف ← بيفتح الأب · ومسح المركز الرئيسي ← التبويب بيختفي
    screen.load(new_id)
    assert screen.delete_button.isEnabled()
    screen.delete()
    assert service.get_center(new_id) is None and screen.current_id == ids["22"] and new_id not in screen.items
    screen.load(root_id)
    screen.delete()
    assert root_id not in screen.root_tabs and len(screen.root_tabs) == 4 and screen.current_id is not None


def test_screen_edit_locks(env):
    screen, ids, service = make_screen(env["db"]), env["ids"], env["service"]
    screen.load(ids["21"])                                       # تحته مراكز
    screen.edit_record()
    assert screen.mode == "edit" and screen.name_ar.isEnabled() and screen.type_combo.isEnabled()
    assert not screen.code_edit.isEnabled() and not screen.parent_combo.isEnabled() and not screen.leaf_radio.isEnabled()
    screen.manager_edit.setText("م. عمرو نبيل")
    screen.save()
    assert service.get_center(ids["21"])["manager_name"] == "م. عمرو نبيل"
    screen.load(ids["23"])                                       # فرعي من غير حاجة تحته ← كله مفتوح
    screen.edit_record()
    assert screen.code_edit.isEnabled() and screen.parent_combo.isEnabled() and screen.main_radio.isEnabled()
    screen.is_active.setChecked(False)
    screen.save()
    assert not service.get_center(ids["23"])["is_active"] and screen.items[ids["23"]].text(2) == "موقوف"
    assert not screen.branch_button.isEnabled() and "موقوف" in screen.branch_button.toolTip()


def test_screen_branch_button(env, monkeypatch):
    screen, ids, service, db = make_screen(env["db"]), env["ids"], env["service"], env["db"]
    questions: list[str] = []
    answer = {"value": True}
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, text: questions.append(text) or answer["value"])
    toolbar = screen.new_button.parentWidget().layout()
    assert toolbar.indexOf(screen.branch_button) == toolbar.indexOf(screen.new_button) + 1   # جنب «مركز جديد»
    assert screen.branch_button.text() == "+ فرعي تحت المحدد" and screen.new_button.text() == "مركز جديد"
    # رئيسي ← جديد تحته على طول (من غير سؤال)
    screen.load(ids["21"])
    screen.branch_record()
    assert screen.mode == "new" and screen.parent_combo.currentData() == ids["21"] and not questions
    assert screen.code_edit.text() == "2103" and not screen.split_note.isVisibleTo(screen)
    screen.cancel()
    # فرعي: السؤال ← «لا» = ولا حاجة
    screen.load(ids["2201"])
    answer["value"] = False
    screen.branch_record()
    assert screen.mode == "view" and "فرعي — عايز تحوّله رئيسي وتفرّع منه؟" in questions[-1]
    assert "⁦220101⁩ MCC — ورشة التصنيع — عام" in questions[-1] and "⁦220102⁩" in questions[-1]
    # «نعم» ← جديد تحته، والأب مقفول، و«إلغاء» = ولا حاجة اتغيّرت
    answer["value"] = True
    screen.branch_record()
    assert screen.mode == "new" and screen.code_edit.text() == "220102" and screen.level_edit.text() == "4"
    assert screen.parent_combo.currentData() == ids["2201"] and not screen.parent_combo.isEnabled()
    assert screen.split_note.isVisibleTo(screen) and "هيتحوّل رئيسي" in screen.split_note.text()
    screen.cancel()
    assert screen.current_id == ids["2201"] and row(db, "2201")["is_leaf"] and not screen.split_note.isVisibleTo(screen)
    # «نعم» + «حفظ»
    screen.branch_record()
    screen.name_ar.setText("MCC — لوحات تحكم محركات")
    screen.save()
    assert env["shown"][-1] == "saved" and screen.mode == "view"
    new_id = screen.current_id
    assert service.get_center(new_id)["code"] == "220102" and not service.get_center(ids["2201"])["is_leaf"]
    assert row(db, "220101")["id"] in screen.items and screen.items[new_id].parent() is screen.items[ids["2201"]]
    # جهاز تاني عدّل المركز بعد السؤال ← رسالة تعارض و«تحديث» بيفتح المركز بآخر نسخة
    screen.load(ids["2202"])
    screen.branch_record()
    screen.name_ar.setText("MCC — توريد خارجي")
    other = CostCenterService(Database(db.settings))
    try:
        c = other.get_center(ids["2202"])
        other.save_center(ids["2202"], {**c, "name_ar": "MCC — توريد واستيراد"}, c["row_version"])
    finally:
        other.db.close()
    monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: env["shown"].append(str(exc)) or True)
    screen.save()
    assert "اتعدّل من جهاز تاني" in env["shown"][-1] and screen.mode == "view"
    assert screen.current_id == ids["2202"] and screen.name_ar.text() == "MCC — توريد واستيراد"
    assert row(db, "2202")["is_leaf"] and row(db, "220201") is None


def test_screen_f1_and_search(env, monkeypatch):
    screen, ids = make_screen(env["db"]), env["ids"]
    from app.ui.dialogs import lookup_dialog

    class FakeLookup:
        def __init__(self, _title, _columns, rows, _fmt, _parent):
            assert len(rows) == 21                               # كل المراكز
            self.selected_id = ids["32"]

        def exec(self):
            return True

    monkeypatch.setattr(lookup_dialog, "LookupDialog", FakeLookup)
    screen.open_lookup()
    assert screen.current_root == ids["3"] and screen.root_tabs[ids["3"]].isChecked()
    assert screen.code_edit.text() == "32" and screen.tree.currentItem() is screen.items[ids["32"]]
    # البحث: اللي بيطابق + آباءه، وعدد كل تبويب
    screen.root_tabs[ids["2"]].click()
    screen.search.setText("MCC")
    assert set(screen.items) == {ids["2"], ids["22"], ids["2201"], ids["2202"]}
    assert tab_texts(screen) == ["1 الإدارة العامة (0)", "2 الأنشطة الفنية (3)", "3 المبيعات والتسويق (0)",
                                 "4 المخازن والنقل (0)"]
    assert "نتايج البحث" in screen.count_label.text()
    # فتح مركز مش ظاهر في البحث ← البحث بيتمسح
    screen.load(ids["2101"])
    assert screen.search.text() == "" and ids["2101"] in screen.items and len(screen.items) == 9


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("acc", "cost_centers")
    assert target.name_ar == "مراكز التكلفة" and target.icon == "🎯"
    group = reg.section("acc").groups[0]
    assert [t.code for t in group.targets] == ["chart_of_accounts", "default_accounts", "cost_centers"]
    assert "cost_centers" in SCREEN_FACTORIES
