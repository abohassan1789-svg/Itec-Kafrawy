"""اختبارات «مرتجع المبيعات» (0053 + sales_return_service + الشاشة — نفس تصميم مرتجع المشتريات):
البضاعة بترجع **بتكلفة البيع الأصلية** (مش المتوسط) والحر بالمتوسط · السعر من الفاتورة بعد الخصومات ويتغيّر · الكمية ≤
اللي اتباع ناقص اللي رجع · أكتر من فاتورة + سطر حر · **القيد الواحد متوازن** (المردودات / ض.ق.م / العميل / المخزون /
تكلفة المبيعات) · الخدمي على «إيراد الخدمات» · نقدي = قيد صرف · «عميل نقدي» نقدي بس · العملة (سعر الفاتورة +
فاتورة متعددة العملات) · منع إلغاء ترحيل فاتورة عليها مرتجع · إلغاء ترحيل مرتجع اتباعت بضاعته و«من غير رصيد» ·
**جهازين** · الأسباب · قفل السنة · الشاشة والواجهة المنبثقة."""

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
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.purchase_invoice_service import PurchaseInvoiceService
from app.services.sales_invoice_service import SalesInvoiceService
from app.services.sales_return_service import SalesReturnService, calculate
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService
from app.ui.common import messages

D0, D1, D2, D3 = dt.date(2026, 2, 1), dt.date(2026, 3, 1), dt.date(2026, 3, 2), dt.date(2026, 3, 3)
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
    cgroup = db.fetch_value("SELECT id FROM party.customer_groups WHERE is_system ORDER BY id LIMIT 1")
    sgroup = db.fetch_value("SELECT group_id FROM party.suppliers WHERE seq = 0")
    env = {"db": db, "shown": shown, "sales": SalesInvoiceService(db), "purchase": PurchaseInvoiceService(db),
           "service": SalesReturnService(db), "ledger": StockLedgerService(db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "virtual": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_virtual ORDER BY seq LIMIT 1"),
           "item": item(db, "كابل نحاس", price="150"), "item2": item(db, "لمبة LED", price="40"),
           "svc": item(db, "تركيب ومعايرة", True, price="500"),
           "vat14": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT14'"),
           "vat0": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT0'"),
           "treasury": db.fetch_value("SELECT id FROM cash.treasuries WHERE code = 'TRS-001'"),
           "cash_cust": db.fetch_value("SELECT id FROM party.customers WHERE is_system"),
           "cust": db.fetch_value("INSERT INTO party.customers (code, name_ar, group_id, currency_code, "
                                  "payment_terms_days) VALUES ('1', 'شركة الأمل', %s, 'EGP', 30) RETURNING id",
                                  [cgroup]),
           "cust2": db.fetch_value("INSERT INTO party.customers (code, name_ar, group_id, currency_code) "
                                   "VALUES ('3', 'النيل للمقاولات', %s, 'EGP') RETURNING id", [cgroup]),
           "usd_cust": db.fetch_value("INSERT INTO party.customers (code, name_ar, group_id, currency_code, "
                                      "vat_tax_code_id) VALUES ('2', 'Gulf Trading', %s, 'USD', "
                                      "(SELECT id FROM acc.tax_codes WHERE code = 'VAT0')) RETURNING id", [cgroup]),
           "sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code) "
                                 "VALUES (1, 'شركة النور', %s, 'EGP') RETURNING id", [sgroup])}
    for code, rate in (("USD", "48.5"), ("EUR", "52.8")):
        db.execute("INSERT INTO core.exchange_rates (currency_id, rate_date, rate) "
                   "SELECT id, '2026-01-01', %s FROM core.currencies WHERE code = %s", [rate, code])
    yield env
    SESSION.clear()
    gc.collect()


def item(db, name, service=False, price=None):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id, is_service, sale_price) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14'), "
        "%s, %s) RETURNING id", [name, service, price])


def buy(env, it=None, qty="10", price="100", d=D0):
    return env["purchase"].save_draft(None, {
        "invoice_date": d, "supplier_id": env["sup"], "warehouse_id": env["main"], "currency_code": "EGP",
        "due_date": d, "lines": [{"item_id": it or env["item"], "qty": qty, "unit_price": price}]}, post=True)


def sell(env, lines, cust=None, d=D1, **kw):
    """فاتورة بيع مرحّلة ← (id، سطورها)."""
    payload = {"invoice_date": d, "customer_id": cust or env["cust"], "warehouse_id": env["main"],
               "currency_code": kw.pop("currency_code", "EGP"), "due_date": d + dt.timedelta(days=30),
               "lines": [{"item_id": it, "qty": q, "unit_price": p, "tax_code_id": env["vat14"]} for it, q, p in lines],
               **kw}
    iid = env["sales"].save_draft(None, payload, post=True)
    return iid, env["sales"].get_invoice(iid)["lines"]


def rline(src=None, qty="1", **kw):
    return {"source_line_id": src["id"] if src else None, "qty": qty, **kw}


def ret(env, lines, cust=None, d=D2, **kw):
    return {"return_date": d, "customer_id": cust or env["cust"], "warehouse_id": env["main"],
            "currency_code": kw.pop("currency_code", "EGP"), "lines": lines, **kw}


def entry(db, doc, sid):
    return db.fetch_all(
        "SELECT a.code, a.role_code, l.debit, l.credit, l.customer_id, l.currency_code, l.fx_rate, l.amount_fc "
        "FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = %s AND e.source_id = %s "
        "AND e.status = 'posted' ORDER BY l.line_no", [doc, sid])


def acct(db, key):
    return db.fetch_value("SELECT a.code FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
                          "WHERE d.key = %s", [key])


def by_code(lines):
    out: dict[str, tuple[Decimal, Decimal]] = {}
    for r in lines:
        d, c = out.get(r["code"], (D_("0"), D_("0")))
        out[r["code"]] = (d + r["debit"], c + r["credit"])
    return out


def stock(env, it=None, wh=None, kind="invoice"):
    return env["ledger"].available(it or env["item"], wh or env["main"], kind)


def avg(env, it=None, kind="invoice"):
    return env["ledger"].average_cost(it or env["item"], kind)


def balanced(lines):
    return sum(r["debit"] for r in lines) == sum(r["credit"] for r in lines)


# ---- السكريبت ------------------------------------------------------------------
def test_migration_rules_reasons_permissions_indexes(env):
    db = env["db"]
    roles = {r["role"]: (r["side"], r["account_source"]) for r in db.fetch_all(
        "SELECT role, side, account_source FROM acc.posting_rules WHERE doc_type = 'sales_return'")}
    assert roles["sales_return"] == ("dr", "category") and roles["service_return"] == ("dr", "default")
    assert roles["inventory"] == ("dr", "category") and roles["cogs"] == ("cr", "category")
    assert [r["role"] for r in db.fetch_all("SELECT role FROM acc.posting_rules WHERE doc_type = 'sales_refund' "
                                            "ORDER BY sort_order")] == ["receivable", "cash", "bank"]
    assert [r["name_ar"] for r in env["service"].reasons()][:2] == ["تالف / عيب صناعة", "مش مطابق للمواصفات"]
    perms = {r["action_code"] for r in db.fetch_all(
        "SELECT action_code FROM sec.permissions WHERE module_code = 'sales' AND target_code = 'sales_returns'")}
    assert perms == {"view", "create", "edit", "save", "delete", "export", "post", "unpost", "negative"}
    missing = db.fetch_all("""
        SELECT c.conrelid::regclass::text AS tbl, a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.conrelid::regclass::text LIKE 'sales.sales_return%%'
          AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])""")
    assert all(m["attname"] == "return_id" for m in missing)       # أول عمود في الـ unique (return_id, line_no)


def test_calculate():
    c = calculate([{"qty": D_("2"), "unit_price": D_("150"), "tax_rate": D_("14"), "currency_code": "EGP",
                    "fx_rate": D_("1"), "factor": D_("1")}])
    ln = c["lines"][0]
    assert (ln["net_amount"], ln["vat_amount"], ln["total_amount"]) == (D_("300.00"), D_("42.00"), D_("342.00"))
    assert "unit_cost" not in ln and c["sales_base"] == D_("300.00")


# ---- التكلفة الأصلية والقيد ---------------------------------------------------------
def test_return_at_original_cost_one_balanced_entry(env):
    s, db = env["service"], env["db"]
    buy(env, qty="10", price="100")
    _i, li = sell(env, [(env["item"], "5", "150")])                    # طلعت بـ 100
    buy(env, qty="10", price="200", d=D1)                               # المتوسط بقى (500 + 2000) ÷ 15
    assert avg(env) == D_("166.666667")
    rid = s.save_draft(None, ret(env, [rline(li[0], "2")], customer_ref="RMA-1"))
    rec = s.get_return(rid)
    assert rec["status"] == "draft" and rec["lines"][0]["unit_price"] == D_("150")       # سعر الفاتورة لوحده
    assert rec["total_base"] == D_("342.00")
    review = s.review(ret(env, [rline(li[0], "2")]), rid)
    assert review["ready"] and review["customer"]["after"] == review["customer"]["before"] - D_("342.00")
    no = s.post(rid, expected_version=rec["row_version"])
    assert no == "SRT-2026-0001"
    assert stock(env) == 17 and avg(env) == D_("158.823529")                             # (2500 + 200) ÷ 17
    mv = s.movements(rid)
    assert mv[0]["qty"] == 2 and mv[0]["unit_cost"] == D_("100") and mv[0]["booked_value"] == D_("200.00")
    assert s.get_return(rid)["lines"][0]["unit_cost"] == D_("100")
    lines = entry(db, "sales_return", rid)
    assert balanced(lines)
    codes = by_code(lines)
    assert codes[acct(db, "sales_ret")] == (D_("300.00"), 0)
    assert codes[acct(db, "vat_out")] == (D_("42.00"), 0)
    assert codes[acct(db, "inventory")] == (D_("200.00"), 0) and codes[acct(db, "cogs")] == (0, D_("200.00"))
    cr = [r for r in lines if r["customer_id"]]
    assert len(cr) == 1 and cr[0]["credit"] == D_("342.00") and cr[0]["customer_id"] == env["cust"]
    assert env["sales"].customer_balance(env["cust"])["base"] == D_("513.00")             # 855 − 342


def test_default_price_after_discounts_and_editable(env):
    s = env["service"]
    buy(env, qty="10", price="100")
    _i, li = sell(env, [(env["item"], "10", "150")], discount_amount="100")             # صافي الوحدة 140
    rid = s.save_draft(None, ret(env, [rline(li[0], "2")]))
    assert s.get_return(rid)["lines"][0]["unit_price"] == D_("140")
    rec = s.get_return(rid)
    s.save_draft(rid, ret(env, [rline(li[0], "2", unit_price="120")]), expected_version=rec["row_version"])
    review = s.review(ret(env, [rline(li[0], "2", unit_price="120")]), rid)
    assert any("غير سعر الفاتورة" in c[1] for c in review["checks"])
    s.post(rid)
    lines = entry(env["db"], "sales_return", rid)
    assert balanced(lines) and by_code(lines)[acct(env["db"], "sales_ret")] == (D_("240.00"), 0)
    assert by_code(lines)[acct(env["db"], "inventory")] == (D_("200.00"), 0)            # التكلفة من الفاتورة مش من السعر


def test_quantity_limit_multi_invoices_and_free_line_at_average(env):
    s, db = env["service"], env["db"]
    buy(env, qty="10", price="100")
    buy(env, env["item2"], qty="10", price="20")
    a, la = sell(env, [(env["item"], "6", "150")])
    _b, lb = sell(env, [(env["item2"], "4", "40")])
    with pytest.raises(BusinessError, match="أكتر من اللي فاضل"):
        s.save_draft(None, ret(env, [rline(la[0], "7")]))
    with pytest.raises(BusinessError, match="أكتر من اللي فاضل"):                      # سطرين من نفس الفاتورة
        s.save_draft(None, ret(env, [rline(la[0], "4"), rline(la[0], "3")]))
    r1 = s.save_draft(None, ret(env, [rline(la[0], "4"), rline(lb[0], "1"),
                                      {"item_id": env["item2"], "qty": "2", "unit_price": "35",
                                       "tax_code_id": env["vat14"]}]), post=True)
    rec = s.get_return(r1)
    assert [ln["source_invoice_no"] for ln in rec["lines"]] == ["INV-2026-00001", "INV-2026-00002", None]
    assert [ln["unit_cost"] for ln in rec["lines"]] == [D_("100"), D_("20"), D_("20")]  # الحر بالمتوسط
    assert stock(env) == 8 and stock(env, env["item2"]) == 9
    assert s.invoice_lines(a)[0]["remaining_base"] == 2
    with pytest.raises(BusinessError, match="فاضل 2"):
        s.save_draft(None, ret(env, [rline(la[0], "3")]))
    assert balanced(entry(db, "sales_return", r1))
    # العميل التاني / النوع التاني / الوهمي
    with pytest.raises(BusinessError, match="لعميل تاني"):
        s.save_draft(None, ret(env, [rline(la[0], "1")], cust=env["cust2"]))
    with pytest.raises(BusinessError, match="نفس النوع"):
        s.save_draft(None, ret(env, [rline(la[0], "1")], stock_kind="statement"))
    with pytest.raises(BusinessError, match="وهمي"):
        s.save_draft(None, ret(env, [rline(la[0], "1", warehouse_id=env["virtual"])]))
    # القاعدة نفسها: سطر مربوط بفاتورة لعميل تاني
    other = db.fetch_value("INSERT INTO sales.sales_returns (return_date, fiscal_year_id, customer_id, currency_code) "
                           "VALUES (%s, (SELECT id FROM core.fiscal_years WHERE year_code = '2026'), %s, 'EGP') "
                           "RETURNING id", [D2, env["cust2"]])
    with pytest.raises(psycopg.errors.RaiseException, match="لعميل تاني"):
        db.execute("INSERT INTO sales.sales_return_lines (return_id, line_no, source_line_id, item_id, warehouse_id, "
                   "unit_id, qty, unit_price, currency_code, net_amount, total_amount, net_base, total_base) "
                   "SELECT %s, 1, l.id, l.item_id, l.warehouse_id, l.unit_id, 1, 1, 'EGP', 1, 1, 1, 1 "
                   "FROM sales.sales_invoice_lines l WHERE l.id = %s", [other, la[0]["id"]])


def test_service_item_and_cash_refund(env):
    s, db = env["service"], env["db"]
    buy(env, qty="10", price="100")
    _i, li = sell(env, [(env["svc"], "1", "500"), (env["item"], "5", "150")])
    rid = s.save_draft(None, ret(env, [rline(li[0], "1"), rline(li[1], "5")], pay_kind="cash",
                                 source_kind="treasury", treasury_id=env["treasury"]), post=True)
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'sales_return' "
                          "AND source_id = %s", [rid]) == 1
    lines = entry(db, "sales_return", rid)
    assert balanced(lines)
    codes = by_code(lines)
    assert codes[acct(db, "services")] == (D_("500.00"), 0)                              # عكس إيراد الخدمات
    assert codes[acct(db, "sales_ret")] == (D_("750.00"), 0)
    refund = entry(db, "sales_refund", rid)
    assert sum(r["debit"] for r in refund) == D_("1425.00")                              # (500 + 750) × 1.14
    assert [r["customer_id"] for r in refund if r["debit"]] == [env["cust"]]
    assert stock(env) == 10
    s.unpost(rid)
    assert entry(db, "sales_refund", rid) == [] and stock(env) == 5
    # «عميل نقدي» نقدي بس
    _c, lc = sell(env, [(env["item"], "1", "150")], cust=env["cash_cust"], pay_kind="cash", source_kind="treasury",
                  treasury_id=env["treasury"])
    with pytest.raises(BusinessError, match="نقدي بس"):
        s.save_draft(None, ret(env, [rline(lc[0], "1")], cust=env["cash_cust"]))
    s.save_draft(None, ret(env, [rline(lc[0], "1")], cust=env["cash_cust"], pay_kind="cash",
                           source_kind="treasury", treasury_id=env["treasury"]), post=True)


def test_foreign_currency_at_invoice_rate_and_multi_currency_invoice(env):
    s, db = env["service"], env["db"]
    buy(env, qty="10", price="1000")
    buy(env, env["item2"], qty="10", price="20")
    _i, li = sell(env, [(env["item"], "4", "100")], cust=env["usd_cust"], currency_code="USD", fx_rate="48.3")
    rid = s.save_draft(None, ret(env, [rline(li[0], "1")], cust=env["usd_cust"], currency_code="USD", fx_rate="50"),
                       post=True)
    rec = s.get_return(rid)
    assert rec["lines"][0]["fx_rate"] == D_("48.3") and rec["total_base"] == D_("5506.20")   # 100 × 1.14 × 48.3
    cust_line = [r for r in entry(db, "sales_return", rid) if r["customer_id"]][0]
    assert (cust_line["currency_code"], cust_line["amount_fc"], cust_line["credit"]) == ("USD", D_("114.00"),
                                                                                          D_("5506.20"))
    assert balanced(entry(db, "sales_return", rid))
    with pytest.raises(BusinessError, match="نفس العملة"):
        s.save_draft(None, ret(env, [rline(li[0], "1")], cust=env["usd_cust"]))
    # فاتورة «متعددة العملات»: كل عملة بمرتجعها (زي مرتجع المشتريات — عملة واحدة في المرتجع)
    multi = env["sales"].save_draft(None, {
        "invoice_date": D1, "customer_id": env["usd_cust"], "warehouse_id": env["main"], "currency_mode": "multi",
        "currency_code": "EGP", "due_date": D1,
        "lines": [{"item_id": env["item"], "qty": "2", "unit_price": "100", "currency_code": "USD", "fx_rate": "50",
                   "tax_code_id": env["vat0"]},
                  {"item_id": env["item2"], "qty": "3", "unit_price": "40", "currency_code": "EGP",
                   "tax_code_id": env["vat14"]}]}, post=True)
    ml = env["sales"].get_invoice(multi)["lines"]
    assert [r["id"] for r in s.returnable_invoices(env["usd_cust"], "invoice", "EGP")] == [multi]
    with pytest.raises(BusinessError, match="نفس العملة"):
        s.save_draft(None, ret(env, [rline(ml[0], "1"), rline(ml[1], "1")], cust=env["usd_cust"],
                               currency_code="USD", fx_rate="50"))
    usd = s.save_draft(None, ret(env, [rline(ml[0], "1")], cust=env["usd_cust"], currency_code="USD",
                                 fx_rate="49"), post=True)
    egp = s.save_draft(None, ret(env, [rline(ml[1], "3")], cust=env["usd_cust"]), post=True)
    assert s.get_return(usd)["total_base"] == D_("5000.00") and s.get_return(egp)["total_base"] == D_("136.80")
    by = {r["currency_code"]: r["amount"] for r in env["sales"].customer_balance(env["usd_cust"])["by_currency"]}
    assert by["USD"] == D_("442.00") and by["EGP"] == 0                                 # 456 − 114 + 200 − 100
    for r in (usd, egp):
        lines = entry(db, "sales_return", r)
        assert balanced(lines) and by_code(lines)[acct(db, "inventory")][0] > 0


def test_invoice_cannot_be_unposted_with_returns(env):
    s = env["service"]
    buy(env, qty="10", price="100")
    iid, li = sell(env, [(env["item"], "5", "150")])
    rid = s.save_draft(None, ret(env, [rline(li[0], "2")]))
    with pytest.raises(BusinessError, match="عليها مرتجع"):
        env["sales"].unpost(iid)
    s.post(rid)
    with pytest.raises(BusinessError, match="عليها مرتجع"):
        env["sales"].unpost(iid)
    s.unpost(rid)
    assert stock(env) == 5
    with pytest.raises(BusinessError, match="رقم رسمي"):
        s.delete_return(rid)
    s.save_draft(rid, ret(env, [{"item_id": env["item"], "qty": "1", "unit_price": "150"}]))
    env["sales"].unpost(iid)                                                           # مفيش ربط ← مسموح
    assert env["sales"].get_invoice(iid)["status"] == "draft"


def test_unpost_after_resale_needs_negative_permission(env, monkeypatch):
    s = env["service"]
    buy(env, qty="5", price="100")
    _i, li = sell(env, [(env["item"], "5", "150")])
    rid = s.save_draft(None, ret(env, [rline(li[0], "3")]), post=True)
    sell(env, [(env["item"], "3", "150")], d=D3)                                       # البضاعة اللي رجعت اتباعت
    assert stock(env) == 0
    real = SESSION.can
    monkeypatch.setattr(SESSION, "can", lambda code: code != "sales.sales_returns.negative" and real(code))
    with pytest.raises(NegativeStockError):
        s.unpost(rid)
    monkeypatch.undo()
    s.unpost(rid)
    assert stock(env) == -3


def test_two_devices_post_returns_on_same_invoice(env, base_settings):
    from dataclasses import replace
    import threading

    s, db = env["service"], env["db"]
    buy(env, qty="10", price="100")
    _i, li = sell(env, [(env["item"], "10", "150")])
    r1 = s.save_draft(None, ret(env, [rline(li[0], "6")]))
    r2 = s.save_draft(None, ret(env, [rline(li[0], "6")]))
    other = Database(replace(base_settings, db_name=db.settings.db_name))
    results: list[str] = []

    def run(service, rid, version=None):
        try:
            service.post(rid, expected_version=version)
            results.append("ok")
        except ConcurrentEditError:
            results.append("conflict")
        except BusinessError as exc:
            results.append(f"err:{exc}")

    try:
        threads = [threading.Thread(target=run, args=(s, r1)),
                   threading.Thread(target=run, args=(SalesReturnService(other), r2))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(r[:3] for r in results) == ["err", "ok"]
        assert any("فاضل" in r or "أكتر" in r for r in results)
        assert stock(env) == 6
        # نفس المرتجع من جهازين: واحد بس يعدّي
        results.clear()
        r3 = s.save_draft(None, ret(env, [rline(li[0], "1")]))
        v = s.get_return(r3)["row_version"]
        threads = [threading.Thread(target=run, args=(s, r3, v)),
                   threading.Thread(target=run, args=(SalesReturnService(other), r3, v))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        other.close()
    assert results.count("ok") == 1 and s.get_return(r3)["status"] == "posted"
    # الحفظ بإصدار قديم بيترفض
    draft = [r for r in (r1, r2) if s.get_return(r)["status"] == "draft"][0]
    rec = s.get_return(draft)
    s.save_draft(draft, ret(env, [rline(li[0], "2")]), expected_version=rec["row_version"])
    with pytest.raises(BusinessError):
        s.save_draft(draft, ret(env, [rline(li[0], "1")]), expected_version=rec["row_version"])


def test_reasons_add_rename_delete_and_closed_year(env):
    s, db = env["service"], env["db"]
    rid = s.save_reason(None, "  العميل غيّر رأيه ")
    assert any(r["name_ar"] == "العميل غيّر رأيه" for r in s.reasons())
    with pytest.raises(BusinessError, match="موجود قبل كده"):
        s.save_reason(None, "العميل غيّر رأيه")
    buy(env, qty="10", price="100", d=dt.date(2025, 11, 1))
    _i, li = sell(env, [(env["item"], "5", "150")], d=dt.date(2025, 12, 1))
    ret_id = s.save_draft(None, ret(env, [rline(li[0], "1")], d=dt.date(2025, 12, 5), reason_id=rid))
    with pytest.raises(BusinessError, match="موقوف"):
        s.delete_reason(rid)
    s.save_reason(rid, "العميل غيّر رأيه", False)
    assert not next(r for r in s.reasons() if r["id"] == rid)["is_active"]
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed' WHERE year_code = '2025'")
    s.delete_return(ret_id)


# ---- الشاشة -----------------------------------------------------------------
def test_screen_from_invoice_add_update_save_post_unpost(env):
    from app.ui.screens.sales_returns_screen import C_NAME, C_SRC, C_TOTAL, FREE, SalesReturnsScreen

    buy(env, qty="10", price="100")
    buy(env, env["item2"], qty="10", price="20")
    a, la = sell(env, [(env["item"], "10", "150"), (env["item2"], "4", "40")])
    screen = SalesReturnsScreen(service=env["service"])
    assert screen.current_id is None and screen.mode == "view"
    screen.new_record()
    screen.customer_edit.set_value(env["cust"])
    assert screen.inv_combo.count() == 2 and screen.inv_combo.currentData() == a        # الفاتورة + «من غير»
    assert screen.src_combo.count() == 2 and screen.add_qty.text() == "10" and screen.add_price.text() == "150"
    screen.add_qty.setText("3")
    screen.add_line()
    assert len(screen._lines) == 1 and screen._lines[0]["source_line_id"] == la[0]["id"]
    assert screen.add_qty.text() == "7"
    screen._refresh_review()
    assert screen.lines_table.item(0, C_SRC).text() == "INV-2026-00001"
    assert screen.lines_table.item(0, C_TOTAL).text() == "513.00" and screen.net_value.text() == "513.00"
    screen.add_qty.setText("8")
    screen.add_line()                                                                    # أكتر من الفاضل (7)
    assert len(screen._lines) == 1 and "فاضل" in env["shown"][-1]
    screen._on_row(0)
    assert screen.add_button.text().endswith("تحديث السطر") and screen.add_qty.text() == "3"
    screen.add_qty.setText("4")
    screen.add_price.setText("140")
    screen.add_line()
    assert screen._lines[0]["qty"] == 4 and screen._lines[0]["unit_price"] == 140
    # سطر من غير فاتورة: السعر = آخر سعر للعميل
    screen._set_combo(screen.inv_combo, FREE)
    assert screen.item_box.isVisibleTo(screen) and not screen.src_box.isVisibleTo(screen)
    screen.item_edit.set_value(env["item2"])
    assert screen.add_price.text() == "40"
    screen.add_qty.setText("1")
    screen.add_line()
    screen.note_no_edit.setText("RMA-7")
    screen.save()
    rec = screen.current_record
    assert screen.mode == "view" and rec["status"] == "draft" and len(rec["lines"]) == 2
    assert rec["total_base"] == D_("684.00") and rec["customer_ref"] == "RMA-7"          # (560 + 40) × 1.14
    screen.post_return()
    rec = screen.current_record
    assert rec["status"] == "posted" and rec["return_no"] == "SRT-2026-0001"
    assert not screen.edit_button.isEnabled() and screen.unpost_button.isEnabled()
    assert stock(env) == 4 and avg(env) == D_("100")
    screen.unpost_return()
    assert screen.current_record["status"] == "draft" and stock(env) == 0
    screen.edit_record()
    screen._lines[0]["warehouse_id"] = env["virtual"]
    screen._refresh_review()
    assert "وهمي" in screen.lines_table.item(0, C_NAME).text() and "✖" in screen.review_label.text()
    screen.cancel()
    screen.close()
    screen.deleteLater()


def test_screen_return_all_cash_and_picker_dialog(env, monkeypatch):
    from PySide6.QtCore import Qt

    from app.ui.screens.sales_returns_screen import InvoicePickerDialog, SalesReturnsScreen

    s = env["service"]
    buy(env, qty="20", price="100")
    buy(env, env["item2"], qty="10", price="20")
    a, la = sell(env, [(env["item"], "10", "150"), (env["item2"], "4", "40")])
    b, _lb = sell(env, [(env["item"], "5", "160")], cust=env["cust2"], d=D2)
    rows = s.returnable_invoices(stock_kind="invoice")
    assert [r["id"] for r in rows] == [b, a] and rows[1]["remaining_lines"] == 2
    assert [r["id"] for r in s.returnable_invoices(env["cust"], "invoice", keyword="لمبة")] == [a]
    customers = s.choices()["customers"]
    dialog = InvoicePickerDialog(s, None, customers=customers, customer_id=None, lock_customer=False,
                                 stock_kind="invoice", currency_code=None, exclude_return_id=None, used={})
    assert dialog.invoices.rowCount() == 2 and dialog.lines.rowCount() == 1           # أحدث فاتورة (B)
    dialog.invoices.setCurrentCell(1, 0)
    assert dialog.lines.rowCount() == 2 and "INV-2026-00001" in dialog.detail_title.text()
    dialog.lines.item(0, dialog.L_CHECK).setCheckState(Qt.Checked)
    dialog.lines.item(1, dialog.L_QTY).setText("2")
    QApplication.processEvents()
    assert {int(k): v[1] for k, v in dialog.picks.items()} == {la[0]["id"]: 10, la[1]["id"]: 2}
    dialog.invoices.setCurrentCell(0, 0)                                                # فاتورة عميل تاني
    dialog.pick_all()
    assert len(dialog.picks) == 2 and "لعميل واحد" in env["shown"][-1]
    dialog.deleteLater()

    screen = SalesReturnsScreen(service=s)
    screen.new_record()
    screen.customer_edit.set_value(env["cust"])
    screen.return_all()
    assert [ln["qty"] for ln in screen._lines] == [10, 4]
    screen._set_combo(screen.pay_combo, "cash")
    assert not screen.source_box.isHidden()
    screen._set_combo(screen.reason_combo, s.reasons()[0]["id"])
    screen.post_return()
    rec = screen.current_record
    assert rec["status"] == "posted" and rec["pay_kind"] == "cash" and rec["reason_name"] == "تالف / عيب صناعة"
    assert stock(env) == 15 and entry(env["db"], "sales_refund", rec["id"])
    screen.new_record()
    screen.customer_edit.set_value(env["cust"])
    assert screen.inv_combo.count() == 1                                                  # مفيش حاجة فاضلة

    def fake_exec(self):
        self.invoices.setCurrentCell(next(r for r, row in enumerate(self._rows) if row["id"] == b), 0)
        self.pick_all()
        self.apply()
        return True

    monkeypatch.setattr(InvoicePickerDialog, "exec", fake_exec)
    screen.customer_edit.set_value(None)
    screen.pick_from_invoices()
    assert screen.customer_edit.currentData() == env["cust2"] and len(screen._lines) == 1
    screen.save()
    assert screen.current_record["total_base"] == D_("912.00")                          # 5 × 160 × 1.14
    screen.close()
    screen.deleteLater()
