"""اختبارات «أنواع المصروفات» (0025: الجدول بالتفريع + الحسابات الخمسة + الأنواع العشرة + حماية حساب النوع +
التفريع بينزّل الأنواع على «— عام» + الـ Service + الصلاحيات + جهازين + الشاشة «الأنواع في دليل الحسابات»)."""

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
from app.repositories.expense_type_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.expense_type_service import ExpenseTypeService
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
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "service": ExpenseTypeService(temp_db)}
    SESSION.clear()
    gc.collect()


def acc(db, code):
    return db.fetch_one("SELECT * FROM acc.accounts WHERE code = %s", [code])


def et(db, code):
    return db.fetch_one("SELECT * FROM exp.expense_types WHERE code = %s", [code])


def payload(**extra):
    return {"name_ar": "أدوات مكتبية", "name_en": "Stationery", "parent_id": None, "notes": None,
            "is_active": True, "account_mode": "pick", **extra}


def insert_type(db, seq, name, account_code, parent_id=None, active=True):
    return db.fetch_value("INSERT INTO exp.expense_types (seq, name_ar, account_id, parent_id, is_active) "
                          "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                          [seq, name, acc(db, account_code)["id"], parent_id, active])


# ---- الأدوات ---------------------------------------------------------------
def test_codes():
    assert [format_code(n) for n in (1, 12, 999, 1000)] == ["EXT-001", "EXT-012", "EXT-999", "EXT-1000"]


# ---- القاعدة ---------------------------------------------------------------
def test_seed_and_database_rules(env):
    db = env["db"]
    rows = db.fetch_all("SELECT t.code, t.name_ar, a.code AS acc, t.owns_account, t.parent_id FROM exp.expense_types t "
                        "JOIN acc.accounts a ON a.id = t.account_id ORDER BY t.seq")
    assert [(r["code"], r["name_ar"], r["acc"]) for r in rows] == [
        ("EXT-001", "كهرباء", "5203"), ("EXT-002", "إيجارات", "5202"), ("EXT-003", "مصاريف عامة", "5204"),
        ("EXT-004", "مصاريف نقل بضاعة", "5301"), ("EXT-005", "مصاريف تليفونات", "5207"),
        ("EXT-006", "إكراميات", "5209"), ("EXT-007", "مياه", "5203"), ("EXT-008", "سولار", "5304"),
        ("EXT-009", "صيانة سيارات", "5305"), ("EXT-010", "مواصلات", "5208")]
    assert not any(r["owns_account"] or r["parent_id"] for r in rows)
    new = {a["code"]: (a["name_ar"], a["level"], a["account_type"], a["is_leaf"], a["is_system"])
           for a in db.fetch_all("SELECT a.*, p.code AS pc FROM acc.accounts a JOIN acc.accounts p ON p.id = a.parent_id "
                                 "WHERE a.code IN ('5207','5208','5209','5304','5305')")}
    assert new == {"5207": ("تليفونات وإنترنت", 3, "expense", True, False),
                   "5208": ("انتقالات ومواصلات", 3, "expense", True, False),
                   "5209": ("إكراميات وضيافة", 3, "expense", True, False),
                   "5304": ("وقود وسولار", 3, "expense", True, False),
                   "5305": ("صيانة سيارات", 3, "expense", True, False)}
    # الحساب: فرعي · مصروفات · من غير دور · مش تكلفة مبيعات
    for code, msg in (("52", "رئيسي"), ("110101", "مش من المصروفات"), ("51", "تكلفة مبيعات"),
                      ("5401", "دور نظامي")):
        with pytest.raises(raise_, match=msg):
            insert_type(db, 50, "نوع غلط", code)
    # كذا نوع على نفس الحساب مسموح · الاسم مايتكررش
    tid = insert_type(db, 11, "غاز", "5203")
    with pytest.raises(psycopg.errors.UniqueViolation):
        insert_type(db, 12, " غاز ", "5204")
    # التفريع: الأب نشط · مش تحت نفسه · لحد 5 مستويات · الأب مايتوقفش وتحته نشط · مايتمسحش وتحته أنواع
    kid = insert_type(db, 12, "غاز المطبخ", "5203", parent_id=tid)
    with pytest.raises(raise_, match="تحته أنواع نشطة"):
        db.execute("UPDATE exp.expense_types SET is_active = false WHERE id = %s", [tid])
    with pytest.raises(raise_, match="تحت نفسه"):
        db.execute("UPDATE exp.expense_types SET parent_id = %s WHERE id = %s", [kid, tid])
    with pytest.raises(raise_, match="تحته أنواع"):
        db.execute("DELETE FROM exp.expense_types WHERE id = %s", [tid])
    chain = kid
    for n in range(3):                                              # المستوى 3 و4 و5
        chain = insert_type(db, 13 + n, f"مستوى {n + 3}", "5203", parent_id=chain)
    with pytest.raises(raise_, match="5 مستويات"):
        insert_type(db, 20, "مستوى 6", "5203", parent_id=chain)
    db.execute("UPDATE exp.expense_types SET is_active = false WHERE id = %s", [chain])
    with pytest.raises(raise_, match="موقوف"):
        insert_type(db, 21, "تحت موقوف", "5203", parent_id=chain)
    # الكود والحساب ثابتين
    with pytest.raises(raise_, match="ثابت"):
        db.execute("UPDATE exp.expense_types SET seq = 99 WHERE id = %s", [tid])
    with pytest.raises(raise_, match="ثابت"):
        db.execute("UPDATE exp.expense_types SET account_id = %s WHERE id = %s", [acc(db, "5204")["id"], tid])
    # حماية حساب النوع من شجرة الحسابات
    a5207 = acc(db, "5207")["id"]
    with pytest.raises(raise_, match="امسح النوع"):
        db.execute("DELETE FROM acc.accounts WHERE id = %s", [a5207])
    with pytest.raises(raise_, match="وقّفهم الأول"):
        db.execute("UPDATE acc.accounts SET is_active = false WHERE id = %s", [a5207])
    with pytest.raises(raise_, match="فرعي تحت المحدد"):
        db.execute("UPDATE acc.accounts SET is_leaf = false WHERE id = %s", [a5207])
    with pytest.raises(raise_, match="دور نظامي"):
        db.execute("UPDATE acc.accounts SET role_code = 'rounding' WHERE id = %s", [a5207])
    # التفريع من الشجرة: الأنواع بتنزل على «— عام»
    general = db.fetch_value("SELECT acc.split_account(%s)", [acc(db, "5203")["id"]])
    assert acc(db, "520301")["id"] == general and acc(db, "520301")["name_ar"] == "كهرباء ومياه — عام"
    assert db.fetch_value("SELECT count(*) FROM exp.expense_types WHERE account_id = %s", [general]) == 7
    assert db.fetch_value("SELECT count(*) FROM exp.expense_types WHERE account_id = %s",
                          [acc(db, "5203")["id"]]) == 0
    # عمق النوع وطول الفرع
    assert db.fetch_value("SELECT exp.expense_type_depth(%s)", [chain]) == 5
    assert db.fetch_value("SELECT exp.expense_type_height(%s)", [tid]) == 4
    assert db.fetch_value("SELECT count(*) FROM exp.expense_type_subtree(%s)", [tid]) == 5


# ---- الـ Service -----------------------------------------------------------
def test_service_auto_pick_tree_and_delete(env):
    db, service = env["db"], env["service"]
    d = service.new_defaults()
    p52 = acc(db, "52")["id"]
    assert (d["code"], d["account_mode"], d["account_parent_id"]) == ("EXT-011", "auto", p52)
    assert service.suggested_account(p52)["code"] == "5210"
    assert [p["code"] for p in service.account_parents()] == ["52", "53", "54"]
    usable = [a["code"] for a in service.usable_accounts()]
    assert "5203" in usable and "5401" not in usable and "51" not in usable and "5303" not in usable
    # يتعمل حساب جديد تحت 52
    tid = service.save_type(None, payload(account_mode="auto", account_parent_id=p52))
    t = service.get_type(tid)
    assert (t["code"], t["account_code"], t["account_name"], t["owns_account"]) == ("EXT-011", "5210", "أدوات مكتبية",
                                                                                    True)
    assert acc(db, "5210")["account_type"] == "expense" and acc(db, "5210")["role_code"] is None
    # حساب موجود + نوع فرعي (الافتراضي = حساب الأب)
    sd = service.new_defaults(tid)
    assert (sd["parent_id"], sd["account_mode"], sd["account_id"]) == (tid, "pick", t["account_id"])
    kid = service.save_type(None, payload(name_ar="أحبار طابعات", parent_id=tid, account_id=t["account_id"]))
    k = service.get_type(kid)
    assert (k["parent_code"], k["account_code"], k["owns_account"]) == ("EXT-011", "5210", False)
    assert service.get_type(tid)["children_count"] == 1
    assert set(service.subtree_ids(tid)) == {tid, kid}
    assert kid not in [r["id"] for r in service.parent_choices(tid)] and tid not in [
        r["id"] for r in service.parent_choices(tid)]
    for bad, msg in ((dict(name_ar=" "), "اسم النوع"),
                     (dict(name_ar="كهرباء"), "بنفس الاسم"), (dict(account_id=None), "اختار حساب"),
                     (dict(account_id=acc(db, "52")["id"]), "رئيسي"),
                     (dict(account_id=acc(db, "5401")["id"]), "نظامي"),
                     (dict(account_mode="auto", account_parent_id=acc(db, "5203")["id"]), "مش حساب رئيسي")):
        with pytest.raises(BusinessError, match=msg):
            service.save_type(None, payload(**{"name_ar": "نوع للاختبار", "account_id": acc(db, "5204")["id"], **bad}))
    # تغيير اسم النوع ← اسم حسابه اللي اتعمل معاه بيتغيّر (ومايتغيّرش لو عليه نوع تاني)
    t = service.get_type(tid)
    service.save_type(tid, {**t, "name_ar": "أدوات مكتبية وطباعة"}, t["row_version"])
    assert acc(db, "5210")["name_ar"] == "أدوات مكتبية"                 # عليه «أحبار طابعات» كمان
    # الأب مايتوقفش وتحته نشط · الفرعي مايتفعّلش والأب موقوف · مايتمسحش وتحته أنواع
    t = service.get_type(tid)
    with pytest.raises(BusinessError, match="تحته أنواع نشطة"):
        service.set_active(tid, False, t["row_version"])
    with pytest.raises(BusinessError, match="تحته أنواع نشطة"):
        service.save_type(tid, {**t, "is_active": False}, t["row_version"])
    with pytest.raises(BusinessError, match="تحته أنواع"):
        service.delete_type(tid, t["row_version"])
    k = service.get_type(kid)
    service.set_active(kid, False, k["row_version"])
    service.set_active(tid, False, t["row_version"])
    k = service.get_type(kid)
    with pytest.raises(BusinessError, match="الأب .* موقوف"):
        service.set_active(kid, True, k["row_version"])
    with pytest.raises(BusinessError, match="تحت نفسه"):
        t = service.get_type(tid)
        service.save_type(tid, {**t, "parent_id": kid}, t["row_version"])
    # المسح: الفرعي الأول ← بعدين الأب ← حسابه اللي اتعمل معاه بيتمسح (مفيش نوع تاني عليه)
    service.delete_type(kid, k["row_version"])
    t = service.get_type(tid)
    service.delete_type(tid, t["row_version"])
    assert et(db, "EXT-011") is None and acc(db, "5210") is None
    audits = [r["entity"] for r in db.fetch_all("SELECT entity FROM sec.audit_log WHERE entity IN "
                                                "('exp.expense_types', 'acc.accounts') ORDER BY id")]
    assert audits[0] == "acc.accounts" and audits[-1] == "acc.accounts" and "exp.expense_types" in audits
    # نوع على حساب موجود مايمسحش الحساب
    sid = service.save_type(None, payload(name_ar="غاز", account_id=acc(db, "5203")["id"]))
    s = service.get_type(sid)
    service.delete_type(sid, s["row_version"])
    assert acc(db, "5203") is not None
    # البحث
    assert [r["code"] for r in service.list_types("سولار")] == ["EXT-008"]
    assert [r["code"] for r in service.list_types("5203")] == ["EXT-001", "EXT-007"]
    assert [r["code"] for r in service.list_types("008")] == ["EXT-008"]


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.expense_types.view", "acc.expense_types.create", "acc.expense_types.save"})
    tid = service.save_type(None, payload(account_id=acc(db, "5204")["id"]))
    t = service.get_type(tid)
    for call in (lambda: service.save_type(tid, {**t, "notes": "x"}, t["row_version"]),
                 lambda: service.set_active(tid, False, t["row_version"]),
                 lambda: service.delete_type(tid, t["row_version"])):
        with pytest.raises(PermissionDenied):
            call()
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.expense_types.%%'")}
    assert codes == {f"acc.expense_types.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · نوعين جداد بحساب جديد تحت 52 في نفس اللحظة بياخدوا كود ومسلسل
    مختلفين وحساب دليل مختلف · جهاز بيوقف الأب والتاني بيضيف تحته نوع نشط ← واحد يستنى التاني ويترفض."""
    db = env["db"]
    dev1, dev2 = ExpenseTypeService(db), ExpenseTypeService(Database(db.settings))
    try:
        seen1, seen2 = dev1.get_type(1), dev2.get_type(1)
        dev2.save_type(1, {**seen2, "notes": "من الجهاز التاني"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_type(1, {**seen1, "notes": "من الجهاز الأول"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(1, False, seen1["row_version"])
        assert dev1.get_type(1)["notes"] == "من الجهاز التاني"
        p52 = acc(db, "52")["id"]
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_type(None, payload(name_ar="نوع ب", account_mode="auto",
                                                             account_parent_id=p52))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=second_device)
        with db.atomic() as tx:
            seq = dev1.repository.next_seq(tx)                      # الجهاز الأول ماسك قفل الترقيم
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                                # التاني مستني
            clean = dev1._clean(payload(name_ar="نوع أ"))
            clean.update(seq=seq, owns_account=True, account_id=dev1._create_account(tx, p52, clean))
            dev1.repository.insert_type(clean, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        got = {r["code"]: (r["name_ar"], r["acc"]) for r in db.fetch_all(
            "SELECT t.code, t.name_ar, a.code AS acc FROM exp.expense_types t JOIN acc.accounts a "
            "ON a.id = t.account_id WHERE t.seq > 10")}
        assert got == {"EXT-011": ("نوع أ", "5210"), "EXT-012": ("نوع ب", "5211")}
        # جهاز بيوقف «كهرباء» (لسه ماحفظش) والتاني بيضيف تحته نوع نشط ← بيستنى وبعدين يترفض
        results.clear()

        def add_under():
            try:
                results["id"] = dev2.save_type(None, payload(name_ar="كهرباء المصنع", parent_id=1,
                                                             account_id=acc(db, "5203")["id"]))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=add_under)
        with db.atomic() as tx:
            tx.execute("UPDATE exp.expense_types SET is_active = false WHERE id = 1")
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
        worker.join(10)
        assert "error" in results and "موقوف" in str(results["error"]), results
        assert db.fetch_value("SELECT count(*) FROM exp.expense_types WHERE parent_id = 1") == 0
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.expense_types_screen import ExpenseTypesScreen

    return ExpenseTypesScreen(ExpenseTypeService(db))


def test_screen_tree_new_sub_edit_toggle_delete(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    try:
        assert screen.current_record["code"] == "EXT-001"
        root = screen.tree.topLevelItem(0)
        assert root.text(0) == "⁦5⁩  المصروفات والتكاليف"
        acc5203 = screen.items[("account", acc(db, "5203")["id"])]
        assert acc5203.text(1) == "2 نوع" and [acc5203.child(i).text(0) for i in range(acc5203.childCount())] == [
            "🧾 كهرباء", "🧾 مياه"]
        assert screen.items[("account", acc(db, "5201")["id"])].text(1) == "⚠ مفيش نوع"
        assert screen.items[("account", acc(db, "5401")["id"])].text(1) == "حساب نظامي"
        assert screen.table.rowCount() == 10 and "الأنواع: ⁦10⁩" in screen.count_label.text()
        # دوس على حساب من غير نوع ← اللوحة الصفرا ← «نوع جديد على الحساب ده»
        a5201 = acc(db, "5201")["id"]
        screen.tree.setCurrentItem(screen.items[("account", a5201)])
        assert screen.current_id is None and not screen.info_frame.isHidden()
        assert "مفيش عليه نوع" in screen.info_label.text() and not screen.link_button.isHidden()
        screen.new_on_account()
        assert screen.mode == "new" and screen.mode_pick.isChecked() and screen.account_pick.currentData() == a5201
        assert "لازم مركز تكلفة" in screen.cc_label.text() and not screen.tree.isEnabled()
        assert screen.code_edit.text() == "EXT-011 (مقترح)"
        screen.name_ar.setText("مرتبات")
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "EXT-011"
        assert screen.items[("account", a5201)].text(1) == "1 نوع"
        # حساب نظامي ← من غير زرار
        screen.tree.setCurrentItem(screen.items[("account", acc(db, "5401")["id"])])
        assert "حساب نظامي" in screen.info_label.text() and screen.link_button.isHidden()
        # حساب رئيسي ← نوع بحساب جديد تحته
        p53 = acc(db, "53")["id"]
        screen.tree.setCurrentItem(screen.items[("account", p53)])
        assert "الحساب الرئيسي" in screen.info_label.text()
        screen.new_on_account()
        assert screen.mode_auto.isChecked() and screen.account_parent_combo.currentData() == p53
        assert "5306" in screen.account_view.text()
        screen.name_ar.setText("كارتة طرق")
        screen.save()
        assert acc(db, "5306")["name_ar"] == "كارتة طرق" and et(db, "EXT-012")["owns_account"] is True
        # «+ نوع فرعي تحت المحدد» ← الأب والحساب بتوعه
        screen.load(int(et(db, "EXT-008")["id"]))
        assert screen.sub_button.isEnabled()
        screen.new_sub_type()
        assert screen.parent_combo.currentData() == et(db, "EXT-008")["id"]
        assert screen.mode_pick.isChecked() and screen.account_pick.currentData() == acc(db, "5304")["id"]
        screen.name_ar.setText("سولار المولد")
        screen.save()
        sol = screen.items[("type", et(db, "EXT-008")["id"])]
        assert [sol.child(i).text(0) for i in range(sol.childCount())] == ["🧾 سولار المولد"]
        assert et(db, "EXT-013")["parent_id"] == et(db, "EXT-008")["id"]
        # اسم مكرر ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("مياه")
        screen.save()
        assert "بنفس الاسم" in shown[-1] and screen.mode == "new"
        screen.cancel()
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        eid = int(et(db, "EXT-002")["id"])
        screen.load(eid)
        screen.edit_record()
        assert screen.name_ar.isEnabled() and screen.parent_combo.isEnabled() and not screen._account_form.isRowVisible(screen.mode_pick)
        db.execute("UPDATE exp.expense_types SET notes = 'من جهاز تاني' WHERE id = %s", [eid])
        screen.notes.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and et(db, "EXT-002")["notes"] == "من جهاز تاني"
        screen.cancel()
        # إيقاف الأب وتحته نشط ← رسالة · إيقاف نوع ← فلتر «الموقوفة»
        screen.load(int(et(db, "EXT-008")["id"]))
        screen.toggle_active()
        assert "تحته أنواع نشطة" in shown[-1] and et(db, "EXT-008")["is_active"] is True
        screen.load(eid)
        screen.toggle_active()
        assert et(db, "EXT-002")["is_active"] is False
        chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "inactive")
        chip.click()
        assert screen.visible_types() == [eid]
        next(b for b in screen.status_buttons.buttons() if b.property("status") == "all").click()
        # البحث
        screen.search.setText("سولار")
        assert sorted(screen.visible_types()) == sorted([int(et(db, "EXT-008")["id"]), int(et(db, "EXT-013")["id"])])
        screen.search.clear()
        # المسح ← الحساب اللي اتعمل معاه بيتمسح
        screen.load(int(et(db, "EXT-012")["id"]))
        screen.delete()
        assert et(db, "EXT-012") is None and acc(db, "5306") is None and screen.current_id is None
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    groups = {g.title: [t.code for t in g.targets] for g in reg.section("acc").groups}
    assert groups["المصروفات"] == ["expense_types"]
    assert reg.find_target("acc", "expense_types").name_ar == "أنواع المصروفات"
    assert "expense_types" in SCREEN_FACTORIES
