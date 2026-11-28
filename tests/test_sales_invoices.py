"""اختبارات «فاتورة البيع / إذن الصرف» (0051 + sales_invoice_service + الشاشة — النموذج 1):
الحسبة (الخصم قبل الضريبة لـ 5303) · مسودة ← ترحيل برقم INV والبضاعة بتطلع **بالمتوسط المرجّح** · **القيد الواحد متوازن**
(العميل · الخصم · المبيعات · ض.ق.م · تكلفة المبيعات / المخزون) · الخدمي على «إيراد الخدمات» · نقدي = قيد قبض ·
«عميل نقدي» نقدي بس · **منع البيع أكتر من المتاح** إلا بـ «من غير رصيد» · **حد الائتمان** إلا بـ «تجاوز حد الائتمان» ·
**متعدد العملات** (سطر على العميل لكل عملة) · العملة الواحدة · إلغاء الترحيل · الرقم الإلكتروني بعد الترحيل · أمر التوريد ·
حماية القاعدة · **جهازين** (نفس الصنف ونفس الفاتورة ونفس العميل) · التكلفة والربح بالصلاحية · الشاشة (الاختيار المتعدد
والتعديل في الجدول)."""

from __future__ import annotations

import datetime as dt
import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.purchase_invoice_service import PurchaseInvoiceService
from app.services.sales_invoice_service import SalesInvoiceService, calculate
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService
from app.ui.common import messages

D0, D1, D2, D3 = dt.date(2026, 2, 1), dt.date(2026, 3, 1), dt.date(2026, 3, 2), dt.date(2026, 3, 3)
D_ = Decimal
PERM = "sales.sales_invoices"


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
    env = {"db": db, "shown": shown, "service": SalesInvoiceService(db), "purchase": PurchaseInvoiceService(db),
           "ledger": StockLedgerService(db),
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
           "usd_cust": db.fetch_value("INSERT INTO party.customers (code, name_ar, group_id, currency_code, "
                                      "vat_tax_code_id) VALUES ('2', 'Gulf Trading', %s, 'USD', "
                                      "(SELECT id FROM acc.tax_codes WHERE code = 'VAT0')) RETURNING id", [cgroup]),
           "sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code) "
                                 "VALUES (1, 'شركة النور', %s, 'EGP') RETURNING id", [sgroup])}
    env["branch"] = db.fetch_value("INSERT INTO inv.warehouses (seq, name_ar) VALUES "
                                   "((SELECT max(seq) + 1 FROM inv.warehouses), 'مخزن الفرع') RETURNING id")
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


def buy(env, it=None, qty="10", price="100", wh=None, kind="invoice", d=D0):
    """شراء مرحّل عشان يبقى فيه رصيد بتكلفة."""
    p = env["purchase"]
    return p.save_draft(None, {"invoice_date": d, "supplier_id": env["sup"], "warehouse_id": wh or env["main"],
                               "currency_code": "EGP", "due_date": d, "stock_kind": kind,
                               "lines": [{"item_id": it or env["item"], "qty": qty, "unit_price": price}]}, post=True)


def line(env, it=None, qty="1", price="150", **kw):
    return {"item_id": it or env["item"], "qty": qty, "unit_price": price, "tax_code_id": env["vat14"], **kw}


def data(env, lines, cust=None, d=D1, **kw):
    return {"invoice_date": d, "customer_id": cust or env["cust"], "warehouse_id": env["main"], "currency_code": "EGP",
            "due_date": d + dt.timedelta(days=30), "lines": lines, **kw}


def deny(monkeypatch, *actions):
    real = SESSION.can
    codes = {f"{PERM}.{a}" for a in actions}
    monkeypatch.setattr(SESSION, "can", lambda code: code not in codes and real(code))


def entry(db, doc, sid):
    return db.fetch_all(
        "SELECT a.code, a.role_code, l.debit, l.credit, l.customer_id, l.currency_code, l.fx_rate, l.amount_fc, "
        "l.project_id, l.cost_center_id FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = %s AND e.source_id = %s "
        "AND e.status = 'posted' ORDER BY l.line_no", [doc, sid])


def stock(env, it=None, wh=None, kind="invoice"):
    return env["ledger"].available(it or env["item"], wh or env["main"], kind)


def avg(env, it=None, kind="invoice"):
    return env["ledger"].average_cost(it or env["item"], kind)


def by_role(lines):
    out: dict[str, list[tuple[Decimal, Decimal]]] = {}
    for r in lines:
        out.setdefault(r["role_code"] or r["code"], []).append((r["debit"], r["credit"]))
    return out


# ---- السكريبت ------------------------------------------------------------------
def test_migration_rules_permissions_and_indexes(env):
    db = env["db"]
    roles = [r["role"] for r in db.fetch_all("SELECT role FROM acc.posting_rules WHERE doc_type = 'sales_receipt' "
                                             "ORDER BY sort_order")]
    assert roles == ["cash", "bank", "receivable"]
    perms = {r["action_code"] for r in db.fetch_all(
        "SELECT action_code FROM sec.permissions WHERE module_code = 'sales' AND target_code = 'sales_invoices'")}
    assert perms == {"view", "create", "edit", "save", "delete", "export", "post", "unpost", "negative",
                     "over_credit", "show_cost"}
    missing = db.fetch_all("""
        SELECT c.conrelid::regclass::text AS tbl, a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.conrelid::regclass::text LIKE 'sales.%%'
          AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])""")
    assert missing == []                                    # كل FK عليه index (invoice_id أول الـ unique)


# ---- الحسبة -----------------------------------------------------------------
def test_calculate_discount_base_for_5303():
    lines = [{"qty": D_("10"), "unit_price": D_("100"), "discount_pct": D_("10"), "tax_rate": D_("14"),
              "currency_code": "EGP", "fx_rate": D_("1"), "factor": D_("1")}]
    c = calculate(lines, discount_amount=D_("50"))
    ln = c["lines"][0]
    assert (ln["line_discount"], ln["header_discount"]) == (D_("100.00"), D_("50.00"))
    assert ln["net_base"] == D_("850.00") and ln["discount_base"] == D_("150.00")
    assert ln["revenue_base"] == D_("1000.00") and c["total_base"] == D_("969.00")
    inc = calculate([{**lines[0], "unit_price": D_("114")}], prices_include_vat=True)
    # الخصم 114 شامل الضريبة ← 100 قبل الضريبة · المبيعات = 900 + 100 = 1000
    assert inc["lines"][0]["discount_base"] == D_("100.00") and inc["revenue_base"] == D_("1000.00")
    usd = calculate([{**lines[0], "currency_code": "USD", "fx_rate": D_("50")}], base_currency="EGP")
    assert usd["lines"][0]["discount_base"] == D_("5000.00") and usd["total_base"] == D_("51300.00")


# ---- الترحيل والقيد الواحد بالمتوسط -------------------------------------------------
def test_post_stock_out_at_weighted_average_one_balanced_entry(env):
    s, db = env["service"], env["db"]
    buy(env, qty="10", price="100")
    buy(env, qty="10", price="130", d=D0 + dt.timedelta(days=1))
    assert avg(env) == D_("115")
    iid = s.save_draft(None, data(env, [line(env, qty="4", price="200", discount_pct="10"),
                                        line(env, env["svc"], "1", "500")], discount_amount="20"))
    rec = s.get_invoice(iid)
    assert rec["status"] == "draft" and rec["invoice_no"] is None and stock(env) == 20
    # 800 − 80 = 720 · 500 ← 1220 − 20 = 1200 → + 14% = 1368
    assert rec["net_base"] == D_("1200.00") and rec["total_base"] == D_("1368.00") and rec["discount_base"] == D_("100.00")
    no = s.post(iid, expected_version=rec["row_version"])
    assert no == "INV-2026-00001"
    assert stock(env) == 16 and avg(env) == D_("115")                      # الخارج بالمتوسط مابيغيّروش
    mv = s.movements(iid)
    assert len(mv) == 1 and mv[0]["booked_value"] == D_("-460.00")       # 4 × 115
    lines = entry(db, "sales_invoice", iid)
    assert sum(r["debit"] for r in lines) == sum(r["credit"] for r in lines) == D_("1368.00") + D_("100.00") + D_("460.00")
    roles = by_role(lines)
    assert roles["customers"] == [(D_("1368.00"), 0)] and lines[0]["customer_id"] == env["cust"]
    assert roles["disc_allowed"] == [(D_("100.00"), 0)]
    assert roles["sales"] == [(0, D_("800.00"))]                         # البضاعة بالإجمالي قبل الخصم
    revenue = sum(r["credit"] for r in lines if r["code"].startswith("41"))
    assert revenue == D_("1300.00")                                       # بالإجمالي قبل الخصم
    assert any(r["code"] == "4102" and r["credit"] for r in lines)        # الخدمي على «إيراد الخدمات»
    assert roles["vat_out"] == [(0, D_("168.00"))]
    assert roles["cogs"] == [(D_("460.00"), 0)] and roles["inventory"] == [(0, D_("460.00"))]
    assert s.customer_balance(env["cust"])["base"] == D_("1368.00")
    with pytest.raises(BusinessError, match="مرحّلة"):
        s.save_draft(iid, data(env, [line(env)]))


def test_average_restarts_after_stock_reaches_zero(env):
    """رد المستخدم (2026-11-28): البيع بالمتوسط طول ما فيه رصيد — ولما الرصيد يصفّر ويتشترى تاني، المتوسط يبدأ من
    الأول بسعر الشراء الجديد (مفيش أثر للأسعار القديمة)."""
    s, db = env["service"], env["db"]
    buy(env, qty="10", price="105", d=dt.date(2026, 1, 5))
    buy(env, qty="10", price="106", d=dt.date(2026, 1, 6))
    buy(env, qty="10", price="106.5", d=dt.date(2026, 1, 7))
    assert avg(env) == D_("105.833333")
    a = s.save_draft(None, data(env, [line(env, qty="12")], d=dt.date(2026, 1, 10)), post=True)
    b = s.save_draft(None, data(env, [line(env, qty="18")], d=dt.date(2026, 1, 11)), post=True)
    assert stock(env) == 0
    # البيعتين بالمتوسط، والأخيرة اللي صفّرت الرصيد خدت الباقي كله (مفيش قروش معلّقة)
    cost_a, cost_b = -s.movements(a)[0]["booked_value"], -s.movements(b)[0]["booked_value"]
    assert cost_a == D_("1270.00") and cost_a + cost_b == D_("3175.00")         # 1050 + 1060 + 1065
    buy(env, qty="5", price="170", d=dt.date(2026, 2, 1))                       # رجعت اشتريته بعد ما صفّر
    assert avg(env) == D_("170")
    c = s.save_draft(None, data(env, [line(env, qty="2", price="250")], d=dt.date(2026, 2, 2)), post=True)
    assert -s.movements(c)[0]["booked_value"] == D_("340.00")                    # أول بيع = سعر الشراء الجديد
    assert by_role(entry(db, "sales_invoice", c))["cogs"] == [(D_("340.00"), 0)]
    buy(env, qty="3", price="180", d=dt.date(2026, 2, 3))                       # شراء والرصيد لسه موجود ← متوسط عادي
    assert avg(env) == D_("175")                                                 # (3 × 170 + 3 × 180) ÷ 6


def test_same_day_follows_posting_order(env):
    """رد المستخدم (2026-11-28 — «ليه 108»): كله في نفس اليوم بترتيب الترحيل: 10 × 100 ← 5 × 115 ← بيع 15 ← شراء
    1 × 160 ← البيع بـ 105 (1,575 زي قيده) والقطعة الباقية بـ 160 مش 108.44 · ومفيش فروق تكلفة."""
    s, db, ledger = env["service"], env["db"], env["ledger"]
    buy(env, qty="10", price="100", d=D1)
    buy(env, qty="5", price="115", d=D1)
    sale = s.save_draft(None, data(env, [line(env, qty="15", price="200")], d=D1), post=True)
    buy(env, qty="1", price="160", d=D1)
    mv = s.movements(sale)[0]
    assert mv["value"] == mv["booked_value"] == D_("-1575.00")                  # الشراء اللي بعده مالمسهوش
    assert avg(env) == D_("160") and stock(env) == 1
    assert not [d for d in ledger.cost_differences() if d["source_type"] == "sales_invoice"]
    r = s.review(data(env, [line(env, qty="1", price="120")], d=D1))
    assert r["profit"]["cost"] == D_("160.00") and r["profit"]["profit"] == D_("-40.00")
    assert any("بخسارة" in c[1] for c in r["checks"])
    # بيع اترحّل قبل شراء نفس اليوم (بصلاحية «من غير رصيد» — الأدمن) ← تكلفته بتتصلّح بسعر الشراء ده
    early = s.save_draft(None, data(env, [line(env, qty="3", price="300")], d=D2), post=True)
    buy(env, qty="5", price="170", d=D2)
    assert -s.movements(early)[0]["value"] == D_("500.00")                      # 160 + 2 × 170
    assert stock(env) == 3 and avg(env) == D_("170")


def test_recompute_request_fixes_old_same_day_order(env):
    """0052: طلب إعادة الحساب بيتنفذ مرة واحدة ويصلّح الحركات اللي اتحسبت بالترتيب القديم."""
    db, ledger = env["db"], env["ledger"]
    buy(env, qty="10", price="100", d=D1)
    sale = env["service"].save_draft(None, data(env, [line(env, qty="10")], d=D1), post=True)
    buy(env, qty="1", price="160", d=D1)
    # نرجّع الحسبة لشكل الترتيب القديم (البيع بمتوسط الـ 11 قطعة) ونطلب إعادة الحساب
    db.execute("UPDATE inv.stock_movements SET value = -1054.55, unit_cost = 105.454545 "
               "WHERE source_type = 'sales_invoice' AND source_id = %s", [sale])
    db.execute("INSERT INTO inv.stock_recompute_requests (reason) VALUES ('اختبار')")
    assert ledger.run_recompute_requests() == 1
    assert env["service"].movements(sale)[0]["value"] == D_("-1000.00") and avg(env) == D_("160")
    assert db.fetch_value("SELECT count(*) FROM inv.stock_recompute_requests WHERE done_at IS NULL") == 0
    assert ledger.run_recompute_requests() == 0                                  # مرة واحدة بس


def test_cash_receipt_and_cash_customer_cash_only(env):
    s, db = env["service"], env["db"]
    buy(env)
    with pytest.raises(BusinessError, match="نقدي بس"):
        s.save_draft(None, data(env, [line(env)], cust=env["cash_cust"]))
    iid = s.save_draft(None, data(env, [line(env, qty="2", price="500")], cust=env["cash_cust"], pay_kind="cash",
                                  source_kind="treasury", treasury_id=env["treasury"]), post=True)
    rec = s.get_invoice(iid)
    assert rec["due_date"] is None and rec["pay_kind"] == "cash"
    rc = entry(db, "sales_receipt", iid)
    assert [(r["debit"], r["credit"]) for r in rc] == [(D_("1140.00"), 0), (0, D_("1140.00"))]
    assert rc[1]["customer_id"] == env["cash_cust"]
    assert s.customer_balance(env["cash_cust"])["base"] == 0
    s.unpost(iid)
    assert entry(db, "sales_receipt", iid) == [] and entry(db, "sales_invoice", iid) == []
    assert stock(env) == 10 and s.get_invoice(iid)["invoice_no"] == "INV-2026-00001"
    with pytest.raises(BusinessError, match="الخزنة"):
        s.save_draft(None, data(env, [line(env)], pay_kind="cash"))


def test_oversell_blocked_without_negative_permission(env, monkeypatch):
    s = env["service"]
    buy(env, qty="5", price="100")
    iid = s.save_draft(None, data(env, [line(env, qty="7")]))
    deny(monkeypatch, "negative")
    r = s.review(data(env, [line(env, qty="7")]), iid)
    assert not r["ready"] and any("من غير رصيد" in c[1] for c in r["checks"] if c[0] == "no")
    assert r["stock"][0]["qty_before"] == 5 and r["stock"][0]["qty_after"] == -2
    with pytest.raises(NegativeStockError):
        s.post(iid)
    rec = s.get_invoice(iid)
    assert rec["status"] == "draft" and rec["invoice_no"] is None and stock(env) == 5     # كله رجع
    monkeypatch.undo()
    r = s.review(data(env, [line(env, qty="7")]), iid)
    assert r["ready"] and any(c[0] == "warn" and "من غير رصيد" in c[1] for c in r["checks"])
    s.post(iid)                                              # الأدمن عنده «من غير رصيد»
    assert stock(env) == -2
    with pytest.raises(BusinessError, match="وهمي"):
        s.save_draft(None, data(env, [line(env, warehouse_id=env["virtual"])]))


def test_credit_limit_needs_permission(env, monkeypatch):
    s, db = env["service"], env["db"]
    buy(env, qty="50")
    db.execute("UPDATE party.customers SET credit_limit = 2000 WHERE id = %s", [env["cust"]])
    first = s.save_draft(None, data(env, [line(env, qty="10", price="150")]), post=True)     # 1,710
    assert s.customer_balance(env["cust"])["base"] == D_("1710.00")
    deny(monkeypatch, "over_credit")
    second = s.save_draft(None, data(env, [line(env, qty="2", price="150")]))               # 342 ← 2,052
    r = s.review(data(env, [line(env, qty="2", price="150")]), second)
    assert not r["ready"] and r["customer"]["limit"] == 2000
    assert any("حد الائتمان" in c[1] for c in r["checks"] if c[0] == "no")
    with pytest.raises(BusinessError, match="تجاوز حد الائتمان"):
        s.post(second)
    assert s.get_invoice(second)["status"] == "draft" and stock(env) == 40
    # النقدي مابيتحسبش
    s.save_draft(None, data(env, [line(env, qty="2", price="150")], pay_kind="cash", source_kind="treasury",
                            treasury_id=env["treasury"]), post=True)
    monkeypatch.undo()
    s.post(second)                                            # بالصلاحية بيعدّي
    assert s.customer_balance(env["cust"])["base"] == D_("2052.00")
    assert s.get_invoice(first)["status"] == "posted"


def test_multi_currency_receivable_line_per_currency(env):
    s, db = env["service"], env["db"]
    buy(env, qty="10", price="1000")
    buy(env, env["item2"], qty="10", price="20")
    lines = [line(env, qty="2", price="100", currency_code="USD", fx_rate="50", tax_code_id=env["vat0"]),
             line(env, env["item2"], "1", "10", currency_code="EUR", fx_rate="52", tax_code_id=env["vat0"]),
             line(env, env["item2"], "1", "100", currency_code="EGP")]
    iid = s.save_draft(None, data(env, lines, cust=env["usd_cust"], currency_mode="multi", currency_code="USD"),
                       post=True)
    rec = s.get_invoice(iid)
    assert rec["currency_mode"] == "multi" and rec["currency_code"] == "EGP" and rec["fx_rate"] == 1
    assert rec["net_base"] == D_("10620.00") and rec["total_base"] == D_("10634.00")    # 10000 + 520 + 100 + 14
    rcv = [r for r in entry(db, "sales_invoice", iid) if r["customer_id"]]
    assert {(r["currency_code"], r["amount_fc"], r["debit"]) for r in rcv} == {
        ("USD", D_("200.00"), D_("10000.00")), ("EUR", D_("10.00"), D_("520.00")), (None, None, D_("114.00"))}
    by = {r["currency_code"]: r["amount"] for r in s.customer_balance(env["usd_cust"])["by_currency"]}
    assert by == {"USD": D_("200.00"), "EUR": D_("10.00"), "EGP": D_("114.00")}
    lines_all = entry(db, "sales_invoice", iid)
    assert sum(r["debit"] for r in lines_all) == sum(r["credit"] for r in lines_all)
    assert by_role(lines_all)["cogs"] == [(D_("2040.00"), 0)]                       # 2 × 1000 + 2 × 20
    with pytest.raises(BusinessError, match="مالهاش خصم فاتورة"):
        s.save_draft(None, data(env, lines, currency_mode="multi", discount_amount="10"))


def test_single_foreign_currency_defaults_and_price(env):
    s, db = env["service"], env["db"]
    buy(env)
    d = s.customer_defaults(env["usd_cust"], D1)
    assert d["currency_code"] == "USD" and d["fx_rate"] == D_("48.5") and d["tax_code_id"] == env["vat0"]
    assert d["due_date"] == D1 + dt.timedelta(days=30) and not d["is_cash"]
    assert s.default_price(env["item"], 1, d["fx_rate"]) == D_("3.09")              # 150 ÷ 48.5
    box = db.fetch_value("SELECT id FROM inv.units WHERE NOT is_default ORDER BY id LIMIT 1")
    db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 12)", [env["item"], box])
    assert s.default_price(env["item"], 12, 1) == D_("1800.00")                       # سعر البيع × المعامل
    iid = s.save_draft(None, data(env, [line(env, qty="2", price="10", tax_code_id=env["vat0"])],
                                  cust=env["usd_cust"], currency_code="USD", fx_rate="48.3"), post=True)
    rcv = [r for r in entry(db, "sales_invoice", iid) if r["customer_id"]][0]
    assert (rcv["currency_code"], rcv["fx_rate"], rcv["amount_fc"], rcv["debit"]) == ("USD", D_("48.3"), D_("20.00"),
                                                                                       D_("966.00"))
    nop = item(db, "صنف من غير سعر")
    assert s.default_price(nop, 1, 1, env["cust"]) is None


def test_unpost_einvoice_after_post_and_delete(env):
    s, db = env["service"], env["db"]
    buy(env)
    iid = s.save_draft(None, data(env, [line(env, qty="3")]), post=True)
    assert stock(env) == 7
    s.set_e_invoice_no(iid, " EG-777 ")
    rec = s.get_invoice(iid)
    assert rec["e_invoice_no"] == "EG-777" and rec["status"] == "posted"
    with pytest.raises(psycopg.errors.RaiseException, match="الرقم الإلكتروني بس"):
        db.execute("UPDATE sales.sales_invoices SET description = 'x' WHERE id = %s", [iid])
    s.unpost(iid, expected_version=rec["row_version"])
    rec = s.get_invoice(iid)
    assert rec["status"] == "draft" and rec["invoice_no"] == "INV-2026-00001" and stock(env) == 10
    with pytest.raises(BusinessError, match="رقم رسمي"):
        s.delete_invoice(iid)
    s.save_draft(iid, data(env, [line(env, qty="4")]), expected_version=rec["row_version"], post=True)
    assert s.get_invoice(iid)["invoice_no"] == "INV-2026-00001" and stock(env) == 6
    draft = s.save_draft(None, data(env, [line(env)]))
    s.delete_invoice(draft)
    assert s.get_invoice(draft) is None


def test_project_dimension_and_customer_match(env):
    s, db = env["service"], env["db"]
    buy(env)
    center = db.fetch_value("SELECT id FROM acc.cost_centers WHERE is_leaf AND is_active ORDER BY code LIMIT 1")
    pid = db.fetch_value("INSERT INTO acc.projects (code, name, cost_center_id, currency_code, customer_id, amount) "
                         "VALUES ('Itec-900', 'برج النخيل', %s, 'EGP', %s, 200) RETURNING id", [center, env["cust"]])
    r = s.review(data(env, [line(env, qty="2", price="150")], project_id=pid))
    assert any(c[0] == "warn" and "أمر التوريد" in c[1] for c in r["checks"])     # 300 > 200 تنبيه بس
    iid = s.save_draft(None, data(env, [line(env, qty="2", price="150")], project_id=pid), post=True)
    lines = entry(db, "sales_invoice", iid)
    assert all(r["project_id"] == pid for r in lines)
    assert all(r["cost_center_id"] == center for r in lines if r["code"].startswith(("4", "5")))
    with pytest.raises(BusinessError, match="لعميل تاني"):
        s.save_draft(None, data(env, [line(env)], cust=env["usd_cust"], currency_code="USD", fx_rate="48.5",
                                project_id=pid))


def test_database_guards(env):
    s, db = env["service"], env["db"]
    buy(env)
    iid = s.save_draft(None, data(env, [line(env)]))
    with pytest.raises(psycopg.errors.RaiseException, match="وهمي"):
        db.execute("UPDATE sales.sales_invoice_lines SET warehouse_id = %s WHERE invoice_id = %s",
                   [env["virtual"], iid])
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE sales.sales_invoice_lines SET net_base = net_base + 1 WHERE invoice_id = %s", [iid])
    with pytest.raises(psycopg.errors.RaiseException, match="من غير حركات المخزون|من غير قيدها"):
        db.execute("UPDATE sales.sales_invoices SET status = 'posted' WHERE id = %s", [iid])
    with pytest.raises(psycopg.errors.RaiseException, match="نقدي بس"):
        db.execute("UPDATE sales.sales_invoices SET customer_id = %s WHERE id = %s", [env["cash_cust"], iid])
    s.post(iid)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة"):
        db.execute("UPDATE sales.sales_invoice_lines SET qty = 1 WHERE invoice_id = %s", [iid])
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة"):
        db.execute("DELETE FROM sales.sales_invoices WHERE id = %s", [iid])
    with pytest.raises(psycopg.errors.RaiseException, match="سند مسودة"):
        s.save_draft(None, data(env, [line(env)]))
        db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2026'")


def test_cost_and_profit_need_permission(env, monkeypatch):
    s = env["service"]
    buy(env, qty="10", price="100")
    r = s.review(data(env, [line(env, qty="2", price="90")]))
    assert r["profit"]["cost"] == D_("200.00") and r["profit"]["profit"] == D_("-20.00")
    assert any("بخسارة" in c[1] for c in r["checks"] if c[0] == "warn")
    assert any(e["role_name"] == "تكلفة المبيعات" for e in r["entry"])
    deny(monkeypatch, "show_cost")
    r = s.review(data(env, [line(env, qty="2", price="90")]))
    assert r["profit"] is None and not any(e["role_name"] == "تكلفة المبيعات" for e in r["entry"])
    assert s.item_info(env["item"], env["main"])["avg"] is None and s.item_info(env["item"], env["main"])["available"] == 10


def test_two_devices_same_item_same_customer_same_invoice(env, base_settings, monkeypatch):
    """جهازين بيبيعوا نفس الصنف (الرصيد 10 والاتنين 6) من غير «من غير رصيد» ← واحد بس يعدّي ·
    جهازين بيرحّلوا نفس الفاتورة ← واحد بس · حد الائتمان بيتحسب جوه قفل العميل."""
    from dataclasses import replace
    import threading

    s, db = env["service"], env["db"]
    buy(env, qty="10", price="100")
    deny(monkeypatch, "negative", "over_credit")
    a = s.save_draft(None, data(env, [line(env, qty="6")]))
    b = s.save_draft(None, data(env, [line(env, qty="6")]))
    other = Database(replace(base_settings, db_name=db.settings.db_name))
    results: list[str] = []

    def run(service, iid, version=None):
        try:
            service.post(iid, expected_version=version)
            results.append("ok")
        except ConcurrentEditError:
            results.append("conflict")
        except BusinessError as exc:
            results.append(f"err:{exc}")

    try:
        threads = [threading.Thread(target=run, args=(s, a)),
                   threading.Thread(target=run, args=(SalesInvoiceService(other), b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(r[:3] for r in results) == ["err", "ok"] and any("بالسالب" in r for r in results)
        assert stock(env) == 4
        # حد الائتمان: الرصيد 1,026 والحد 1,500 ← فاتورتين 342 كل واحدة: واحدة بس تعدّي
        db.execute("UPDATE party.customers SET credit_limit = 1500 WHERE id = %s", [env["cust"]])
        buy(env, qty="10", price="100")
        results.clear()
        c = s.save_draft(None, data(env, [line(env, qty="2")]))
        e = s.save_draft(None, data(env, [line(env, qty="2")]))
        threads = [threading.Thread(target=run, args=(s, c)),
                   threading.Thread(target=run, args=(SalesInvoiceService(other), e))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(r[:3] for r in results) == ["err", "ok"] and any("حد ائتمان" in r for r in results)
        # نفس الفاتورة من جهازين
        results.clear()
        monkeypatch.undo()
        f = s.save_draft(None, data(env, [line(env, qty="1")]))
        v = s.get_invoice(f)["row_version"]
        threads = [threading.Thread(target=run, args=(s, f, v)),
                   threading.Thread(target=run, args=(SalesInvoiceService(other), f, v))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        other.close()
    assert "ok" in results and results.count("ok") == 1
    assert s.get_invoice(f)["status"] == "posted"


def test_list_search_and_concurrent_edit(env):
    s = env["service"]
    buy(env)
    iid = s.save_draft(None, data(env, [line(env)], customer_ref="PO-55"))
    v = s.get_invoice(iid)["row_version"]
    s.save_draft(iid, data(env, [line(env, qty="3")], customer_ref="PO-55"), expected_version=v)
    with pytest.raises(ConcurrentEditError):
        s.save_draft(iid, data(env, [line(env, qty="4")]), expected_version=v)
    s.post(iid)
    assert s.count_invoices(D1, D3)["total"] == 1
    assert [r["invoice_no"] for r in s.list_invoices(D1, D3, keyword="INV-2026-00001")] == ["INV-2026-00001"]
    assert len(s.list_invoices(D1, D3, keyword="PO-55")) == 1 and len(s.list_invoices(D1, D3, keyword="كابل")) == 1


# ---- الشاشة (النموذج 1) ---------------------------------------------------------
def _screen(env):
    from app.ui.screens.sales_invoices_screen import SalesInvoicesScreen

    return SalesInvoicesScreen(service=env["service"])


def test_screen_multi_select_edit_in_table_save_post(env):
    from app.ui.screens.sales_invoices_screen import C_PRICE, C_QTY, C_TOTAL

    buy(env, qty="20")
    buy(env, env["item2"], qty="20", price="10")
    screen = _screen(env)
    screen.new_record()
    assert screen.mode == "new" and screen.wh_combo.currentData() == env["main"]
    screen.customer_edit.set_value(env["cust"])
    assert screen.due_edit.date() == screen.date_edit.date().addDays(30)
    assert "من غير حد" in screen.credit_edit.text()
    assert screen.add_items([env["item"], env["item2"], env["svc"]]) == 3       # الاختيار المتعدد
    assert [ln["unit_price"] for ln in screen._lines] == [D_("150.00"), D_("40.00"), D_("500.00")]
    assert screen._lines[2]["warehouse_id"] is None and screen._lines[0]["warehouse_id"] == env["main"]
    t = screen.lines_table
    assert t.rowCount() == 3 and bool(t.item(0, C_QTY).flags() & Qt.ItemIsEditable)            # الكمية بتتعدّل في الجدول
    t.item(0, C_QTY).setText("4")                                              # تعديل الكمية في الجدول
    t.item(1, C_PRICE).setText("45")
    assert screen._lines[0]["qty"] == 4 and screen._lines[1]["unit_price"] == 45
    screen._refresh_review()
    assert t.item(0, C_TOTAL).text() == "684.00"                               # 4 × 150 × 1.14
    assert screen.net_value.text() == "1,305.30"                               # (600 + 45 + 500) × 1.14
    assert screen.profit_row.isVisibleTo(screen) and "·" in screen.profit_value.text()
    screen.save()
    rec = screen.current_record
    assert screen.mode == "view" and rec["status"] == "draft" and len(rec["lines"]) == 3
    assert not bool(t.item(0, C_QTY).flags() & Qt.ItemIsEditable)                              # العرض = مقفول
    screen.post_invoice()
    rec = screen.current_record
    assert rec["status"] == "posted" and rec["invoice_no"] == "INV-2026-00001" and stock(env) == 16
    assert screen.einv_button.isEnabled() and not screen.edit_button.isEnabled()
    screen.unpost_invoice()
    assert screen.current_record["status"] == "draft" and stock(env) == 20
    screen.close()
    screen.deleteLater()


def test_screen_unit_combo_currency_and_cash_customer(env):
    from app.ui.screens.sales_invoices_screen import C_CUR, C_UNIT, MULTI

    db = env["db"]
    buy(env, qty="50")
    box = db.fetch_value("SELECT id FROM inv.units WHERE NOT is_default ORDER BY id LIMIT 1")
    db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 10)", [env["item"], box])
    screen = _screen(env)
    screen.refresh_screen()
    screen.new_record()
    screen.customer_edit.set_value(env["cash_cust"])
    assert screen.pay_combo.currentData() == "cash"                            # «عميل نقدي» ← نقدي
    screen.customer_edit.set_value(env["usd_cust"])
    assert screen.currency_combo.currentData() == "USD" and screen.rate_edit.text() == "48.5"
    screen._set_combo(screen.pay_combo, "credit")
    screen.add_items([env["item"]])
    assert screen._lines[0]["unit_price"] == D_("3.09")
    combo = screen.lines_table.cellWidget(0, C_UNIT)
    assert combo is not None and combo.count() == 2
    combo.setCurrentIndex(combo.findData(box))                                  # الوحدة من الجدول
    assert screen._lines[0]["unit_id"] == box and screen._lines[0]["unit_price"] == D_("30.90")
    screen._set_combo(screen.currency_combo, MULTI)
    screen._refresh_review()
    cur = screen.lines_table.cellWidget(0, C_CUR)
    assert cur is not None
    cur.setCurrentIndex(cur.findData("EUR"))                                    # العملة من الجدول
    assert screen._lines[0]["currency_code"] == "EUR" and screen._lines[0]["fx_rate"] == D_("52.8")
    assert screen._lines[0]["unit_price"] == D_("28.41")                        # 150 × 10 ÷ 52.8
    screen.save()
    rec = screen.current_record
    assert rec["currency_mode"] == "multi" and rec["lines"][0]["currency_code"] == "EUR"
    assert rec["lines"][0]["base_qty"] == 10
    screen.close()
    screen.deleteLater()
