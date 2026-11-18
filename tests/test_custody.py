"""اختبارات «العهد» (0040): صرف عهدة · تسوية بسطور مصروفات وفلوس راجعة · التوزيع على العهد المختارة الأقدم الأول ·
الزيادة «ليه» و«سداد اللي ليه» · الحماية في القاعدة · الصلاحيات · جهازين · الشاشة والواجهات المنبثقة."""

from __future__ import annotations

import datetime as dt
import gc
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication, QDialog

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.custody_service import CustodyService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

D1, D2, D3 = dt.date(2026, 3, 1), dt.date(2026, 3, 10), dt.date(2026, 3, 20)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2026', '2026-01-01', '2026-12-31')")
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
    for seq, name, custody in ((1, "أحمد سامي", True), (2, "محمود عادل", True), (3, "كريم فتحي", False)):
        temp_db.execute("INSERT INTO party.employees (seq, name_ar, takes_custody) VALUES (%s, %s, %s)",
                        [seq, name, custody])
    yield {"db": temp_db, "shown": shown, "asked": asked, "cs": CustodyService(temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def emp(db, code="EMP-001"):
    return one(db, "SELECT id FROM party.employees WHERE code = %s", code)


def treasury(db, code="TRS-001"):
    return one(db, "SELECT id FROM cash.treasuries WHERE code = %s", code)


def bank(db, code="BNK-001-01"):
    return one(db, "SELECT id FROM cash.bank_accounts WHERE code = %s", code)


def xtype(db, name):
    return one(db, "SELECT id FROM exp.expense_types WHERE name_ar = %s", name)


def project(db, code="Itec-900"):
    center = one(db, "SELECT id FROM acc.cost_centers WHERE is_leaf AND is_active ORDER BY code LIMIT 1")
    return one(db, "INSERT INTO acc.projects (code, name, cost_center_id, currency_code) "
                   "VALUES (%s, 'تجربة', %s, 'EGP') RETURNING id", code, center)


def disb(db, amount="5000", d=D1, employee="EMP-001", **extra):
    out = {"disb_date": d, "employee_id": emp(db, employee), "purpose": "custody", "source_kind": "treasury",
           "treasury_id": treasury(db), "amount": amount, "description": "عهدة مصاريف"}
    out.update(extra)
    return out


def expense(db, amount, name="مواصلات", **extra):
    return {"line_kind": "expense", "expense_type_id": xtype(db, name), "amount": amount, **extra}


def ret(db, amount, code="TRS-001"):
    return {"line_kind": "return", "return_kind": "treasury", "treasury_id": treasury(db, code), "amount": amount}


def settle(db, lines, custody_ids, d=D3, employee="EMP-001"):
    return {"settle_date": d, "employee_id": emp(db, employee), "description": "تسوية", "lines": lines,
            "custody_ids": custody_ids}


def entry_lines(db, source_type, source_id):
    return [(r["code"], r["debit"], r["credit"], r["employee_id"]) for r in db.fetch_all(
        "SELECT a.code, l.debit, l.credit, l.employee_id FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
        "WHERE e.source_type = %s AND e.source_id = %s ORDER BY l.line_no", [source_type, source_id])]


def remaining(cs, db, employee="EMP-001"):
    return {r["disb_no"]: r["remaining"] for r in cs.open_custodies(emp(db, employee))}


# ---- القاعدة -----------------------------------------------------------------
def test_database_rules(env):
    db, cs = env["db"], env["cs"]
    t = treasury(db)
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة الأول"):
        db.execute("INSERT INTO cash.custody_disbursements (disb_date, fiscal_year_id, employee_id, source_kind, "
                   "treasury_id, amount, description, status, posted_at) VALUES (%s, 1, %s, 'treasury', %s, 10, "
                   "'x', 'posted', now())", [D1, emp(db), t])
    with pytest.raises(BusinessError, match="دور «عهدة»"):
        cs.save_disbursement(None, disb(db, employee="EMP-003"))
    a = cs.save_disbursement(None, disb(db), post=True)
    assert cs.get_disbursement(a)["disb_no"] == "CD-2026-00001"
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل — مايتعدّلش"):
        db.execute("UPDATE cash.custody_disbursements SET amount = 1 WHERE id = %s", [a])
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة وليه قيد"):
        db.execute("UPDATE cash.custody_disbursements SET status = 'draft' WHERE id = %s", [a])
    # الربط على عهدة لموظف تاني بيترفض · سطور التسوية المرحّلة ماتتغيّرش
    other = cs.save_disbursement(None, disb(db, employee="EMP-002"), post=True)
    s = cs.save_settlement(None, settle(db, [expense(db, "100")], []))
    with pytest.raises(psycopg.errors.RaiseException, match="مش بتاعة نفس الموظف"):
        db.execute("INSERT INTO cash.custody_settlement_links (settlement_id, disbursement_id) VALUES (%s, %s)",
                   [s, other])
    with pytest.raises(psycopg.errors.RaiseException, match="مش زي سطورها"):
        db.execute("UPDATE cash.custody_settlements SET expense_total = 5 WHERE id = %s", [s])
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة وعليها توزيع"):
        cs.save_settlement(s, settle(db, [expense(db, "100")], [a]))
        db.execute("UPDATE cash.custody_settlement_links SET allocated_amount = 50 WHERE settlement_id = %s", [s])
    cs.post_settlement(s)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة — سطورها"):
        db.execute("UPDATE cash.custody_settlement_lines SET amount = 1 WHERE settlement_id = %s", [s])
    with pytest.raises(psycopg.errors.RaiseException, match="اتسوّى منه"):
        db.execute("UPDATE cash.custody_disbursements SET status = 'draft' WHERE id = %s", [a])
    with pytest.raises(psycopg.errors.RaiseException, match="ماتتمسحش"):
        db.execute("DELETE FROM cash.custody_settlements WHERE id = %s", [s])
    # السنة ماتتقفلش وفيها مسودة
    cs.save_disbursement(None, disb(db))
    with pytest.raises(psycopg.errors.RaiseException, match="سند مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed' WHERE year_code = '2026'")


# ---- صرف العهدة ----------------------------------------------------------------
def test_disbursement_flow(env):
    db, cs = env["db"], env["cs"]
    pid = project(db)
    a = cs.save_disbursement(None, disb(db, amount="5,000", project_id=pid))
    rec = cs.get_disbursement(a)
    assert rec["status"] == "draft" and rec["disb_no"] is None and rec["project_id"] == pid
    preview = cs.preview_disbursement(disb(db))
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in preview] == [
        ("1107", Decimal("5000.00"), 0), ("110101", 0, Decimal("5000.00"))]
    assert cs.post_disbursement(a) == "CD-2026-00001"
    assert entry_lines(db, "custody_disb", a) == [("1107", Decimal("5000.00"), Decimal("0"), emp(db)),
                                                  ("110101", Decimal("0"), Decimal("5000.00"), None)]
    summary = cs.employee_summary(emp(db))
    assert summary["open_total"] == Decimal("5000") and summary["balance"] == Decimal("5000")
    # من البنك
    b = cs.save_disbursement(None, disb(db, amount="2000", source_kind="bank", treasury_id=None,
                                        bank_account_id=bank(db)), post=True)
    assert entry_lines(db, "custody_disb", b)[1][0] == "110201"
    assert "هيبقى -6,000.00" in (cs.funds_warning(disb(db, amount="1000")) or "")    # الخزنة فاضية
    cs.unpost_disbursement(b)
    assert cs.get_disbursement(b)["status"] == "draft" and entry_lines(db, "custody_disb", b) == []
    with pytest.raises(BusinessError, match="خد رقم رسمي"):
        cs.delete_disbursement(b)
    c = cs.save_disbursement(None, disb(db, amount="10"))
    cs.delete_disbursement(c)
    assert cs.get_disbursement(c) is None
    with pytest.raises(BusinessError, match="اكتب مبلغ"):
        cs.save_disbursement(None, disb(db, amount="0"))


# ---- التسوية ---------------------------------------------------------------------
def test_settlement_allocation_excess_and_payback(env):
    db, cs = env["db"], env["cs"]
    a = cs.save_disbursement(None, disb(db, amount="5000", d=D1), post=True)       # CD-1
    b = cs.save_disbursement(None, disb(db, amount="3000", d=D2), post=True)       # CD-2
    # مصروفات 6,000 + راجع 500 على العهدتين ← 5,000 من الأقدم و1,500 من التانية
    lines = [expense(db, "4000", "سولار"), expense(db, "2000", "مواصلات", description="مواصلات الموقع"),
             ret(db, "500")]
    s1 = cs.save_settlement(None, settle(db, lines, [b, a]))
    rec = cs.get_settlement(s1)
    assert rec["expense_total"] == Decimal("6000") and rec["return_total"] == Decimal("500")
    assert {lk["id"] for lk in rec["links"]} == {a, b} and all(lk["allocated_amount"] == 0 for lk in rec["links"])
    preview = cs.allocation_preview(emp(db), [a, b], Decimal("6500"), Decimal("500"))
    assert [r["allocated"] for r in preview["rows"]] == [Decimal("5000"), Decimal("1500")] and preview["excess"] == 0
    assert cs.post_settlement(s1) == "CS-2026-00001"
    rec = cs.get_settlement(s1)
    assert rec["allocated_total"] == Decimal("6500") and rec["excess_amount"] == 0
    assert remaining(cs, db) == {"CD-2026-00002": Decimal("1500.00")}
    lines_je = entry_lines(db, "custody_settle", s1)
    assert ("5304", Decimal("4000.00"), Decimal("0"), None) in lines_je
    assert ("110101", Decimal("500.00"), Decimal("0"), None) in lines_je
    assert lines_je[-1] == ("1107", Decimal("0"), Decimal("6500.00"), emp(db))
    # تسوية أكبر من الباقي (1,500) بـ 500 ← «ليه»
    s2 = cs.save_settlement(None, settle(db, [expense(db, "2000")], [b]), post=True)
    rec2 = cs.get_settlement(s2)
    assert rec2["allocated_total"] == Decimal("1500") and rec2["excess_amount"] == Decimal("500")
    summary = cs.employee_summary(emp(db))
    assert summary["open"] == [] and summary["credit_due"] == Decimal("500") and summary["balance"] == Decimal("-500")
    # العهدة اللي اتسوّت كلها مابتتختارش تاني · المرتجع مايعدّيش الموزّع
    with pytest.raises(BusinessError, match="اتسوّت كلها"):
        cs.save_settlement(None, settle(db, [expense(db, "10")], [a]))
    c = cs.save_disbursement(None, disb(db, amount="100", d=D2), post=True)
    with pytest.raises(BusinessError, match="الفلوس الراجعة"):
        cs.save_settlement(None, settle(db, [ret(db, "300")], [c]), post=True)
    # «سداد اللي ليه»: مايعدّيش 500 · وبعده الزيادة ماتتلغاش
    payback = disb(db, amount="600", purpose="payback")
    with pytest.raises(BusinessError, match="500.00 بس"):
        cs.save_disbursement(None, payback)
    p = cs.save_disbursement(None, {**payback, "amount": "500"}, post=True)
    assert cs.employee_summary(emp(db))["credit_due"] == 0
    assert cs.repository.ledger_balance(emp(db)) == Decimal("100")                # العهدة c بس
    with pytest.raises(BusinessError, match="اتسددت للموظف"):
        cs.unpost_settlement(s2)
    cs.unpost_disbursement(p)
    cs.unpost_settlement(s2)
    assert cs.get_settlement(s2)["status"] == "draft" and remaining(cs, db)["CD-2026-00002"] == Decimal("1500.00")
    # صرف عهدة اتسوّى منها مايتلغاش ترحيله
    with pytest.raises(BusinessError, match="اتسوّى منه بالتسوية CS-2026-00001"):
        cs.unpost_disbursement(a)
    # موظف مالوش عهدة ← التسوية كلها «ليه»
    s3 = cs.save_settlement(None, settle(db, [expense(db, "250")], [], employee="EMP-002"), post=True)
    assert cs.get_settlement(s3)["excess_amount"] == Decimal("250")


def test_expense_line_rules(env):
    db, cs = env["db"], env["cs"]
    # «مصاريف نقل بضاعة» على 5301 «نقل ومشال» ← لازم مركز تكلفة ← لازم أمر توريد
    with pytest.raises(BusinessError, match="مركز تكلفة"):
        cs.check_line(expense(db, "100", "مصاريف نقل بضاعة"))
    pid = project(db)
    line = cs.check_line(expense(db, "100", "مصاريف نقل بضاعة", project_id=pid))
    assert line["project_id"] == pid and line["amount"] == Decimal("100")
    a = cs.save_disbursement(None, disb(db, amount="1000"), post=True)
    s = cs.save_settlement(None, settle(db, [expense(db, "100", "مصاريف نقل بضاعة", project_id=pid)], [a]),
                           post=True)
    row = db.fetch_one("SELECT l.project_id, l.cost_center_id FROM acc.journal_lines l JOIN acc.journal_entries e "
                       "ON e.id = l.entry_id WHERE e.source_type = 'custody_settle' AND e.source_id = %s "
                       "AND l.debit > 0", [s])
    assert row["project_id"] == pid and row["cost_center_id"] is not None
    with pytest.raises(BusinessError, match="اختار نوع المصروف"):
        cs.check_line({"line_kind": "expense", "amount": "5"})
    with pytest.raises(BusinessError, match="التسوية فاضية"):
        cs.save_settlement(None, settle(db, [], [a]))


def test_permissions(env):
    db, cs = env["db"], env["cs"]
    admin_id = one(db, "SELECT id FROM sec.app_users WHERE username = %s", DEFAULT_ADMIN_USERNAME)
    perm = "hr.custody"
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.create", f"{perm}.edit", f"{perm}.save"})
    a = cs.save_disbursement(None, disb(db))
    for call in (lambda: cs.post_disbursement(a), lambda: cs.save_disbursement(a, disb(db), post=True),
                 lambda: cs.unpost_disbursement(a), lambda: cs.delete_disbursement(a),
                 lambda: cs.save_settlement(None, settle(db, [expense(db, "5")], []), post=True)):
        with pytest.raises(PermissionDenied):
            call()
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.post", f"{perm}.unpost"})
    cs.post_disbursement(a)
    cs.unpost_disbursement(a)
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE %s AND is_active", [f"{perm}.%"])}
    assert codes == {f"{perm}.{x}" for x in ("view", "create", "edit", "save", "delete", "export", "post", "unpost")}


def test_two_devices(env):
    """جهازين: تعديل نفس المسودة ← التاني بيترفض · تسويتين على نفس العهدة في نفس اللحظة ← العهدة ماتتسوّاش مرتين."""
    db = env["db"]
    s1, s2 = CustodyService(db), CustodyService(Database(db.settings))
    try:
        a = s1.save_disbursement(None, disb(db, amount="1000"), post=True)
        x = s1.save_settlement(None, settle(db, [expense(db, "1000")], [a]))
        seen1, seen2 = s1.get_settlement(x), s2.get_settlement(x)
        s2.save_settlement(x, settle(db, [expense(db, "900")], [a]), seen2["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.save_settlement(x, settle(db, [expense(db, "800")], [a]), seen1["row_version"])
        y = s2.save_settlement(None, settle(db, [expense(db, "1000")], [a]))
        results: dict[str, object] = {}

        def worker():
            try:
                results["no"] = s2.post_settlement(y)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        thread = threading.Thread(target=worker)
        with db.atomic() as tx:
            s1.repository.lock_employee(emp(db), tx)                  # جهاز ماسك الموظف (بيرحّل تسوية x)
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()                                  # التاني مستني
        thread.join(10)
        assert results.get("no") == "CS-2026-00001", results
        # x دلوقتي: العهدة باقي منها 0 ← رسالة واضحة
        with pytest.raises(BusinessError, match="اتسوّت كلها"):
            s1.post_settlement(x)
        used = one(db, "SELECT sum(allocated_amount) FROM cash.custody_settlement_links WHERE disbursement_id = %s", a)
        assert used == Decimal("1000.00")
    finally:
        s2.db.close()


# ---- الشاشة ----------------------------------------------------------------------
def plain(text):
    return text.replace("⁦", "").replace("⁩", "")


def make_screen(db):
    from app.ui.screens.custody_screen import CustodyScreen

    screen = CustodyScreen(CustodyService(db))
    screen.period_from.setDate(screen.period_from.date().addYears(-5))
    screen.period_to.setDate(screen.period_to.date().addYears(5))
    return screen


def test_screen(env, monkeypatch):
    from app.ui.screens.custody_screen import CustodyPickerDialog, LineDialog

    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    plan: list = []

    def fake_exec(dialog):
        """بدل ما الواجهة المنبثقة تستنى المستخدم: الخطوة اللي عليها الدور في ``plan``."""
        step = plan.pop(0)
        step(dialog)
        return dialog.result() == QDialog.Accepted

    monkeypatch.setattr(screen, "_exec", fake_exec)
    try:
        assert screen.windowTitle().endswith("العهد") and screen.kind_tabs["disb"].isChecked()
        # 1) صرف عهدتين
        for amount, day in (("2000", "2026-03-01"), ("1500", "2026-03-05")):
            screen.new_record()
            assert screen.mode == "new" and screen.source_edit.currentData() == treasury(db)
            assert screen.employee_edit.currentData() is None
            screen.employee_edit.set_value(emp(db))
            screen.date_edit.setDate(screen.date_edit.date().fromString(day, "yyyy-MM-dd"))
            screen.amount_edit.setText(amount)
            screen.description.setText("عهدة موقع")
            screen._refresh_side()
            assert screen.entry_table.rowCount() == 2 and "متوازن" in screen.entry_label.text()
            screen.post_doc()
            assert screen.current_record["status"] == "posted"
        assert screen.current_record["disb_no"] == "CD-2026-00002" and len(screen.table.rows) == 2
        screen._refresh_side()
        assert screen.open_table.rowCount() == 2 and "3,500.00" in plain(screen.card_label.text())
        # 2) تسوية: الموظف ← واجهة العهد المفتوحة (يعلّم على الاتنين) ← بند مصروف + سطر تاني ← فلوس راجعة
        screen.kind_tabs["settle"].click()
        assert screen.kind == "settle" and screen.stack.currentIndex() == 1 and screen.table.rows == []
        screen.new_record()

        def pick(dialog):
            assert isinstance(dialog, CustodyPickerDialog) and len(dialog.checks) == 2
            for check in dialog.checks:
                check.setChecked(True)
            dialog.accept()

        plan.append(pick)
        screen.employee_edit.set_value(emp(db))
        assert len(screen.custody_ids) == 2 and "CD-2026-00001" in plain(screen.custody_label.text())

        def two_expenses(dialog):
            assert isinstance(dialog, LineDialog) and dialog.kind == "expense"
            dialog.type_edit.set_value(xtype(db, "سولار"))
            dialog.amount_edit.setText("2500")
            dialog.description.setText("سولار")
            dialog.add_more()                                      # «إضافة وسطر جديد»
            assert dialog.amount_edit.text() == "" and len(dialog.lines) == 1 and dialog.isVisible() is False
            dialog.type_edit.set_value(xtype(db, "مواصلات"))
            dialog.amount_edit.setText("700")
            dialog.add_close()

        plan.append(two_expenses)
        screen.add_line("expense")
        assert len(screen.lines) == 2 and screen.lines_table.rowCount() == 2

        def give_back(dialog):
            assert dialog.kind == "return" and dialog.source_edit.currentData() == treasury(db)
            dialog.amount_edit.setText("100")
            dialog.add_close()

        plan.append(give_back)
        screen.add_line("return")
        screen._refresh_side()
        text = plain(screen.totals_label.text())
        assert "3,200.00" in text and "100.00" in text and "3,300.00" in text and "ليه" not in text
        assert screen.entry_table.rowCount() == 4 and "متوازن" in screen.entry_label.text()
        assert screen.open_table.item(0, 4).text().startswith("2,000.00")      # الأقدم الأول ويتقفل
        # سطر غلط في الواجهة: من غير نوع مصروف ← رسالة وماتقفلش
        def bad(dialog):
            dialog.amount_edit.setText("50")
            dialog.add_close()
            assert "نوع المصروف" in dialog.error_label.text() and dialog.result() != QDialog.Accepted
            dialog.reject()

        plan.append(bad)
        screen.add_line("expense")
        assert len(screen.lines) == 3
        screen.save()
        assert shown[-1] == "saved" and screen.current_record["status"] == "draft"
        assert len(screen.current_record["links"]) == 2 and len(screen.current_record["lines"]) == 3
        screen.post_doc()
        rec = screen.current_record
        assert rec["settle_no"] == "CS-2026-00001" and rec["excess_amount"] == 0
        assert rec["allocated_total"] == Decimal("3300.00")
        assert not screen.add_expense_button.isEnabled() and screen.unpost_button.isEnabled()
        screen._refresh_side()
        assert screen.entry_box.title() == "القيد المرحّل" and "اتسوّى من" in plain(screen.custody_label.text())
        # 3) تعديل بعد إلغاء الترحيل: حذف سطر المرتجع وتكبير الإجمالي ← «ليه»
        screen.unpost_doc()
        assert screen.current_record["status"] == "draft"
        screen.edit_record()
        screen.lines_table.setCurrentCell(2, 0)
        screen.delete_line()
        assert [ln["line_kind"] for ln in screen.lines] == ["expense", "expense"]

        def bigger(dialog):
            dialog.type_edit.set_value(xtype(db, "إكراميات"))
            dialog.amount_edit.setText("400")
            dialog.add_close()

        plan.append(bigger)
        screen.add_line("expense")
        screen._refresh_side()
        assert "ليه عندنا" in plain(screen.totals_label.text()) and "100.00" in plain(screen.totals_label.text())
        env["asked"].clear()
        screen.post_doc()
        assert any("ليه" in a for a in env["asked"])
        assert screen.current_record["excess_amount"] == Decimal("100.00")
        # 4) صرف «سداد اللي ليه»
        screen.kind_tabs["disb"].click()
        screen.new_record()
        screen.employee_edit.set_value(emp(db))
        screen.purpose_combo.setCurrentIndex(1)
        assert screen.amount_edit.text() == "100.00" and not screen.project_edit.isEnabled()
        screen.post_doc()
        assert screen.current_record["purpose"] == "payback" and screen.current_record["status"] == "posted"
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    t = reg.find_target("hr", "custody")
    assert t.name_ar == "العهد" and "custody" in SCREEN_FACTORIES
    assert set(t.action_codes()) == {"view", "create", "edit", "save", "delete", "export", "post", "unpost"}
