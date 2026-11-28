"""اختبارات «مرتجع المشتريات» (0049 + purchase_return_service + الشاشة — النموذج 1):
الخروج بسعر الشراء (given_out) والمتوسط من الباقي · السعر الافتراضي من الفاتورة بعد الخصومات ويتغيّر · الكمية ≤ اللي
اتشرى ناقص اللي رجع · أكتر من فاتورة + سطر حر · القيد (المورد / المخزون / ض.ق.م / فروق التكلفة) · نقدي = قيد قبض ·
الدولار بسعر الفاتورة · البضاعة اللي اتصرفت و«من غير رصيد» · الفاتورة مايتلغيش ترحيلها وعليها مرتجع · جهازين ·
أسباب المرتجع · قفل السنة · الشاشة."""

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
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.purchase_invoice_service import PurchaseInvoiceService
from app.services.purchase_return_service import PurchaseReturnService, calculate
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService
from app.ui.common import messages

D1, D2, D3 = dt.date(2026, 3, 1), dt.date(2026, 3, 2), dt.date(2026, 3, 3)
D_ = Decimal


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
    group = db.fetch_value("SELECT group_id FROM party.suppliers WHERE seq = 0")
    env = {"db": db, "shown": shown, "inv": PurchaseInvoiceService(db), "service": PurchaseReturnService(db),
           "ledger": StockLedgerService(db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "virtual": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_virtual ORDER BY seq LIMIT 1"),
           "item": item(db, "كابل نحاس"), "item2": item(db, "لمبة LED"), "svc": item(db, "تركيب ومعايرة", True),
           "vat14": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT14'"),
           "treasury": db.fetch_value("SELECT id FROM cash.treasuries WHERE code = 'TRS-001'"),
           "sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code, payment_terms_days) "
                                 "VALUES (1, 'شركة النور', %s, 'EGP', 45) RETURNING id", [group]),
           "sup2": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code) "
                                  "VALUES (2, 'المصرية', %s, 'EGP') RETURNING id", [group]),
           "usd_sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code, "
                                     "vat_tax_code_id) VALUES (3, 'Global Controls', %s, 'USD', "
                                     "(SELECT id FROM acc.tax_codes WHERE code = 'EXEMPT')) RETURNING id", [group])}
    env["branch"] = db.fetch_value("INSERT INTO inv.warehouses (seq, name_ar) VALUES "
                                   "((SELECT max(seq) + 1 FROM inv.warehouses), 'مخزن الفرع') RETURNING id")
    db.execute("INSERT INTO core.exchange_rates (currency_id, rate_date, rate) "
               "SELECT id, '2026-01-01', 48.5 FROM core.currencies WHERE code = 'USD'")
    yield env
    SESSION.clear()
    gc.collect()


def item(db, name, service=False):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id, is_service) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14'), %s) "
        "RETURNING id", [name, service])


def buy(env, lines, sup=None, d=D1, **kw):
    """فاتورة شراء مرحّلة ← (id، سطورها)."""
    payload = {"invoice_date": d, "supplier_id": sup or env["sup"], "warehouse_id": env["main"],
               "currency_code": kw.pop("currency_code", "EGP"), "due_date": d + dt.timedelta(days=45),
               "lines": [{"item_id": it, "qty": q, "unit_price": p, "tax_code_id": env["vat14"]} for it, q, p in lines],
               **kw}
    iid = env["inv"].save_draft(None, payload, post=True)
    return iid, env["inv"].get_invoice(iid)["lines"]


def rline(src=None, qty="1", **kw):
    return {"source_line_id": src["id"] if src else None, "qty": qty, **kw}


def ret(env, lines, sup=None, d=D2, **kw):
    return {"return_date": d, "supplier_id": sup or env["sup"], "warehouse_id": env["main"],
            "currency_code": kw.pop("currency_code", "EGP"), "lines": lines, **kw}


def entry(db, doc, sid):
    return db.fetch_all(
        "SELECT a.code, a.role_code, l.debit, l.credit, l.supplier_id, l.currency_code, l.fx_rate, l.amount_fc "
        "FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = %s AND e.source_id = %s "
        "AND e.status = 'posted' ORDER BY l.line_no", [doc, sid])


def stock(env, it=None, wh=None, kind="invoice"):
    return env["ledger"].available(it or env["item"], wh or env["main"], kind)


def avg(env, it=None, kind="invoice"):
    return env["ledger"].average_cost(it or env["item"], kind)


def no_negative(monkeypatch):
    real = SESSION.can
    monkeypatch.setattr(SESSION, "can", lambda code: code != "purch.purchase_returns.negative" and real(code))


# ---- السكريبت ------------------------------------------------------------------
def test_migration_rules_reasons_permissions(env):
    db = env["db"]
    roles = {r["role"]: (r["side"], r["account_source"]) for r in db.fetch_all(
        "SELECT role, side, account_source FROM acc.posting_rules WHERE doc_type = 'purchase_return'")}
    assert roles["service"] == ("cr", "category") and roles["cost_var"] == ("cr", "default")
    assert [r["role"] for r in db.fetch_all("SELECT role FROM acc.posting_rules WHERE doc_type = 'purchase_refund' "
                                            "ORDER BY sort_order")] == ["cash", "bank", "payable"]
    assert [r["name_ar"] for r in env["service"].reasons()][:3] == ["تالف / مكسور", "مش مطابق للمواصفات",
                                                                   "زيادة عن المطلوب"]
    perms = {r["action_code"] for r in db.fetch_all(
        "SELECT action_code FROM sec.permissions WHERE module_code = 'purch' AND target_code = 'purchase_returns'")}
    assert perms == {"view", "create", "edit", "save", "delete", "export", "post", "unpost", "negative"}
    missing = db.fetch_all("""
        SELECT c.conrelid::regclass::text AS tbl, a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.conrelid::regclass::text LIKE 'purch.purchase_return%%'
          AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])""")
    assert all(m["attname"] == "return_id" for m in missing)       # أول عمود في الـ unique (return_id, line_no)


def test_calculate():
    c = calculate([{"qty": D_("2"), "unit_price": D_("90"), "tax_rate": D_("14"), "currency_code": "EGP",
                    "fx_rate": D_("1"), "factor": D_("10")}])
    ln = c["lines"][0]
    assert (ln["net_amount"], ln["vat_amount"], ln["total_amount"]) == (D_("180.00"), D_("25.20"), D_("205.20"))
    assert ln["base_qty"] == 20 and ln["unit_cost"] == D_("9")
    st = calculate([{"qty": D_("1"), "unit_price": D_("5"), "tax_rate": D_("14"), "currency_code": "USD",
                     "fx_rate": D_("48.5"), "factor": D_("1")}], statement=True)
    assert st["vat_amount"] == 0 and st["total_base"] == D_("242.50")


# ---- الخروج بسعر الشراء ---------------------------------------------------------
def test_return_at_purchase_price_and_avg_from_rest(env):
    s, db = env["service"], env["db"]
    _a, la = buy(env, [(env["item"], "10", "100")])
    _b, lb = buy(env, [(env["item"], "10", "200")], d=D1)
    assert avg(env) == D_("150")
    rid = s.save_draft(None, ret(env, [rline(lb[0], "5")], supplier_note_no="CN-1"))
    rec = s.get_return(rid)
    assert rec["status"] == "draft" and rec["lines"][0]["unit_price"] == D_("200")      # سعر الفاتورة لوحده
    assert rec["total_base"] == D_("1140.00")
    no = s.post(rid, expected_version=rec["row_version"])
    assert no == "PRT-2026-0001"
    assert stock(env) == 15 and avg(env) == D_("133.333333")                             # (3000 − 1000) ÷ 15
    lines = entry(db, "purchase_return", rid)
    assert sum(r["debit"] for r in lines) == sum(r["credit"] for r in lines) == D_("1140.00")
    dr = [r for r in lines if r["debit"]]
    assert len(dr) == 1 and dr[0]["supplier_id"] == env["sup"]
    cr = {r["role_code"]: r["credit"] for r in lines if r["credit"]}
    assert cr["inventory"] == D_("1000.00") and cr["vat_in"] == D_("140.00")
    assert env["inv"].supplier_balance(env["sup"])["base"] == D_("2280.00")             # 3420 − 1140
    assert s.movements(rid)[0]["value"] == D_("-1000.00")


def test_default_price_after_discounts_and_editable(env):
    s = env["service"]
    _i, li = buy(env, [(env["item"], "10", "100")], discount_amount="100")             # صافي الوحدة 90
    rid = s.save_draft(None, ret(env, [rline(li[0], "2")]))
    assert s.get_return(rid)["lines"][0]["unit_price"] == D_("90")
    rec = s.get_return(rid)
    s.save_draft(rid, ret(env, [rline(li[0], "2", unit_price="80")]), expected_version=rec["row_version"])
    review = s.review(ret(env, [rline(li[0], "2", unit_price="80")]), rid)
    assert any("غير سعر الفاتورة" in c[1] for c in review["checks"])
    s.post(rid)
    assert avg(env) == D_("92.5")                                                       # (900 − 160) ÷ 8


def test_quantity_limit_and_multi_invoices_and_free_line(env):
    s, db = env["service"], env["db"]
    _a, la = buy(env, [(env["item"], "10", "100")])
    _b, lb = buy(env, [(env["item2"], "4", "50")])
    with pytest.raises(BusinessError, match="أكتر من اللي فاضل"):
        s.save_draft(None, ret(env, [rline(la[0], "11")]))
    with pytest.raises(BusinessError, match="أكتر من اللي فاضل"):                      # سطرين من نفس الفاتورة
        s.save_draft(None, ret(env, [rline(la[0], "6"), rline(la[0], "5")]))
    r1 = s.save_draft(None, ret(env, [rline(la[0], "6"), rline(lb[0], "1"),
                                      {"item_id": env["item2"], "qty": "1", "unit_price": "40",
                                       "tax_code_id": env["vat14"]}]), post=True)
    rec = s.get_return(r1)
    assert [ln["source_invoice_no"] for ln in rec["lines"]] == ["PUR-2026-00001", "PUR-2026-00002", None]
    assert stock(env) == 4 and stock(env, env["item2"]) == 2
    assert s.invoice_lines(_a)[0]["remaining_base"] == 4
    with pytest.raises(BusinessError, match="فاضل 4"):
        s.save_draft(None, ret(env, [rline(la[0], "5")]))
    # المورد التاني / النوع التاني
    with pytest.raises(BusinessError, match="لمورد تاني"):
        s.save_draft(None, ret(env, [rline(la[0], "1")], sup=env["sup2"]))
    with pytest.raises(BusinessError, match="نفس النوع"):
        s.save_draft(None, ret(env, [rline(la[0], "1")], stock_kind="statement"))
    with pytest.raises(BusinessError, match="وهمي"):
        s.save_draft(None, ret(env, [rline(la[0], "1", warehouse_id=env["virtual"])]))
    # القاعدة نفسها: سطر مربوط بفاتورة لمورد تاني
    other = db.fetch_value("INSERT INTO purch.purchase_returns (return_date, fiscal_year_id, supplier_id, currency_code) "
                           "VALUES (%s, (SELECT id FROM core.fiscal_years WHERE year_code = '2026'), %s, 'EGP') "
                           "RETURNING id", [D2, env["sup2"]])
    with pytest.raises(psycopg.errors.RaiseException, match="لمورد تاني"):
        db.execute("INSERT INTO purch.purchase_return_lines (return_id, line_no, source_line_id, item_id, warehouse_id, "
                   "unit_id, qty, unit_price, currency_code, net_amount, total_amount, net_base, total_base) "
                   "SELECT %s, 1, l.id, l.item_id, l.warehouse_id, l.unit_id, 1, 1, 'EGP', 1, 1, 1, 1 "
                   "FROM purch.purchase_invoice_lines l WHERE l.id = %s", [other, la[0]["id"]])


def test_cost_variance_when_return_empties_stock(env):
    s, db = env["service"], env["db"]
    _a, la = buy(env, [(env["item"], "10", "100")])
    _b, lb = buy(env, [(env["item"], "10", "200")])
    s.save_draft(None, ret(env, [rline(la[0], "10")]), post=True)
    assert stock(env) == 10 and avg(env) == D_("200")
    rid = s.save_draft(None, ret(env, [rline(lb[0], "10", unit_price="150")]), post=True)
    assert stock(env) == 0
    lines = entry(db, "purchase_return", rid)
    var = [r for r in lines if r["code"] == "5206"]
    assert var and var[0]["debit"] == D_("500.00")                                      # 1500 − 2000
    assert sum(r["debit"] for r in lines) == sum(r["credit"] for r in lines)


def test_service_item_and_cash_refund(env):
    s, db = env["service"], env["db"]
    _i, li = buy(env, [(env["svc"], "1", "2500"), (env["item"], "5", "100")])
    rid = s.save_draft(None, ret(env, [rline(li[0], "1"), rline(li[1], "5")], pay_kind="cash",
                                 source_kind="treasury", treasury_id=env["treasury"]), post=True)
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'purchase_return' "
                          "AND source_id = %s", [rid]) == 1
    roles = {r["role_code"] for r in entry(db, "purchase_return", rid) if r["credit"]}
    assert "inventory" in roles
    refund = entry(db, "purchase_refund", rid)
    assert sum(r["debit"] for r in refund) == D_("3420.00")                              # (2500 + 500) × 1.14
    assert [r["supplier_id"] for r in refund if r["credit"]] == [env["sup"]]
    s.unpost(rid)
    assert entry(db, "purchase_refund", rid) == [] and stock(env) == 5


def test_usd_return_at_invoice_rate(env):
    s, db = env["service"], env["db"]
    _i, li = buy(env, [(env["item"], "4", "100")], sup=env["usd_sup"], currency_code="USD", fx_rate="48.3")
    rid = s.save_draft(None, ret(env, [rline(li[0], "1")], sup=env["usd_sup"], currency_code="USD", fx_rate="50"),
                       post=True)
    rec = s.get_return(rid)
    assert rec["lines"][0]["fx_rate"] == D_("48.3") and rec["total_base"] == D_("5506.20")   # 100 × 1.14 × 48.3 (بسعر الفاتورة)
    sup_line = [r for r in entry(db, "purchase_return", rid) if r["debit"]][0]
    assert (sup_line["currency_code"], sup_line["amount_fc"]) == ("USD", D_("114.00"))
    with pytest.raises(BusinessError, match="نفس العملة"):
        s.save_draft(None, ret(env, [rline(li[0], "1")], sup=env["usd_sup"]))


def test_sold_out_needs_negative_permission(env, monkeypatch):
    s = env["service"]
    _i, li = buy(env, [(env["item"], "10", "100")])
    s.save_draft(None, ret(env, [{"item_id": env["item"], "qty": "8", "unit_price": "100",
                                  "tax_code_id": env["vat14"]}]), post=True)            # 8 خرجوا
    no_negative(monkeypatch)
    rid = s.save_draft(None, ret(env, [rline(li[0], "5")]))
    review = s.review(ret(env, [rline(li[0], "5")]), rid)
    assert any(c[0] == "no" and "اتصرفت" in c[1] for c in review["checks"])
    with pytest.raises(NegativeStockError):
        s.post(rid)
    monkeypatch.undo()
    s.post(rid)
    assert stock(env) == -3


def test_invoice_cannot_be_unposted_with_returns(env):
    s = env["service"]
    iid, li = buy(env, [(env["item"], "10", "100")])
    rid = s.save_draft(None, ret(env, [rline(li[0], "2")]))
    with pytest.raises(BusinessError, match="عليها مرتجع"):
        env["inv"].unpost(iid)
    s.post(rid)
    with pytest.raises(BusinessError, match="عليها مرتجع"):
        env["inv"].unpost(iid)
    s.unpost(rid)
    assert stock(env) == 10
    with pytest.raises(BusinessError, match="رقم رسمي"):
        s.delete_return(rid)
    s.save_draft(rid, ret(env, [{"item_id": env["item"], "qty": "1", "unit_price": "100"}]))
    env["inv"].unpost(iid)                                                             # مفيش ربط ← مسموح
    assert env["inv"].get_invoice(iid)["status"] == "draft"


def test_two_devices_post_returns_on_same_invoice(env):
    s, db = env["service"], env["db"]
    _i, li = buy(env, [(env["item"], "10", "100")])
    r1 = s.save_draft(None, ret(env, [rline(li[0], "6")]))
    r2 = s.save_draft(None, ret(env, [rline(li[0], "6")]))
    s.post(r1)
    with pytest.raises(BusinessError, match="فاضل|أكتر"):
        s.post(r2)
    assert s.get_return(r2)["status"] == "draft"
    # نفس المرتجع من جهازين: الإصدار القديم بيترفض
    rec = s.get_return(r2)
    s.save_draft(r2, ret(env, [rline(li[0], "3")]), expected_version=rec["row_version"])
    with pytest.raises(BusinessError):
        s.save_draft(r2, ret(env, [rline(li[0], "2")]), expected_version=rec["row_version"])


def test_reasons_add_rename_delete(env):
    s = env["service"]
    rid = s.save_reason(None, "  صلاحية منتهية ")
    assert any(r["name_ar"] == "صلاحية منتهية" for r in s.reasons())
    with pytest.raises(BusinessError, match="موجود قبل كده"):
        s.save_reason(None, "صلاحية منتهية")
    s.save_reason(rid, "منتهي الصلاحية", True)
    _i, li = buy(env, [(env["item"], "10", "100")])
    s.save_draft(None, ret(env, [rline(li[0], "1")], reason_id=rid))
    with pytest.raises(BusinessError, match="موقوف"):
        s.delete_reason(rid)
    s.save_reason(rid, "منتهي الصلاحية", False)
    assert not next(r for r in s.reasons() if r["id"] == rid)["is_active"]


def test_closed_year_blocks(env):
    s, db = env["service"], env["db"]
    _i, li = buy(env, [(env["item"], "10", "100")], d=dt.date(2025, 12, 1))
    rid = s.save_draft(None, ret(env, [rline(li[0], "1")], d=dt.date(2025, 12, 5)))
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed' WHERE year_code = '2025'")
    s.delete_return(rid)


# ---- الشاشة -----------------------------------------------------------------
def _screen(env):
    from app.ui.screens.purchase_returns_screen import PurchaseReturnsScreen

    return PurchaseReturnsScreen(service=env["service"])


def test_screen_from_invoice_add_update_save_post_unpost(env):
    from app.ui.screens.purchase_returns_screen import C_NAME, C_SRC, C_TOTAL, FREE

    _a, la = buy(env, [(env["item"], "10", "100"), (env["item2"], "4", "50")])
    screen = _screen(env)
    assert screen.current_id is None and screen.mode == "view"
    screen.new_record()
    screen.supplier_edit.set_value(env["sup"])
    assert screen.inv_combo.count() == 2 and screen.inv_combo.currentData() == _a       # الفاتورة + «من غير»
    assert screen.src_combo.count() == 2 and screen.add_qty.text() == "10" and screen.add_price.text() == "100"
    screen.add_qty.setText("3")
    screen.add_line()
    assert len(screen._lines) == 1 and screen._lines[0]["source_line_id"] == la[0]["id"]
    assert screen.add_qty.text() == "7"                                                 # الفاضل بعد السطر
    screen._refresh_review()
    assert screen.lines_table.item(0, C_SRC).text() == "PUR-2026-00001"
    assert screen.lines_table.item(0, C_TOTAL).text() == "342.00" and screen.net_value.text() == "342.00"
    screen.add_qty.setText("8")
    screen.add_line()                                                                    # أكتر من الفاضل (7)
    assert len(screen._lines) == 1 and "فاضل" in env["shown"][-1]
    screen._on_row(0)
    assert screen.add_button.text().endswith("تحديث السطر") and screen.add_qty.text() == "3"
    screen.add_qty.setText("4")
    screen.add_price.setText("90")                                                       # السعر بيتغيّر
    screen.add_line()
    assert screen._lines[0]["qty"] == 4 and screen._lines[0]["unit_price"] == 90
    # سطر من غير فاتورة
    screen._set_combo(screen.inv_combo, FREE)
    assert screen.item_box.isVisibleTo(screen) and not screen.src_box.isVisibleTo(screen)
    screen.item_edit.set_value(env["item2"])
    screen.add_qty.setText("1")
    screen.add_price.setText("40")
    screen.add_line()
    screen.note_no_edit.setText("CN-7")
    screen.save()
    rec = screen.current_record
    assert screen.mode == "view" and rec["status"] == "draft" and len(rec["lines"]) == 2
    assert rec["total_base"] == D_("456.00")                                             # (360 + 40) × 1.14
    screen.post_return()
    rec = screen.current_record
    assert rec["status"] == "posted" and rec["return_no"] == "PRT-2026-0001"
    assert not screen.edit_button.isEnabled() and screen.unpost_button.isEnabled()
    assert stock(env) == 6 and avg(env) == D_("106.666667")                              # (1000 − 360) ÷ 6
    screen.unpost_return()
    assert screen.current_record["status"] == "draft" and stock(env) == 10
    screen.edit_record()
    screen._lines[0]["warehouse_id"] = env["virtual"]
    screen._refresh_review()
    assert "وهمي" in screen.lines_table.item(0, C_NAME).text() and "✖" in screen.review_label.text()
    screen.cancel()
    screen.close()
    screen.deleteLater()


def test_screen_return_all_cash_and_reasons(env):
    _a, la = buy(env, [(env["item"], "10", "100"), (env["item2"], "4", "50")])
    screen = _screen(env)
    screen.new_record()
    screen.supplier_edit.set_value(env["sup"])
    screen.return_all()
    assert [ln["qty"] for ln in screen._lines] == [10, 4]
    screen._set_combo(screen.pay_combo, "cash")
    assert not screen.source_box.isHidden()
    screen._set_combo(screen.reason_combo, env["service"].reasons()[0]["id"])
    screen.post_return()
    rec = screen.current_record
    assert rec["status"] == "posted" and rec["pay_kind"] == "cash" and rec["reason_name"] == "تالف / مكسور"
    assert stock(env) == 0 and env["inv"].supplier_balance(env["sup"])["base"] == D_("1368.00")   # الفاتورة باقية
    assert entry(env["db"], "purchase_refund", rec["id"])
    screen.new_record()
    screen.supplier_edit.set_value(env["sup"])
    assert screen.inv_combo.count() == 1                                                  # مفيش حاجة فاضلة
    screen.cancel()
    from app.ui.screens.purchase_returns_screen import ReasonsDialog

    dialog = ReasonsDialog(env["service"], screen)
    dialog.name_edit.setText("صلاحية منتهية")
    dialog.add_reason()
    assert any(r["name_ar"] == "صلاحية منتهية" for r in env["service"].reasons())
    dialog.close()
    screen.close()
    screen.deleteLater()


def test_returnable_invoices_and_picker_dialog(env, monkeypatch):
    from PySide6.QtCore import Qt

    from app.ui.screens.purchase_returns_screen import InvoicePickerDialog, PurchaseReturnsScreen

    s = env["service"]
    a, la = buy(env, [(env["item"], "10", "100"), (env["item2"], "4", "50")])
    b, _lb = buy(env, [(env["item"], "5", "200")], sup=env["sup2"], d=D2)
    rows = s.returnable_invoices(stock_kind="invoice")
    assert [r["id"] for r in rows] == [b, a] and rows[1]["remaining_lines"] == 2
    assert s.count_returnable_invoices(stock_kind="invoice") == 2
    assert [r["id"] for r in s.returnable_invoices(env["sup"], "invoice", keyword="لمبة")] == [a]
    assert s.returnable_invoices(stock_kind="statement") == []
    # الواجهة المنبثقة: الفواتير فوق ← الضغط على فاتورة بيعرض تفاصيلها تحت
    suppliers = s.choices()["suppliers"]
    dialog = InvoicePickerDialog(s, None, suppliers=suppliers, supplier_id=None, lock_supplier=False,
                                 stock_kind="invoice", currency_code=None, exclude_return_id=None, used={})
    assert dialog.invoices.rowCount() == 2 and dialog.lines.rowCount() == 1           # أحدث فاتورة (B)
    dialog.invoices.setCurrentCell(1, 0)                                                # الفاتورة A
    assert dialog.lines.rowCount() == 2 and "PUR-2026-00001" in dialog.detail_title.text()
    dialog.lines.item(0, dialog.L_CHECK).setCheckState(Qt.Checked)                       # الكابل كله (10)
    dialog.lines.item(1, dialog.L_QTY).setText("2")                                     # لمبتين بس
    QApplication.processEvents()
    assert {int(k): v[1] for k, v in dialog.picks.items()} == {la[0]["id"]: 10, la[1]["id"]: 2}
    dialog.lines.item(1, dialog.L_QTY).setText("9")                                     # أكتر من الفاضل
    QApplication.processEvents()
    assert dialog.picks[la[1]["id"]][1] == 2 and "الفاضل 4" in env["shown"][-1]
    dialog.invoices.setCurrentCell(0, 0)                                                # فاتورة مورد تاني
    dialog.pick_all()
    assert len(dialog.picks) == 2 and "لمورد واحد" in env["shown"][-1]
    dialog.apply()
    assert dialog.result_supplier == env["sup"] and len(dialog.result) == 2
    dialog.deleteLater()

    # الزرار في الشاشة: المورد بيتملى والأصناف بتنزل
    def fake_exec(self):
        self.invoices.setCurrentCell(next(r for r, row in enumerate(self._rows) if row["id"] == a), 0)
        self.pick_all()
        self.apply()
        return True

    monkeypatch.setattr(InvoicePickerDialog, "exec", fake_exec)
    screen = PurchaseReturnsScreen(service=s)
    screen.new_record()
    assert screen.pick_button.isEnabled()
    screen.pick_from_invoices()
    assert screen.supplier_edit.currentData() == env["sup"]
    assert [(ln["source_line_id"], ln["qty"]) for ln in screen._lines] == [(la[0]["id"], 10), (la[1]["id"], 4)]
    screen.pick_from_invoices()                                                          # مفيش حاجة فاضلة
    assert len(screen._lines) == 2
    screen.save()
    assert screen.current_record["total_base"] == D_("1368.00")
    screen.close()
    screen.deleteLater()
