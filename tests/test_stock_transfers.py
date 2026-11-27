"""اختبارات «التحويل بين المخازن» (0046 + stock_transfer_service + الشاشة — النموذج 8):
الحساب والمفتاح الجديد · مسودة ← ترحيل برقم TR وحركتين في دفتر المخزون لكل سطر · بين مخزنين حقيقيين من غير قيد ·
فاتورة ↔ بيان ومايعدّيش رصيد الشركة · الوهمي بقيد مصروف في الاتجاهين · الرصيد الناقص وصلاحية «من غير رصيد» ·
إلغاء الترحيل والرقم الثابت والمسح · الوحدات والتجميع · حماية القاعدة · جهازين · لوحة المراجعة · قفل السنة · الشاشة."""

from __future__ import annotations

import datetime as dt
import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.permission_registry import SECTIONS
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService, StockLine
from app.services.stock_transfer_service import StockTransferService
from app.ui.common import messages

D1, D2, D3 = dt.date(2026, 3, 1), dt.date(2026, 3, 2), dt.date(2026, 3, 3)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    db = temp_db
    db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
               "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    AuthService(db).seed_admin()
    PermissionsSyncService(db).sync()
    SESSION.clear()
    assert AuthService(db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _text: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    env = {"db": db, "shown": shown, "service": StockTransferService(db), "ledger": StockLedgerService(db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "virtual": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_virtual ORDER BY seq LIMIT 1"),
           "item": item(db, "كابل نحاس"), "item2": item(db, "مفتاح")}
    env["branch"] = db.fetch_value("INSERT INTO inv.warehouses (seq, name_ar) VALUES "
                                   "((SELECT max(seq) + 1 FROM inv.warehouses), 'مخزن الفرع') RETURNING id")
    yield env
    SESSION.clear()
    gc.collect()


def item(db, name, service=False):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id, is_service) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14'), %s) "
        "RETURNING id", [name, service])


def buy(env, doc_id, qty, cost, wh=None, kind="invoice", it=None, d=D1):
    with env["db"].atomic() as tx:
        env["ledger"].post(tx, doc_type="purchase_invoice", source_id=doc_id, source_no=f"PI-{doc_id}",
                           movement_date=d, lines=[StockLine(it or env["item"], wh or env["main"], qty, kind,
                                                             unit_cost=cost)])


def sell(env, doc_id, qty, wh, kind="invoice", it=None, d=D3):
    with env["db"].atomic() as tx:
        env["ledger"].post(tx, doc_type="sales_invoice", source_id=doc_id, source_no=f"SI-{doc_id}",
                           movement_date=d, lines=[StockLine(it or env["item"], wh, -Decimal(qty), kind)],
                           allow_negative=True)


def data(env, lines, frm=None, to=None, fk="invoice", tk="invoice", d=D2, note=None):
    return {"transfer_date": d, "from_warehouse_id": frm or env["main"], "to_warehouse_id": to or env["branch"],
            "from_kind": fk, "to_kind": tk, "description": note,
            "lines": [{"item_id": ln[0], "qty": ln[1], "unit_id": ln[2] if len(ln) > 2 else None} for ln in lines]}


def qty_at(env, wh, kind="invoice", it=None):
    return env["db"].fetch_value("SELECT coalesce(sum(qty), 0) FROM inv.stock_balances WHERE item_id = %s "
                                 "AND warehouse_id = %s AND stock_kind = %s", [it or env["item"], wh, kind])


def entry(env, tid):
    return env["db"].fetch_one("SELECT status, total_debit FROM acc.journal_entries "
                               "WHERE source_type = 'stock_transfer' AND source_id = %s", [tid])


def no_negative(monkeypatch):
    real = SESSION.can
    monkeypatch.setattr(SESSION, "can", lambda code: code != "inv.stock_transfers.negative" and real(code))


# ---- السكريبت ------------------------------------------------------------------
def test_migration_account_key_and_permissions(env):
    db = env["db"]
    acc = db.fetch_one("SELECT a.code, a.name_ar, a.account_type FROM acc.default_accounts d "
                       "JOIN acc.accounts a ON a.id = d.account_id WHERE d.key = 'virtual_stock'")
    assert acc["code"].startswith("52") and len(acc["code"]) == 4 and acc["account_type"] == "expense"
    assert acc["name_ar"] == "بضاعة المخزن الوهمي"
    order = [r["key"] for r in db.fetch_all("SELECT key FROM acc.default_accounts WHERE group_code = 'inv' "
                                            "ORDER BY sort_order")]
    assert order.index("virtual_stock") == order.index("cost_var") + 1
    assert db.fetch_value("SELECT prefix FROM core.document_types WHERE code = 'stock_transfer'") == "TR"
    target = next(t for s in SECTIONS if s.id == "inv" for t in s.targets()
                  if t.code == "stock_transfers")
    assert {"post", "unpost", "negative", "delete"} <= set(target.actions)
    assert db.fetch_value("SELECT count(*) FROM sec.permissions "
                          "WHERE permission_code = 'inv.stock_transfers.negative'") == 1


# ---- بين مخزنين حقيقيين -----------------------------------------------------------
def test_post_between_real_warehouses_no_entry(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 100, "10")
    tid = s.save_draft(None, data(env, [(env["item"], "30")], note="طلب الفرع"))
    rec = s.get_transfer(tid)
    assert rec["status"] == "draft" and rec["transfer_no"] is None and rec["lines"][0]["base_qty"] == 30
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'transfer'") == 0
    no = s.post(tid, expected_version=rec["row_version"])
    assert no.startswith("TR-2026-")
    assert (qty_at(env, env["main"]), qty_at(env, env["branch"])) == (70, 30)
    moves = db.fetch_all("SELECT qty, value FROM inv.stock_movements WHERE source_type = 'transfer' "
                         "AND source_id = %s ORDER BY line_no", [tid])
    assert [(m["qty"], m["value"]) for m in moves] == [(-30, Decimal("-300.00")), (30, Decimal("300.00"))]
    assert entry(env, tid) is None                       # نفس حساب المخزون — مفيش قيد
    assert env["ledger"].average_cost(env["item"]) == Decimal("10")
    assert s.get_transfer(tid)["route_type"] == "wh"


def test_draft_edit_lines_units_and_merge(env):
    s, db = env["service"], env["db"]
    carton = db.fetch_value("SELECT id FROM inv.units WHERE NOT is_default ORDER BY id LIMIT 1")
    db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 24)", [env["item"], carton])
    buy(env, 1, 200, "5")
    tid = s.save_draft(None, data(env, [(env["item"], "2", carton), (env["item"], "1", carton)]))
    line = s.get_transfer(tid)["lines"][0]
    assert (line["unit_id"], line["qty"], line["base_qty"]) == (carton, 3, 72)     # نفس الوحدة ← اتجمعت
    rec = s.get_transfer(tid)
    s.save_draft(tid, data(env, [(env["item"], "1", carton), (env["item"], "6")]), expected_version=rec["row_version"])
    lines = s.get_transfer(tid)["lines"]
    assert len(lines) == 1 and lines[0]["base_qty"] == 30 and lines[0]["factor"] == 1   # وحدات مختلفة ← بالأساسية
    with pytest.raises(BusinessError, match="مش من وحدات"):
        s.save_draft(None, data(env, [(env["item2"], "1", carton)]))
    with pytest.raises(BusinessError, match="أكبر من صفر"):
        s.save_draft(None, data(env, [(env["item"], "0")]))
    with pytest.raises(BusinessError, match="نفس المخزن ونفس النوع"):
        s.save_draft(None, data(env, [(env["item"], "1")], to=env["main"]))
    with pytest.raises(BusinessError, match="ضيف صنف"):
        s.save_draft(None, data(env, []))
    with pytest.raises(BusinessError, match="خدمي"):
        s.save_draft(None, data(env, [(item(db, "تركيب", service=True), "1")]))


# ---- الرصيد الناقص والصلاحية ---------------------------------------------------------
def test_shortage_needs_permission(env, monkeypatch):
    s = env["service"]
    buy(env, 1, 10, "10")
    tid = s.save_draft(None, data(env, [(env["item"], "15")]))
    no_negative(monkeypatch)
    review = s.review(data(env, [(env["item"], "15")]))
    assert not review["ready"] and review["lines"][0]["error"].startswith("ناقص 5")
    assert any(c[0] == "no" and "من غير رصيد" in c[1] for c in review["checks"])
    with pytest.raises(NegativeStockError):
        s.post(tid)
    assert s.get_transfer(tid)["status"] == "draft" and s.get_transfer(tid)["transfer_no"] is None
    monkeypatch.undo()
    review = s.review(data(env, [(env["item"], "15")]))
    assert review["ready"] and any(c[0] == "warn" for c in review["checks"])
    s.post(tid)
    assert qty_at(env, env["main"]) == -5 and qty_at(env, env["branch"]) == 15


def test_kind_change_cannot_exceed_company(env):
    s = env["service"]
    buy(env, 1, 10, "10")
    buy(env, 2, 50, "8", wh=env["branch"])
    # نفس المخزن من فاتورة لبيان
    tid = s.save_draft(None, data(env, [(env["item"], "4")], to=env["main"], tk="statement"))
    s.post(tid)
    assert qty_at(env, env["main"], "statement") == 4 and s.get_transfer(tid)["route_type"] == "kind"
    assert entry(env, tid) is None
    # أكبر من رصيد «فاتورة» في الشركة (56) ← ممنوع حتى بالصلاحية (الأدمن)
    review = s.review(data(env, [(env["item"], "60")], to=env["main"], tk="statement"))
    assert not review["ready"] and "الشركة" in review["lines"][0]["error"]
    tid2 = s.save_draft(None, data(env, [(env["item"], "60")], to=env["main"], tk="statement"))
    with pytest.raises(BusinessError, match="الشركة"):
        s.post(tid2)


# ---- الوهمي --------------------------------------------------------------------
def test_virtual_entries_both_ways(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 10, "20")
    tid = s.save_draft(None, data(env, [(env["item"], "4")], to=env["virtual"]))
    review = s.review(s_data := data(env, [(env["item"], "4")], to=env["virtual"]))
    assert review["entry"] and review["value"] == Decimal("80.00") and review["route_type"] == "virtual"
    s.post(tid)
    e = entry(env, tid)
    assert e["status"] == "posted" and e["total_debit"] == Decimal("80.00")
    rows = db.fetch_all("SELECT a.role_code, l.debit, l.credit FROM acc.journal_lines l "
                        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
                        "WHERE e.source_type = 'stock_transfer' AND e.source_id = %s ORDER BY l.debit DESC", [tid])
    assert [(r["role_code"], r["debit"], r["credit"]) for r in rows] == [
        ("virtual_stock", Decimal("80.00"), Decimal("0")), ("inventory", Decimal("0"), Decimal("80.00"))]
    assert qty_at(env, env["virtual"]) == 4 and qty_at(env, env["main"]) == 6
    assert s_data["lines"]
    # وهمي ← حقيقي: دخول بآخر متوسط والقيد بالعكس
    back = s.save_draft(None, data(env, [(env["item"], "3")], frm=env["virtual"], to=env["branch"], d=D3))
    s.post(back)
    e = entry(env, back)
    assert e["total_debit"] == Decimal("60.00")
    rows = db.fetch_all("SELECT a.role_code, l.debit FROM acc.journal_lines l JOIN acc.journal_entries e "
                        "ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
                        "WHERE e.source_type = 'stock_transfer' AND e.source_id = %s AND l.debit > 0", [back])
    assert [r["role_code"] for r in rows] == ["inventory"]
    assert qty_at(env, env["branch"]) == 3 and qty_at(env, env["virtual"]) == 1


# ---- إلغاء الترحيل والمسح --------------------------------------------------------
def test_unpost_keeps_number_and_delete_rules(env, monkeypatch):
    s, db = env["service"], env["db"]
    buy(env, 1, 10, "20")
    tid = s.save_draft(None, data(env, [(env["item"], "4")], to=env["virtual"]))
    no = s.post(tid)
    s.unpost(tid)
    rec = s.get_transfer(tid)
    assert rec["status"] == "draft" and rec["transfer_no"] == no and entry(env, tid) is None
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'transfer'") == 0
    assert qty_at(env, env["main"]) == 10
    with pytest.raises(BusinessError, match="رقم رسمي"):
        s.delete_transfer(tid)
    assert s.post(tid) == no                               # نفس الرقم
    draft = s.save_draft(None, data(env, [(env["item"], "1")]))
    s.delete_transfer(draft)
    assert s.get_transfer(draft) is None
    # إلغاء ترحيل هيخلّي «إلى» بالسالب (اتصرف منه بعد التحويل) ← محتاج «من غير رصيد»
    t2 = s.save_draft(None, data(env, [(env["item"], "5")]))
    s.post(t2)
    sell(env, 9, "5", env["branch"])
    no_negative(monkeypatch)
    with pytest.raises(NegativeStockError):
        s.unpost(t2)
    assert s.get_transfer(t2)["status"] == "posted"


# ---- حماية القاعدة ---------------------------------------------------------------
def test_database_guards(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 10, "10")
    tid = s.save_draft(None, data(env, [(env["item"], "2")]))
    s.post(tid)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        db.execute("UPDATE inv.stock_transfer_lines SET qty = 1 WHERE transfer_id = %s", [tid])
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        db.execute("UPDATE inv.stock_transfers SET description = 'x' WHERE id = %s", [tid])
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO inv.stock_transfers (transfer_date, fiscal_year_id, from_warehouse_id, "
                   "to_warehouse_id) VALUES ('2026-03-01', 1, %s, %s)", [env["main"], env["main"]])
    draft = s.save_draft(None, data(env, [(env["item"], "1")]))
    # الترحيل من غير حركات المخزون ← بيترفض آخر الـ transaction
    with pytest.raises(psycopg.errors.RaiseException, match="حركات المخزون"):
        db.execute("UPDATE inv.stock_transfers SET status = 'posted' WHERE id = %s", [draft])
    unit = db.fetch_value("SELECT id FROM inv.units WHERE NOT is_default ORDER BY id LIMIT 1")
    with pytest.raises(psycopg.errors.RaiseException, match="مش من وحدات"):
        db.execute("UPDATE inv.stock_transfer_lines SET unit_id = %s WHERE transfer_id = %s", [unit, draft])
    # مخزن موقوف
    db.execute("UPDATE inv.warehouses SET is_active = false WHERE id = %s", [env["branch"]])
    with pytest.raises(BusinessError, match="موقوف"):
        s.post(draft)


def test_concurrent_edit_and_year_close(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 10, "10")
    tid = s.save_draft(None, data(env, [(env["item"], "2")]))
    v = s.get_transfer(tid)["row_version"]
    s.save_draft(tid, data(env, [(env["item"], "3")]), expected_version=v)        # جهاز 1
    with pytest.raises(ConcurrentEditError):
        s.save_draft(tid, data(env, [(env["item"], "4")]), expected_version=v)     # جهاز 2 بالإصدار القديم
    with pytest.raises(ConcurrentEditError):
        s.post(tid, expected_version=v)
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed' WHERE year_code = '2026'")


def test_two_devices_post_same_item(env, base_settings, monkeypatch):
    """جهازين بيحوّلوا آخر 10 من نفس الصنف في نفس اللحظة ← واحد بس يعدّي (التاني رصيده مش مكفي)."""
    from dataclasses import replace
    import threading

    s, db = env["service"], env["db"]
    buy(env, 1, 10, "10")
    a = s.save_draft(None, data(env, [(env["item"], "10")]))
    b = s.save_draft(None, data(env, [(env["item"], "10")], to=env["virtual"]))
    no_negative(monkeypatch)
    other = Database(replace(base_settings, db_name=db.settings.db_name))
    results: list[str] = []

    def run(service, tid):
        try:
            service.post(tid)
            results.append("ok")
        except NegativeStockError:
            results.append("short")

    try:
        threads = [threading.Thread(target=run, args=(s, a)),
                   threading.Thread(target=run, args=(StockTransferService(other), b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        other.close()
    assert sorted(results) == ["ok", "short"]
    assert qty_at(env, env["main"]) == 0


# ---- القائمة واللوحة -----------------------------------------------------------------
def test_list_count_and_posted_review(env):
    s = env["service"]
    buy(env, 1, 10, "10")
    tid = s.save_draft(None, data(env, [(env["item"], "2")], note="طلب الفرع"))
    s.post(tid)
    s.save_draft(None, data(env, [(env["item2"], "1")], to=env["virtual"]))
    rows = s.list_transfers(D1, D3)
    assert len(rows) == 2 and {r["route_type"] for r in rows} == {"wh", "virtual"}
    assert s.count_transfers(D1, D3) == {"total": 2, "drafts": 1, "posted": 1}
    assert len(s.list_transfers(D1, D3, keyword="كابل")) == 1
    assert len(s.list_transfers(D1, D3, keyword=s.get_transfer(tid)["transfer_no"])) == 1
    assert len(s.list_transfers(D1, D3, warehouse_id=env["virtual"])) == 1
    rec = s.get_transfer(tid)
    review = s.review({**data(env, [(env["item"], "2")]), "lines": [
        {"item_id": ln["item_id"], "qty": ln["qty"], "unit_id": ln["unit_id"]} for ln in rec["lines"]]}, tid)
    assert review["lines"][0]["value"] == Decimal("20.00") and review["lines"][0]["error"] == ""


# ---- الشاشة ------------------------------------------------------------------
def test_screen_new_review_post_unpost(env, monkeypatch):
    from app.ui.screens.stock_transfers_screen import C_AVAIL, C_ITEM, C_QTY, StockTransfersScreen

    s = env["service"]
    no_negative(monkeypatch)
    buy(env, 1, 10, "10")
    screen = StockTransfersScreen(service=s)
    screen._from, screen._to = D1, D3
    screen.new_record()
    assert screen.mode == "new" and screen.from_wh.currentData() == env["main"]
    assert screen.to_wh.currentData() not in (None, env["main"])
    screen.to_wh.setCurrentIndex(screen.to_wh.findData(env["branch"]))
    screen.date_edit.setDate(screen.date_edit.date().fromString("02/03/2026", "dd/MM/yyyy"))
    screen.lines_table.cellWidget(0, C_ITEM).set_value(env["item"])
    screen.lines_table.cellWidget(0, C_QTY).setText("12")
    screen._refresh_review()
    assert not screen._review["ready"] and "فيه مشاكل" in screen.review_box.title()
    assert screen.lines_table.item(0, C_AVAIL).text() == "10"
    screen.lines_table.cellWidget(0, C_QTY).setText("4")
    screen._refresh_review()
    assert screen._review["ready"] and "جاهز" in screen.review_box.title()
    assert "مفيش قيد" in screen.entry_label.text()
    screen.post_transfer()
    rec = screen.current_record
    assert rec["status"] == "posted" and rec["transfer_no"].startswith("TR-")
    assert qty_at(env, env["branch"]) == 4
    screen._refresh_review()
    assert screen.effect_box.title() == "الرصيد دلوقتي" and screen.unpost_button.isEnabled()
    screen.unpost_transfer()
    assert screen.current_record["status"] == "draft" and qty_at(env, env["branch"]) == 0
    assert len(screen.table.rows) == 1
    screen.close()
    screen.deleteLater()


def test_screen_virtual_entry_preview_and_save_draft(env):
    from app.ui.screens.stock_transfers_screen import C_ITEM, C_QTY, StockTransfersScreen

    buy(env, 1, 10, "20")
    screen = StockTransfersScreen(service=env["service"])
    screen.new_record()
    screen.to_wh.setCurrentIndex(screen.to_wh.findData(env["virtual"]))
    screen.lines_table.cellWidget(0, C_ITEM).set_value(env["item"])
    screen.lines_table.cellWidget(0, C_QTY).setText("2")
    screen._add_line()
    screen._refresh_review()
    assert screen.entry_table.rowCount() == 2 and screen.type_label.text() == "للوهمي / من الوهمي"
    screen.save()
    assert screen.mode == "view" and screen.current_record["status"] == "draft"
    assert len(screen.current_record["lines"]) == 1                         # السطر الفاضي اتشال
    screen.close()
    screen.deleteLater()
