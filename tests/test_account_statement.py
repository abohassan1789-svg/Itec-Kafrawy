"""اختبارات «كشف الحساب ودفتر الأستاذ» (المرحلة 4 — النموذج 1): الرصيد السابق · الرصيد الجاري · المرحّل بس ·
الطرف · الخزنة والبنك · الحساب الرئيسي بالتجميعين · الصفحات · الصلاحية · جهاز تاني بيرحّل في نفس الوقت · الشاشة."""

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
from app.services.errors import BusinessError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.statement_service import StatementService, balance_text, doc_label
from app.ui.common import messages

Y0, Y1 = dt.date(2026, 1, 1), dt.date(2026, 12, 31)


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
           "B": one(db, "INSERT INTO party.customers (code, name_ar, group_id, currency_code) VALUES "
                        "('7', 'عميل تاني', (SELECT group_id FROM party.customers WHERE code = '0'), 'EGP') "
                        "RETURNING id"),
           "S": one(db, "SELECT id FROM party.suppliers ORDER BY seq LIMIT 1")}
    e = {
        "E7": entry(db, "2025-11-01", "رصيد خزنة الفرع", [("110102", 400, 0), ("4101", 0, 400)]),
        "E1": entry(db, "2025-12-20", "فاتورة قديمة", [("1103", 1000, 0, ids["A"]), ("4101", 0, 1000)]),
        "E2": entry(db, "2026-01-10", "فاتورة يناير", [("1103", 500, 0, ids["A"]), ("4101", 0, 500)]),
        "E3": entry(db, "2026-02-05", "تحصيل نقدي", [("110101", 300, 0), ("1103", 0, 300, ids["A"])]),
        "E4": entry(db, "2026-02-10", "عميل تاني", [("1103", 999, 0, ids["B"]), ("4101", 0, 999)]),
        "E6": entry(db, "2026-03-01", "إيداع في البنك", [("110201", 2000, 0), ("110101", 0, 2000)]),
        "E8": entry(db, "2026-03-05", "شراء من المورد", [("5202", 750, 0), ("2101", 0, 750, ids["S"])]),
        # مسودة في الفترة — مابتظهرش
        "D1": entry(db, "2026-02-15", "مسودة", [("1103", 7777, 0, ids["A"]), ("4101", 0, 7777)], post=False),
    }
    yield {"db": db, "shown": shown, "service": StatementService(db), "ids": ids, "e": e}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def acc(db, code):
    return one(db, "SELECT id FROM acc.accounts WHERE code = %s", code)


def entry(db, d, desc, lines, post=True):
    """قيد يدوي بالـ SQL (ولو post ← يترحّل وياخد رقم JV)."""
    eid = one(db, "INSERT INTO acc.journal_entries (entry_date, fiscal_year_id, description) VALUES "
                  "(%s, (SELECT id FROM core.fiscal_years WHERE %s::date BETWEEN start_date AND end_date), %s) "
                  "RETURNING id", d, d, desc)
    for n, line in enumerate(lines, start=1):
        code, dr, cr, *party = line
        a = db.fetch_one("SELECT id, party_kind FROM acc.accounts WHERE code = %s", [code])
        col = {"customer": "customer_id", "supplier": "supplier_id"}.get(a["party_kind"], "customer_id")
        db.execute(f"INSERT INTO acc.journal_lines (entry_id, line_no, account_id, debit, credit, {col}) "
                   "VALUES (%s, %s, %s, %s, %s, %s)", [eid, n, a["id"], dr, cr, party[0] if party else None])
    if post:
        db.execute("UPDATE acc.journal_entries SET status = 'posted' WHERE id = %s", [eid])
    return eid


def kinds(res):
    return [r["kind"] for r in res["rows"]]


def lines_of(res):
    return [r for r in res["rows"] if r["kind"] == "line"]


# ---- الكشف -------------------------------------------------------------------
def test_helpers():
    assert balance_text(Decimal("88300")) == "88,300.00 مدين"
    assert balance_text(Decimal("-1800.5")) == "1,800.50 دائن"
    assert balance_text(0) == "0.00"
    assert doc_label({"source_type": "manual", "entry_no": "JV-2026-00001"}) == "JV-2026-00001"
    assert doc_label({"source_type": "opening_balance", "source_no": "OB-2026", "entry_no": None}) == "OB-2026"


def test_customer_prev_and_running_balance(env):
    """رصيد سابق = كل اللي قبل «من» · الرصيد الجاري · المرحّل بس · العميل ده بس."""
    service, ids = env["service"], env["ids"]
    res = service.statement("customer", ids["A"], Y0, Y1)
    assert res["prev"] == 1000 and res["debit"] == 500 and res["credit"] == 300 and res["end"] == 1200
    assert kinds(res) == ["prev", "line", "line", "total", "end"]
    ls = lines_of(res)
    assert [(r["doc_no"], r["debit"], r["credit"], r["balance"]) for r in ls] == [
        ("JV-2026-00001", 500, 0, 1500), ("JV-2026-00002", 0, 300, 1200)]
    assert ls[0]["description"] == "فاتورة يناير" and ls[0]["entry_id"] == env["e"]["E2"]
    assert res["rows"][0]["balance"] == 1000 and res["rows"][-1]["balance"] == 1200
    assert res["rows"][-2]["debit"] == 500 and res["rows"][-2]["credit"] == 300
    # من 2026-02-01 ← الرصيد السابق = كل اللي لحد 2026-01-31
    res = service.statement("customer", ids["A"], dt.date(2026, 2, 1), Y1)
    assert res["prev"] == 1500 and [r["balance"] for r in lines_of(res)] == [1200]
    # المسودة (7,777) مابتظهرش لا في الحركات ولا في الرصيد — ولما تترحّل تظهر
    assert all(r["debit"] != 7777 for r in lines_of(res))
    env["db"].execute("UPDATE acc.journal_entries SET status = 'posted' WHERE id = %s", [env["e"]["D1"]])
    assert service.statement("customer", ids["A"], Y0, Y1)["end"] == 1200 + 7777
    # عميل تاني ومورد
    assert service.statement("customer", ids["B"], Y0, Y1)["end"] == 999
    sup = service.statement("supplier", ids["S"], Y0, Y1)
    assert sup["end"] == -750 and balance_text(sup["end"]) == "750.00 دائن"
    # «إلى» قبل «من» بيتقلب · من غير اسم ← رسالة
    assert service.statement("customer", ids["A"], Y1, Y0)["date_from"] == Y0
    with pytest.raises(BusinessError, match="اختار الاسم"):
        service.statement("customer", None, Y0, Y1)
    with pytest.raises(BusinessError, match="مش موجود"):
        service.statement("customer", 99999, Y0, Y1)


def test_treasury_bank_and_leaf_account(env):
    db, service = env["db"], env["service"]
    trs = service.statement("treasury", 1, Y0, Y1)                      # TRS-001 على 110101
    assert trs["target"]["code"] == "TRS-001"
    assert [r["balance"] for r in lines_of(trs)] == [300, -1700] and trs["end"] == -1700
    bank = service.statement("bank", 1, Y0, Y1)                         # BNK-001-01 على 110201
    assert bank["end"] == 2000 and len(lines_of(bank)) == 1
    leaf = service.statement("account", acc(db, "110101"), Y0, Y1)
    assert leaf["end"] == trs["end"] and not leaf["is_main_account"]
    assert [r["code"] for r in service.choices("treasury")] == ["TRS-001", "TRS-002"]
    assert any(not r["is_leaf"] for r in service.choices("account"))


def test_main_account_groupings(env):
    """1101 (فيه 110101 و110102): «بالرئيسية» = كشف واحد · «بالفرعية» = كل فرعي لوحده + إجمالي الكل."""
    db, service = env["db"], env["service"]
    main = service.statement("account", acc(db, "1101"), Y0, Y1, grouping="main")
    assert main["is_main_account"] and main["grouping"] == "main"
    assert main["prev"] == 400 and main["end"] == 400 + 300 - 2000
    assert kinds(main) == ["prev", "line", "line", "total", "end"]
    assert [r["balance"] for r in lines_of(main)] == [700, -1300]
    sub = service.statement("account", acc(db, "1101"), Y0, Y1, grouping="sub")
    assert sub["grouping"] == "sub" and sub["end"] == main["end"] and sub["prev"] == main["prev"]
    assert kinds(sub) == ["group", "prev", "line", "line", "total", "end",       # 110101
                          "group", "prev", "total", "end",                       # 110102 (رصيد سابق بس)
                          "grand_total", "grand_end"]
    rows = sub["rows"]
    assert "110101" in rows[0]["label"] and "110102" in rows[6]["label"]
    assert [r["balance"] for r in lines_of(sub)] == [300, -1700]                 # من رصيد 110101 لوحده
    assert rows[5]["balance"] == -1700 and rows[7]["balance"] == 400 and rows[9]["balance"] == 400
    assert rows[-1]["balance"] == -1300 and rows[-2]["debit"] == 300 and rows[-2]["credit"] == 2000
    # الحساب اللي مالوش رصيد ولا حركة مابيظهرش · الفرعي/الخزنة مالهمش تجميع
    leaf = service.statement("account", acc(db, "110101"), Y0, Y1, grouping="sub")
    assert leaf["grouping"] == "main"
    root = service.statement("account", acc(db, "11"), Y0, Y1, grouping="sub")
    labels = [r["label"] for r in root["rows"] if r["kind"] == "group"]
    assert not any("110202" in x for x in labels) and any("1103" in x for x in labels)
    assert root["end"] == service.statement("account", acc(db, "11"), Y0, Y1)["end"]


def test_paging(env):
    """الحركات بصفحات: الرصيد الجاري بيكمّل · «رصيد منقول» أول الصفحة · الإجمالي والآخر في آخر صفحة بس."""
    db, service, ids = env["db"], env["service"], env["ids"]
    p1 = service.statement("customer", ids["A"], Y0, Y1, page=1, page_size=1)
    p2 = service.statement("customer", ids["A"], Y0, Y1, page=2, page_size=1)
    assert p1["pages"] == 2 and kinds(p1) == ["prev", "line"]
    assert kinds(p2) == ["carry", "line", "total", "end"]
    assert p2["rows"][0]["balance"] == 1500 and lines_of(p2)[0]["balance"] == 1200
    assert service.statement("customer", ids["A"], Y0, Y1, page=9, page_size=1)["page"] == 2
    # «بالفرعية» بصفحات: الحساب اللي اتقسم بيكمّل بـ «(تكملة)» ورصيد منقول
    s2 = service.statement("account", acc(db, "1101"), Y0, Y1, grouping="sub", page=2, page_size=1)
    assert s2["pages"] == 3 and kinds(s2) == ["group", "carry", "line", "total", "end"]
    assert "(تكملة)" in s2["rows"][0]["label"] and s2["rows"][1]["balance"] == 300
    s3 = service.statement("account", acc(db, "1101"), Y0, Y1, grouping="sub", page=3, page_size=1)
    assert kinds(s3) == ["group", "prev", "total", "end", "grand_total", "grand_end"]
    # كله مرة واحدة (التصدير)
    full = service.statement("account", acc(db, "1101"), Y0, Y1, grouping="sub", page_size=None)
    assert full["pages"] == 1 and len(lines_of(full)) == 2


def test_permission(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    admin_id = one(db, "SELECT id FROM sec.app_users WHERE username = %s", DEFAULT_ADMIN_USERNAME)
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False}, set())
    with pytest.raises(PermissionDenied):
        service.statement("customer", ids["A"], Y0, Y1)
    with pytest.raises(PermissionDenied):
        service.choices("customer")
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"acc.account_statement.view"})
    assert service.statement("customer", ids["A"], Y0, Y1)["end"] == 1200
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.account_statement.%%'")}
    assert codes == {"acc.account_statement.view"}


def test_other_device_posts_meanwhile(env, monkeypatch):
    """جهاز تاني بيرحّل وإحنا بنقرا الكشف: الكشف بيفضل متطابق مع نفسه (لقطة واحدة) ومش بيقفل على التاني."""
    db, ids = env["db"], env["ids"]
    other = Database(db.settings)
    try:
        service = StatementService(db)
        real = service.repository.summary
        posted: list[int] = []

        def summary_then_post(*args, **kwargs):
            rows = real(*args, **kwargs)
            if not posted:                    # الجهاز التاني بيرحّل قيد للعميل ده بعد ما الرصيد اتحسب
                posted.append(entry(other, "2026-01-20", "من جهاز تاني", [("1103", 250, 0, ids["A"]),
                                                                       ("4101", 0, 250)]))
            return rows

        monkeypatch.setattr(service.repository, "summary", summary_then_post)
        res = service.statement("customer", ids["A"], Y0, Y1)
        assert posted and one(db, "SELECT status FROM acc.journal_entries WHERE id = %s", posted[0]) == "posted"
        ls = lines_of(res)
        assert sum(r["debit"] for r in ls) == res["debit"] == 500 and len(ls) == 2
        assert ls[-1]["balance"] == res["end"] == 1200
        monkeypatch.setattr(service.repository, "summary", real)
        again = service.statement("customer", ids["A"], Y0, Y1)
        assert again["end"] == 1450 and len(lines_of(again)) == 3
    finally:
        other.close()


def test_default_period(env):
    service = env["service"]
    assert service.default_period(dt.date(2026, 5, 3)) == (dt.date(2026, 1, 1), dt.date(2026, 5, 3))
    assert service.default_period(dt.date(2031, 7, 9)) == (dt.date(2031, 1, 1), dt.date(2031, 7, 9))


# ---- الشاشة ----------------------------------------------------------------
def make_screen(env, opened):
    from app.ui.screens.account_statement_screen import AccountStatementScreen

    class FakeWindow:
        def __init__(self, key):
            self.key = key
            self.loaded = None

        def load(self, entry_id):
            self.loaded = entry_id
            opened.append((self.key, entry_id))
            return False

    def opener(key):
        opened.append((key, None))
        return FakeWindow(key)

    screen = AccountStatementScreen(env["service"], opener=opener)
    screen.from_edit.setDate(screen.from_edit.date().__class__(2026, 1, 1))
    screen.to_edit.setDate(screen.to_edit.date().__class__(2026, 12, 31))
    return screen


def select(screen, kind, target_id):
    screen.kind_combo.setCurrentIndex(screen.kind_combo.findData(kind))
    screen.target_edit.set_value(target_id)


def test_screen(env, monkeypatch):
    db, ids, e = env["db"], env["ids"], env["e"]
    opened: list = []
    screen = make_screen(env, opened)
    try:
        assert not screen.new_button.isVisible() and not screen.search.isVisibleTo(screen)
        assert screen.export_button.isVisibleTo(screen) and screen.export_button.isEnabled()
        select(screen, "customer", ids["A"])
        rows = screen.table.rows
        assert [r["kind"] for r in rows] == ["prev", "line", "line", "total", "end"]
        assert screen.table.columnCount() == 6
        assert [screen.table.horizontalHeaderItem(c).text() for c in range(6)] == [
            "التاريخ", "المستند", "البيان", "مدين", "دائن", "الرصيد"]
        assert screen.table.item(0, 5).text() == "1,000.00 مدين" and screen.table.item(0, 3).text() == "1,000.00"
        assert screen.table.item(1, 1).text() == "JV-2026-00001" and screen.table.item(1, 5).text() == "1,500.00 مدين"
        assert screen.table.item(4, 5).text() == "1,200.00 مدين" and screen.table.columnSpan(0, 0) == 3
        assert "رصيد سابق" in screen.summary_label.text() and "1,200.00 مدين" in screen.summary_label.text()
        assert not screen.grouping_combo.isVisibleTo(screen)
        # دبل كليك ← القيد اليدوي
        screen._on_double_click(1, 2)
        assert opened[-2:] == [("journal_entries", None), ("journal_entries", e["E2"])]
        screen._on_double_click(0, 0)                                    # سطر الرصيد السابق مابيفتحش حاجة
        assert len(opened) == 2
        # الأرصدة الافتتاحية بتفتح شاشتها · المستند اللي لسه شاشته ماتعملتش ← رسالة
        screen.open_row({"kind": "line", "source_type": "opening_balance", "entry_id": 5})
        assert opened[-1] == ("opening_balances", None)
        screen.open_row({"kind": "line", "source_type": "sales_invoice", "entry_id": 5, "doc_type_name": "فاتورة بيع"})
        assert "لسه شاشته ماتعملتش" in env["shown"][-1]
        # الحساب الرئيسي: التجميع بيظهر ويتغيّر
        select(screen, "account", acc(db, "1101"))
        assert screen.grouping_combo.isVisibleTo(screen)
        assert screen.result["grouping"] == "main" and len(screen.table.rows) == 5
        screen.grouping_combo.setCurrentIndex(screen.grouping_combo.findData("sub"))
        assert screen.result["grouping"] == "sub" and screen.table.rows[0]["kind"] == "group"
        assert screen.table.columnSpan(0, 0) == 6
        select(screen, "treasury", 1)
        assert not screen.grouping_combo.isVisibleTo(screen) and screen.result["end"] == -1700
        assert screen.table.item(len(screen.table.rows) - 1, 5).text() == "1,700.00 دائن"
        # شرايح الفترة: «من أول السنة» = أول السنة المالية · «الشهر ده» = أول شهر «إلى»
        chip = {c.property("value"): c for c in screen.period_buttons.buttons()}
        assert chip["year"].isChecked()
        chip["month"].click()
        assert screen.from_edit.date().toPython() == dt.date(2026, 12, 1) and chip["month"].isChecked()
        chip["quarter"].click()
        assert screen.from_edit.date().toPython() == dt.date(2026, 10, 1)
        # تصدير إكسل (الكشف كله)
        path = os.path.join(os.environ.get("TEMP", "."), "stmt_test.xlsx")
        monkeypatch.setattr("app.ui.screens.account_statement_screen.QFileDialog.getSaveFileName",
                            lambda *a, **k: (path, ""))
        select(screen, "customer", ids["A"])
        screen.export_excel()
        from openpyxl import load_workbook

        ws = load_workbook(path).active
        assert ws.cell(1, 6).value == "الرصيد" and ws.cell(ws.max_row, 6).value == "1,200.00 مدين"
        os.remove(path)
    finally:
        screen.close()
        screen.deleteLater()


def test_menu_and_registry(env):
    from app.services import navigation_service as nav
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("acc", "account_statement")
    assert target.name_ar == "كشف الحساب ودفتر الأستاذ" and target.action_codes() == ("view",)
    assert "account_statement" in SCREEN_FACTORIES
    item = nav.find_item(nav.menu_tree(SESSION.can), "account_statement")
    assert item is not None and item["module"] == "acc"
    window = SCREEN_FACTORIES["account_statement"]
    assert callable(window)


def test_opens_from_menu_and_double_click_opens_entry(env, monkeypatch):
    """من القايمة في الواجهة الرئيسية ← الكشف · ودبل كليك ← شاشة «القيود اليومية» على القيد نفسه."""
    from app.services.journal_service import JournalService
    from app.ui import main_window as mw
    from app.ui.screens.account_statement_screen import AccountStatementScreen
    from app.ui.screens.journal_entries_screen import JournalEntriesScreen

    class FakeAuth:
        def logout(self):
            SESSION.clear()

    db, ids, e = env["db"], env["ids"], env["e"]
    monkeypatch.setattr(mw, "_info", lambda *_a: None)
    window = mw.MainWindow(FakeAuth(), photo_provider=lambda: None, factories={
        "account_statement": lambda: AccountStatementScreen(StatementService(db)),
        "journal_entries": lambda: JournalEntriesScreen(JournalService(db))})
    try:
        screen = window.open_screen("account_statement")
        assert isinstance(screen, AccountStatementScreen) and screen.isVisible()
        screen.from_edit.setDate(screen.from_edit.date().__class__(2026, 1, 1))
        screen.to_edit.setDate(screen.to_edit.date().__class__(2026, 12, 31))
        select(screen, "customer", ids["A"])
        line = next(r for r, row in enumerate(screen.table.rows) if row.get("entry_id") == e["E3"])
        screen._on_double_click(line, 0)
        journal = window.open_windows.get("journal_entries")
        assert isinstance(journal, JournalEntriesScreen)
        assert journal.current_id == e["E3"] and journal.current_record["entry_no"] == "JV-2026-00002"
    finally:
        for w in list(window.open_windows.values()):
            w.close()
        window.close()
        window.deleteLater()
