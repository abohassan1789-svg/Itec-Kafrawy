"""اختبارات «الشركاء» (0022: القاعدة + الكود PRT-001 + النسب + رأس المال ببُعد الشريك + الـ Service + الصلاحيات +
جهازين + الشاشة)."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.repositories.partner_repository import format_code
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.partner_service import PartnerService, fmt_percent, share_warning
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

BLANK = {"name_en": None, "partner_type": "general", "mobile": None, "phone": None, "email": None, "address": None,
         "national_id": None, "join_date": None, "profit_percent": 0, "loss_percent": 0, "subscribed_capital": 0,
         "notes": None, "is_active": True}


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
    yield {"db": temp_db, "shown": shown, "service": PartnerService(temp_db)}
    SESSION.clear()
    gc.collect()


def make_screen(db):
    from app.ui.screens.partners_screen import PartnersScreen

    return PartnersScreen(PartnerService(db))


def ins(db, name, **extra):
    """INSERT مباشر (عشان نجرّب القاعدة من غير الـ Service)."""
    cols = {"seq": extra.pop("seq", int(db.fetch_value("SELECT coalesce(max(seq), 0) + 1 FROM party.partners"))),
            "name_ar": name, **extra}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    return db.fetch_value(f"INSERT INTO party.partners ({names}) VALUES ({marks}) RETURNING id", list(cols.values()))


def prt(db, code):
    return db.fetch_one("SELECT * FROM party.partners WHERE code = %s", [code])


def payload(**extra):
    return {**BLANK, "name_ar": "م/ حسام عبد الله", **extra}


# ---- القاعدة ---------------------------------------------------------------
def test_code_format_and_helpers():
    assert [format_code(n) for n in (1, 12, 999, 1000)] == ["PRT-001", "PRT-012", "PRT-999", "PRT-1000"]
    assert [fmt_percent(v) for v in (Decimal("50.00"), Decimal("33.50"), Decimal("33.33"), 0, None)] == \
           ["50", "33.5", "33.33", "0", "0"]
    assert share_warning(Decimal("100"), Decimal("100.00")) is None
    assert "الربح 90% · الخسارة 100%" in share_warning(90, 100)


def test_database_rules(env):
    db = env["db"]
    assert db.fetch_value("SELECT count(*) FROM party.partners") == 0           # مفيش شركاء جاهزين
    pid = ins(db, "حسام", national_id="27805050100011", profit_percent=50, loss_percent=50,
              subscribed_capital=500000, join_date=date(2012, 1, 1))
    p = prt(db, "PRT-001")
    assert p["id"] == pid and p["row_version"] == 1 and p["partner_type"] == "general"
    ins(db, "شريك 1000", seq=1000)
    assert prt(db, "PRT-1000")["seq"] == 1000
    with pytest.raises(psycopg.errors.GeneratedAlways):
        db.execute("INSERT INTO party.partners (seq, code, name_ar) VALUES (5, 'X', 'x')")
    with pytest.raises(psycopg.errors.UniqueViolation):                         # المسلسل مايتكررش
        ins(db, "تاني", seq=1)
    with pytest.raises(psycopg.errors.UniqueViolation):                         # الاسم مايتكررش
        ins(db, "  حسام ")
    with pytest.raises(psycopg.errors.UniqueViolation):                         # الرقم القومي مايتكررش
        ins(db, "تالت", national_id="27805050100011")
    for extra in (dict(seq=0), dict(national_id="123"), dict(partner_type="silent"), dict(profit_percent=101),
                  dict(loss_percent=-1), dict(subscribed_capital=-5), dict(join_date=date(1900, 1, 1)),
                  dict(is_active=False, profit_percent=5)):                     # اللي خرج نسبته 0
        with pytest.raises(psycopg.errors.CheckViolation):
            ins(db, "x", **extra)
    ins(db, "خرج", is_active=False)
    with pytest.raises(psycopg.errors.RaiseException, match="ثابت"):
        db.execute("UPDATE party.partners SET seq = 77 WHERE id = %s", [pid])
    db.execute("UPDATE party.partners SET mobile = '01000003001' WHERE id = %s", [pid])
    assert prt(db, "PRT-001")["row_version"] == 2
    # رأس المال بقى ببُعد الشريك زي جاري الشركاء
    acc = {r["code"]: r["party_kind"] for r in db.fetch_all("SELECT code, party_kind FROM acc.accounts "
                                                            "WHERE code IN ('31', '32')")}
    assert acc == {"31": "partner", "32": "partner"}
    assert db.fetch_value("SELECT default_party_kind FROM acc.account_roles WHERE code = 'capital'") == "partner"


# ---- الـ Service -------------------------------------------------------------
def test_service_save_rules_and_list(env):
    db, service = env["db"], env["service"]
    assert service.new_defaults()["code"] == "PRT-001"
    assert service.share_totals() == {"profit": 0, "loss": 0, "capital": 0, "partners": 0}
    pid = service.save_partner(None, payload(national_id="2780505 0100011", join_date="2012-01-01",
                                             profit_percent="50", loss_percent=50,
                                             subscribed_capital="500,000", mobile="01000003001"))
    p = service.get_partner(pid)
    assert (p["code"], p["national_id"], p["join_date"], p["profit_percent"], p["subscribed_capital"]) == \
           ("PRT-001", "27805050100011", date(2012, 1, 1), Decimal("50.00"), Decimal("500000.00"))
    audit = db.fetch_one("SELECT action, entity_label, record_label FROM sec.audit_log "
                         "WHERE entity = 'party.partners' ORDER BY id DESC LIMIT 1")
    assert audit == {"action": "create", "entity_label": "شريك", "record_label": "PRT-001 م/ حسام عبد الله"}
    with pytest.raises(BusinessError, match="بنفس الاسم"):
        service.save_partner(None, payload(name_ar="م/  حسام عبد الله"))
    with pytest.raises(BusinessError, match="متسجل قبل كده للشريك «م/ حسام عبد الله» \\(PRT-001\\)"):
        service.save_partner(None, payload(name_ar="تاني", national_id="27805050100011"))
    for bad, msg in ((dict(name_ar=""), "اسم الشريك"), (dict(national_id="123"), "14 رقم \\(مكتوب 3\\)"),
                     (dict(email="no-at"), "الإيميل"), (dict(join_date="bad"), "تاريخ الدخول"),
                     (dict(join_date=date(1900, 1, 1)), "برّه"), (dict(partner_type="x"), "متضامن أو موصي"),
                     (dict(profit_percent=120), "من 0 لـ 100"), (dict(loss_percent=-1), "من 0 لـ 100"),
                     (dict(profit_percent="abc"), "لازم يبقى رقم"), (dict(subscribed_capital=-1), "سالب"),
                     (dict(is_active=False, profit_percent=5), "نسبته لازم تبقى 0")):
        with pytest.raises(BusinessError, match=msg):
            service.save_partner(None, payload(**{"name_ar": "جديد", **bad}))
    # الشركاء التانيين: المجموع بيتجمع للنشطين بس
    p2 = service.save_partner(None, payload(name_ar="أ/ طارق منصور", partner_type="general", profit_percent=30,
                                            loss_percent=30, subscribed_capital=300000))
    p3 = service.save_partner(None, payload(name_ar="د/ علا فاروق", partner_type="limited", profit_percent=15,
                                            loss_percent=20, subscribed_capital=150000))
    totals = service.share_totals()
    assert (totals["profit"], totals["loss"], totals["capital"], totals["partners"]) == (95, 100, 950000, 3)
    assert service.share_totals(exclude_id=p3)["profit"] == 80                     # من غير اللي بيتعدّل
    # تعديل بالإصدار + الكود ثابت
    service.save_partner(pid, {**p, "seq": 99, "code": "PRT-099", "profit_percent": 55, "loss_percent": 50},
                         p["row_version"])
    p_after = service.get_partner(pid)
    assert (p_after["code"], p_after["profit_percent"], p_after["row_version"]) == ("PRT-001", 55, 2)
    assert service.share_totals()["profit"] == 100
    # الإيقاف: لازم النسبة تتصفّر الأول
    p2_row = service.get_partner(p2)
    with pytest.raises(BusinessError, match="نسبته لازم تبقى 0"):
        service.set_active(p2, False, p2_row["row_version"])
    service.save_partner(p2, {**p2_row, "profit_percent": 0, "loss_percent": 0}, p2_row["row_version"])
    service.set_active(p2, False, 2)
    assert service.get_partner(p2)["is_active"] is False
    service.set_active(p2, False, 3)                                               # نفس الحالة ← مفيش تغيير
    assert service.get_partner(p2)["row_version"] == 3
    # القائمة: البحث والفلاتر والصفحات والعدد
    codes = lambda rows: [r["code"] for r in rows]                                 # noqa: E731
    assert codes(service.list_partners()) == ["PRT-001", "PRT-002", "PRT-003"]
    for key in ("PRT-002", "prt-002", "002", "طارق"):
        assert "PRT-002" in codes(service.list_partners(key)), key
    assert codes(service.list_partners("2780505")) == ["PRT-001"]                  # الرقم القومي
    assert codes(service.list_partners("01000003")) == ["PRT-001"]                 # الموبايل
    assert codes(service.list_partners(status="inactive")) == ["PRT-002"]
    assert codes(service.list_partners(status="active")) == ["PRT-001", "PRT-003"]
    assert codes(service.list_partners(page=2, page_size=2)) == ["PRT-003"]
    assert service.count_partners() == {"total": 3, "active": 2, "inactive": 1}
    assert len(service.lookup_partners()) == 3
    service.delete_partner(p2, 3)
    assert prt(db, "PRT-002") is None
    assert service.new_defaults()["code"] == "PRT-004"                             # بيكمّل بعد الأكبر


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.partners.view", "acc.partners.create", "acc.partners.save"})
    pid = service.save_partner(None, payload())
    p = service.get_partner(pid)
    with pytest.raises(PermissionDenied):
        service.save_partner(pid, {**p, "name_ar": "x"}, p["row_version"])
    with pytest.raises(PermissionDenied):
        service.set_active(pid, False, p["row_version"])
    with pytest.raises(PermissionDenied):
        service.delete_partner(pid, p["row_version"])
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.partners.%%'")}
    assert codes == {f"acc.partners.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · الإضافة في نفس اللحظة بتاخد أكواد مختلفة ·
    نفس الرقم القومي من جهازين في نفس اللحظة ← واحد بس اللي بيتسجل."""
    db = env["db"]
    dev1, dev2 = PartnerService(db), PartnerService(Database(db.settings))
    try:
        pid = dev1.save_partner(None, payload(profit_percent=50, loss_percent=50))
        seen1, seen2 = dev1.get_partner(pid), dev2.get_partner(pid)
        dev2.save_partner(pid, {**seen2, "profit_percent": 60, "loss_percent": 60}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_partner(pid, {**seen1, "profit_percent": 40, "loss_percent": 40}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(pid, False, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.delete_partner(pid, seen1["row_version"])
        assert dev1.get_partner(pid)["profit_percent"] == 60
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_partner(None, payload(name_ar="شريك الجهاز التاني",
                                                               national_id="29001010100088"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_seq(tx)                   # الجهاز الأول ماسك قفل الترقيم
            worker = threading.Thread(target=second_device)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            dev1.repository.insert_partner({**BLANK, "seq": first, "name_ar": "شريك الجهاز الأول",
                                            "national_id": "29001010100088"}, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "متسجل قبل كده" in str(results["error"])
        results.clear()

        def third():
            try:
                results["id"] = dev2.save_partner(None, payload(name_ar="شريك تالت"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        with db.atomic() as tx:
            first = dev1.repository.next_seq(tx)
            worker = threading.Thread(target=third)
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            dev1.repository.insert_partner({**BLANK, "seq": first, "name_ar": "شريك رابع"}, ex=tx)
        worker.join(10)
        assert "error" not in results
        assert sorted(r["code"] for r in db.fetch_all("SELECT code FROM party.partners")) == \
               ["PRT-001", "PRT-002", "PRT-003", "PRT-004"]
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen_new_save_edit_toggle_delete(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    try:
        assert screen.table.rowCount() == 0 and screen.share_label.text() == ""       # فاضية في الأول
        screen.new_record()
        assert screen.mode == "new" and screen.code_edit.text() == "PRT-001 (مقترح)"
        assert not screen.join_date.isEnabled() and not screen.loss_percent.isEnabled()  # الخسارة = الربح
        screen.name_ar.setText("م/ حسام عبد الله")
        screen.national_id.setText("27805050100011")
        screen.has_join.setChecked(True)
        screen.join_date.setDate(QDate(2012, 1, 1))
        screen.profit_percent.setValue(60)
        assert screen.loss_percent.value() == 60
        assert "⚠" in screen.share_hint.text() and "الربح 60%" in screen.share_hint.text()
        screen.subscribed_capital.setValue(500000)
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "PRT-001"
        p = prt(db, "PRT-001")
        assert (p["national_id"], p["join_date"], p["profit_percent"], p["loss_percent"],
                p["subscribed_capital"], p["partner_type"]) == \
               ("27805050100011", date(2012, 1, 1), 60, 60, 500000, "general")
        texts = [screen.table.item(0, col).text() for col in range(screen.table.columnCount())]
        assert texts[:6] == ["PRT-001", "م/ حسام عبد الله", "متضامن", "60%", "60%", "500,000.00"]
        assert "⚠" in screen.share_label.text() and "الربح 60%" in screen.share_label.text()
        # شريك تاني موصي بنسبة خسارة مختلفة ← المجموع يكمّل 100
        screen.new_record()
        screen.name_ar.setText("د/ علا فاروق")
        screen.partner_type.setCurrentIndex(screen.partner_type.findData("limited"))
        screen.profit_percent.setValue(40)
        screen.same_loss.setChecked(False)
        assert screen.loss_percent.isEnabled()
        screen.loss_percent.setValue(40)
        assert screen.share_hint.text().startswith("✔")
        screen.save()
        p2 = prt(db, "PRT-002")
        assert (p2["partner_type"], p2["profit_percent"], p2["loss_percent"], p2["join_date"]) == \
               ("limited", 40, 40, None)
        assert screen.share_label.text().startswith("✔") and "500,000.00" in screen.share_label.text()
        # الرقم القومي الغلط ← رسالة والفورم بيفضل
        screen.new_record()
        screen.name_ar.setText("شخص تالت")
        screen.national_id.setText("123")
        screen.save()
        assert "14 رقم" in shown[-1] and screen.mode == "new"
        screen.cancel()
        screen.load(int(p["id"]))
        assert screen.has_join.isChecked() and screen.same_loss.isChecked()
        assert screen.share_hint.text().startswith("✔")
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.edit_record()
        db.execute("UPDATE party.partners SET notes = 'من جهاز تاني' WHERE code = 'PRT-001'")
        screen.subscribed_capital.setValue(1)
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and prt(db, "PRT-001")["subscribed_capital"] == 500000
        screen.cancel()
        # الإيقاف بنسبة ← رسالة ومفيش تغيير
        screen.load(int(p["id"]))
        assert screen.toggle_button.isEnabled()
        screen.toggle_active()
        assert "نسبته لازم تبقى 0" in shown[-1] and prt(db, "PRT-001")["is_active"] is True
        # صفّر النسبة ووقّفه ← يختفي من «النشط»
        screen.edit_record()
        screen.profit_percent.setValue(0)
        screen.save()
        screen.toggle_active()
        assert prt(db, "PRT-001")["is_active"] is False
        active_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "active")
        active_chip.click()
        assert screen.table.rowCount() == 1 and screen.current_id is None
        all_chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "all")
        all_chip.click()
        assert "خرجوا 1" in screen.count_label.text()
        screen.load(int(p["id"]))
        screen.delete()
        assert prt(db, "PRT-001") is None and screen.current_id is None
    finally:
        screen.close()


def test_screen_paging(env, monkeypatch):
    import app.ui.screens.partners_screen as module

    db = env["db"]
    for i in range(1, 6):
        ins(db, f"شريك {i}", profit_percent=20, loss_percent=20)
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    screen = make_screen(db)
    try:
        assert screen.page_label.text() == "صفحة 1 من 3" and screen.table.rowCount() == 2
        assert screen.current_record["code"] == "PRT-001" and screen.share_label.text().startswith("✔")
        screen.next_button.click()
        screen.next_button.click()
        assert screen.page_label.text() == "صفحة 3 من 3" and screen.table.rowCount() == 1
        screen.search.setText("PRT-004")
        assert screen.page_label.text() == "صفحة 1 من 1" and screen.table.rowCount() == 1
        screen.search.clear()
        screen.new_record()
        assert not screen.next_button.isEnabled()
        screen.cancel()
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("acc").groups[3]
    assert group.title == "حقوق الملكية" and [t.code for t in group.targets] == ["partners"]
    target = reg.find_target("acc", "partners")
    assert target.name_ar == "الشركاء"
    assert "partners" in SCREEN_FACTORIES
