"""اختبارات «ميزان المراجعة» (المرحلة 4 — النموذج 4): الأعمدة الثمانية · الرصيد السابق (الميزانية كل اللي قبل «من»
والإيرادات والمصروفات من أول السنة المالية) · أرباح السنين اللي فاتت على الأرباح المحتجزة · المرحّل بس · الشجرة
والمستويات والحسابات الصفرية · التوازن · الصلاحية · جهاز تاني بيرحّل في نفس الوقت · الشاشة والدبل كليك."""

from __future__ import annotations

import datetime as dt
import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.trial_balance_service import TYPES, TrialBalanceService, columns8, is_balanced
from app.ui.common import messages
from tests.test_account_statement import entry, one

FEB, Y0, Y1 = dt.date(2026, 2, 1), dt.date(2026, 1, 1), dt.date(2026, 12, 31)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    db = temp_db
    ids = {"A": one(db, "SELECT id FROM party.customers WHERE code = '0'"),
           "S": one(db, "SELECT id FROM party.suppliers ORDER BY seq LIMIT 1")}
    e = {
        "E7": entry(db, "2025-11-01", "رصيد خزنة الفرع", [("110102", 400, 0), ("4101", 0, 400)]),
        "E1": entry(db, "2025-12-20", "فاتورة قديمة", [("1103", 1000, 0, ids["A"]), ("4101", 0, 1000)]),
        "E2": entry(db, "2026-01-10", "فاتورة يناير", [("1103", 500, 0, ids["A"]), ("4101", 0, 500)]),
        "E3": entry(db, "2026-02-05", "تحصيل نقدي", [("110101", 300, 0), ("1103", 0, 300, ids["A"])]),
        "E8": entry(db, "2026-03-05", "إيجار على المورد", [("5202", 750, 0), ("2101", 0, 750, ids["S"])]),
        # مسودة في الفترة — مابتظهرش
        "D1": entry(db, "2026-02-15", "مسودة", [("1103", 7777, 0, ids["A"]), ("4101", 0, 7777)], post=False),
    }
    yield {"db": db, "shown": shown, "service": TrialBalanceService(db), "ids": ids, "e": e}
    SESSION.clear()
    gc.collect()


def by_code(res):
    return {r["code"]: r for r in res["accounts"]}


def cols(r):
    return tuple(r[k] for k in ("pd", "pc", "debit", "credit", "td", "tc", "ed", "ec"))


# ---- الميزان ------------------------------------------------------------------
def test_columns8_helper():
    c = columns8(Decimal("1500"), Decimal("0"), Decimal("300"))
    assert (c["pd"], c["pc"], c["td"], c["tc"], c["ed"], c["ec"]) == (1500, 0, 1500, 300, 1200, 0)
    c = columns8(Decimal("-500"), Decimal("200"), Decimal("0"))
    assert (c["pd"], c["pc"], c["td"], c["tc"], c["ed"], c["ec"], c["end"]) == (0, 500, 200, 500, 0, 300, -300)


def test_eight_columns_prev_rules_and_carried_profit(env):
    """الميزانية: كل اللي قبل «من» · الإيرادات: من أول السنة المالية بس · إيراد 2025 على «الأرباح المحتجزة»."""
    service = env["service"]
    res = service.trial_balance(FEB, Y1)
    a = by_code(res)
    assert cols(a["1103"]) == (1500, 0, 0, 300, 1500, 300, 1200, 0)          # 1000 (2025) + 500 (يناير) ← سابق
    assert cols(a["110102"]) == (400, 0, 0, 0, 400, 0, 400, 0)                # ميزانية من 2025
    assert cols(a["4101"]) == (0, 500, 0, 0, 0, 500, 0, 500)                  # يناير 2026 بس (مش 2025)
    assert cols(a["33"]) == (0, 1400, 0, 0, 0, 1400, 0, 1400)                 # إيراد 2025 (1,400) لسه ماتقفلش
    assert a["33"]["carried"] == -1400 and res["carried"] == -1400 and res["retained"]["code"] == "33"
    assert cols(a["5202"]) == (0, 0, 750, 0, 750, 0, 750, 0)
    assert cols(a["2101"]) == (0, 0, 0, 750, 0, 750, 0, 750)
    # الرئيسي = مجموع اللي تحته (والرئيسي فوقه برضه)
    assert a["1101"]["prev"] == 400 and a["1101"]["debit"] == 300 and a["1101"]["ed"] == 700
    assert a["11"]["prev"] == 1900 and a["11"]["end"] == 1900 and a["3"]["ec"] == 1400
    assert a["4"]["pc"] == 500
    # الإجمالي من الحسابات الفرعية · متوازن في كل عمودين
    g = res["grand"]
    assert (g["pd"], g["pc"], g["debit"], g["credit"]) == (1900, 1900, 1050, 1050)
    assert g["td"] == g["tc"] == 2950 and g["ed"] == g["ec"] == 2650 and res["balanced"]
    assert res["year_start"] == Y0
    # المسودة (7,777) مابتدخلش — ولما تترحّل تدخل في حركة الفترة
    assert all(r["debit"] != 7777 for r in res["accounts"])
    env["db"].execute("UPDATE acc.journal_entries SET status = 'posted' WHERE id = %s", [env["e"]["D1"]])
    again = service.trial_balance(FEB, Y1)
    assert by_code(again)["1103"]["debit"] == 7777 and again["balanced"]


def test_period_from_year_start_and_older_year(env):
    service = env["service"]
    res = service.trial_balance(Y0, Y1)
    a = by_code(res)
    assert cols(a["4101"]) == (0, 0, 0, 500, 0, 500, 0, 500)                  # سابق الإيرادات من أول 2026 = صفر
    assert a["33"]["pc"] == 1400 and res["balanced"]
    # من أول 2025: مفيش سنين قبلها ← مفيش أرباح مرحّلة، والإيراد كله حركة الفترة
    old = service.trial_balance(dt.date(2025, 1, 1), Y1)
    a = by_code(old)
    assert old["carried"] == 0 and old["retained"] is None and "33" not in a
    assert a["4101"]["credit"] == 1900 and old["balanced"] and old["year_start"] == dt.date(2025, 1, 1)
    # «إلى» قبل «من» بيتقلب
    assert service.trial_balance(Y1, Y0)["date_from"] == Y0


def test_levels_zero_accounts_and_tabs(env):
    service = env["service"]
    tree = service.trial_balance(FEB, Y1)
    codes = [r["code"] for r in tree["accounts"]]
    assert codes == sorted(codes) and "1" in codes and "1103" in codes        # شجرة بكل المستويات
    assert "110201" not in codes                                              # صفري ← مستخبي
    zero = service.trial_balance(FEB, Y1, show_zero=True)
    assert "110201" in [r["code"] for r in zero["accounts"]] and zero["grand"] == tree["grand"]
    lv1 = service.trial_balance(FEB, Y1, level=1)
    assert {r["level"] for r in lv1["accounts"]} == {1} and lv1["balanced"]
    assert lv1["grand"]["ed"] == lv1["grand"]["ec"] == 2650                    # بالصافي على مستوى النوع
    lv3 = service.trial_balance(FEB, Y1, level=3)
    for r in lv3["accounts"]:
        assert r["level"] == 3 or (r["is_leaf"] and r["level"] < 3)
    assert "33" in [r["code"] for r in lv3["accounts"]] and lv3["balanced"]   # فرعي مستواه 2 بيظهر برضه
    leaf = service.trial_balance(FEB, Y1, level="leaf")
    assert all(r["is_leaf"] for r in leaf["accounts"]) and leaf["grand"] == tree["grand"]
    assert service.levels()[0] == 1
    # التبويبات: النوع ← حساباته + إجماليه · «الكل» ← سطر لكل نوع + إجمالي الميزان (= مجموع الأنواع)
    asset = service.rows_for(tree, "asset")
    assert all(r["account_type"] == "asset" for r in asset[:-1]) and asset[-1]["kind"] == "type_total"
    assert asset[-1]["ed"] == 1900 and tree["active_counts"]["asset"] == 3
    allrows = service.rows_for(tree, None)
    assert [r["kind"] for r in allrows] == ["type"] * 5 + ["grand_total"]
    assert sum(r["pd"] for r in allrows[:5]) == allrows[-1]["pd"] == 1900
    assert is_balanced(allrows[-1]) and set(TYPES) == {"asset", "liability", "equity", "revenue", "expense"}


def test_permission(env):
    db, service = env["db"], env["service"]
    admin_id = one(db, "SELECT id FROM sec.app_users WHERE username = %s", DEFAULT_ADMIN_USERNAME)
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False}, set())
    with pytest.raises(PermissionDenied):
        service.trial_balance(FEB, Y1)
    with pytest.raises(PermissionDenied):
        service.levels()
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.trial_balance.view"})
    assert service.trial_balance(FEB, Y1)["balanced"]
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.trial_balance.%%'")}
    assert codes == {"acc.trial_balance.view"}


def test_other_device_posts_meanwhile(env, monkeypatch):
    """جهاز تاني بيرحّل وإحنا بنقرا الميزان: الميزان بيفضل من لحظة واحدة (متوازن ومن غير القيد الجديد) ومابيقفلش."""
    db, ids = env["db"], env["ids"]
    other = Database(db.settings)
    try:
        service = TrialBalanceService(db)
        real = service.repository.accounts
        posted: list[int] = []

        def accounts_then_post(*args, **kwargs):
            rows = real(*args, **kwargs)
            if not posted:                     # الجهاز التاني بيرحّل بعد ما اللقطة بدأت وقبل الأرصدة
                posted.append(entry(other, "2026-03-10", "من جهاز تاني", [("1103", 250, 0, ids["A"]),
                                                                       ("4101", 0, 250)]))
            return rows

        monkeypatch.setattr(service.repository, "accounts", accounts_then_post)
        res = service.trial_balance(FEB, Y1)
        assert posted and one(db, "SELECT status FROM acc.journal_entries WHERE id = %s", posted[0]) == "posted"
        assert by_code(res)["1103"]["debit"] == 0 and res["balanced"]
        monkeypatch.setattr(service.repository, "accounts", real)
        again = service.trial_balance(FEB, Y1)
        assert by_code(again)["1103"]["debit"] == 250 and again["balanced"]
    finally:
        other.close()


def test_default_period(env):
    service = env["service"]
    assert service.default_period(dt.date(2026, 5, 3)) == (Y0, dt.date(2026, 5, 3))
    assert service.default_period(dt.date(2031, 7, 9)) == (dt.date(2031, 1, 1), dt.date(2031, 7, 9))


# ---- الشاشة ----------------------------------------------------------------
class FakeStatement:
    def __init__(self, opened):
        self.opened = opened

    def show_account(self, account_id, date_from, date_to, grouping="main"):
        self.opened.append((account_id, date_from, date_to, grouping))


def make_screen(env, opened):
    from app.ui.screens.trial_balance_screen import TrialBalanceScreen

    def opener(key):
        opened.append(key)
        return FakeStatement(opened)

    screen = TrialBalanceScreen(env["service"], opener=opener)
    screen.from_edit.setDate(screen.from_edit.date().__class__(2026, 2, 1))
    screen.to_edit.setDate(screen.to_edit.date().__class__(2026, 12, 31))
    return screen


def row_of(screen, code):
    return next(r for r, row in enumerate(screen.table.rows) if row.get("code") == code)


def test_screen(env, monkeypatch):
    db = env["db"]
    opened: list = []
    screen = make_screen(env, opened)
    try:
        assert not screen.new_button.isVisible() and not screen.search.isVisibleTo(screen)
        assert screen.export_button.isVisibleTo(screen) and screen.export_button.isEnabled()
        assert screen.table.columnCount() == 10
        assert screen.table.horizontalHeaderItem(2).text() == "رصيد سابق\nمدين"
        assert screen.table.horizontalHeaderItem(9).text() == "الرصيد\nدائن"
        # التبويب الافتراضي: الأصول بالشجرة + إجمالي الأصول
        assert screen.type_tabs["asset"].isChecked() and screen.type_tabs["asset"].text().endswith("(⁦3⁩)")
        rows = screen.table.rows
        assert rows[-1]["kind"] == "type_total" and all(r["account_type"] == "asset" for r in rows[:-1])
        r = row_of(screen, "1103")
        assert screen.table.item(r, 2).text() == "1,500.00" and screen.table.item(r, 5).text() == "300.00"
        assert screen.table.item(r, 8).text() == "1,200.00" and screen.table.item(r, 9).text() == ""
        assert screen.table.item(r, 1).text().startswith("    ")             # مسافة الشجرة
        assert screen.table.item(len(rows) - 1, 8).text() == "1,900.00"
        assert "متوازن" in screen.check_label.text() and "المرحّل بس" in screen.count_label.text()
        # التبويبات بتتبدّل من غير ما الميزان يتقرا تاني
        calls: list = []
        real = screen.service.trial_balance
        monkeypatch.setattr(screen.service, "trial_balance", lambda **k: calls.append(k) or real(**k))
        screen.type_tabs["equity"].click()
        assert screen.current_type == "equity" and not calls
        r = row_of(screen, "33")
        assert screen.table.item(r, 3).text() == "1,400.00" and screen.table.item(r, 1).text().endswith("*")
        assert "ماتقفلتش" in screen.count_label.text()
        screen.type_tabs["all"].click()
        assert [x["kind"] for x in screen.table.rows] == ["type"] * 5 + ["grand_total"] and not calls
        assert screen.table.item(5, 2).text() == "1,900.00" and screen.table.item(5, 3).text() == "1,900.00"
        # المستوى والحسابات الصفرية بيعيدوا الميزان
        screen.type_tabs["asset"].click()
        screen.level_combo.setCurrentIndex(screen.level_combo.findData("leaf"))
        assert calls and calls[-1]["level"] == "leaf"
        assert all(x["is_leaf"] for x in screen.table.rows[:-1])
        assert not screen.table.item(row_of(screen, "1103"), 1).text().startswith(" ")
        screen.zero_check.setChecked(True)
        assert calls[-1]["show_zero"] and any(x.get("code") == "110201" for x in screen.table.rows)
        screen.level_combo.setCurrentIndex(0)
        screen.zero_check.setChecked(False)
        # دبل كليك على حساب ← كشف الحساب على نفس الحساب والفترة · سطر الإجمالي مابيفتحش حاجة
        r = row_of(screen, "1103")
        screen._on_double_click(r, 1)
        acc_id = one(db, "SELECT id FROM acc.accounts WHERE code = '1103'")
        assert opened[-2:] == ["account_statement", (acc_id, FEB, Y1, "main")]
        screen._on_double_click(row_of(screen, "11"), 1)
        assert opened[-1][3] == "sub"
        n = len(opened)
        screen._on_double_click(len(screen.table.rows) - 1, 1)
        assert len(opened) == n
        # شرايح الفترة
        chip = {c.property("value"): c for c in screen.period_buttons.buttons()}
        chip["year"].click()
        assert screen.from_edit.date().toPython() == Y0 and chip["year"].isChecked()
        # تصدير إكسل (كل الأنواع + الإجمالي)
        path = os.path.join(os.environ.get("TEMP", "."), "tb_test.xlsx")
        monkeypatch.setattr("app.ui.screens.trial_balance_screen.QFileDialog.getSaveFileName",
                            lambda *a, **k: (path, ""))
        screen.export_excel()
        from openpyxl import load_workbook

        ws = load_workbook(path).active
        assert ws.cell(1, 3).value == "رصيد سابق مدين" and ws.cell(ws.max_row, 2).value == "إجمالي الميزان"
        assert sum(1 for row in ws.iter_rows(min_row=2) if str(row[1].value).startswith("إجمالي ")) == 6
        os.remove(path)
    finally:
        screen.close()
        screen.deleteLater()


def test_menu_and_registry(env):
    from app.services import navigation_service as nav
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("acc", "trial_balance")
    assert target.name_ar == "ميزان المراجعة" and target.action_codes() == ("view",)
    assert "trial_balance" in SCREEN_FACTORIES
    item = nav.find_item(nav.menu_tree(SESSION.can), "trial_balance")
    assert item is not None and item["module"] == "acc"


def test_opens_from_menu_and_double_click_opens_statement(env, monkeypatch):
    """من القايمة ← الميزان · دبل كليك على حساب ← «كشف الحساب» الحقيقي على نفس الحساب والفترة."""
    from app.services.statement_service import StatementService
    from app.ui import main_window as mw
    from app.ui.screens.account_statement_screen import AccountStatementScreen
    from app.ui.screens.trial_balance_screen import TrialBalanceScreen

    class FakeAuth:
        def logout(self):
            SESSION.clear()

    db = env["db"]
    monkeypatch.setattr(mw, "_info", lambda *_a: None)
    window = mw.MainWindow(FakeAuth(), photo_provider=lambda: None, factories={
        "trial_balance": lambda: TrialBalanceScreen(TrialBalanceService(db)),
        "account_statement": lambda: AccountStatementScreen(StatementService(db))})
    try:
        screen = window.open_screen("trial_balance")
        assert isinstance(screen, TrialBalanceScreen) and screen.isVisible()
        screen.from_edit.setDate(screen.from_edit.date().__class__(2026, 2, 1))
        screen.to_edit.setDate(screen.to_edit.date().__class__(2026, 12, 31))
        screen._on_double_click(row_of(screen, "1103"), 1)
        stmt = window.open_windows.get("account_statement")
        assert isinstance(stmt, AccountStatementScreen)
        res = stmt.result
        assert res["kind"] == "account" and res["target"]["code"] == "1103"
        assert (res["date_from"], res["date_to"]) == (FEB, Y1)
        assert res["prev"] == 1500 and res["end"] == 1200                    # = سطر 1103 في الميزان
    finally:
        for w in list(window.open_windows.values()):
            w.close()
        window.close()
        window.deleteLater()
