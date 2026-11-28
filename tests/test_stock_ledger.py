"""اختبارات «دفتر المخزون» (0043 + stock_ledger_service): المتوسط المرجّح المستمر لكل (صنف + فاتورة/بيان) · ترتيب
نفس اليوم · آخر خروج بياخد الباقي · الرصيد السالب ممنوع في أي يوم · الحركة بتاريخ قديم بتعيد الحساب وفروق التكلفة ·
التحويل (نفس النوع ومن نوع لنوع) · المخزن الوهمي · إعادة الترحيل والمسح · حماية القاعدة · جهازين في نفس اللحظة."""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal

import psycopg
import pytest

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService
from app.services.stock_ledger_service import StockLine as S

D1, D2, D3, D4, D5 = (dt.date(2026, 3, d) for d in (1, 2, 3, 4, 5))


@pytest.fixture()
def env(temp_db):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    db = temp_db
    env = {"db": db, "ledger": StockLedgerService(db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE NOT is_virtual ORDER BY seq LIMIT 1"),
           "virtual": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_virtual ORDER BY seq LIMIT 1"),
           "item": item(db, "كابل نحاس"), "item2": item(db, "مفتاح")}
    env["branch"] = db.fetch_value("INSERT INTO inv.warehouses (seq, name_ar) VALUES "
                                   "((SELECT max(seq) + 1 FROM inv.warehouses), 'مخزن الفرع') RETURNING id")
    yield env
    SESSION.clear()


def item(db, name, service=False):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id, is_service) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14'), %s) "
        "RETURNING id", [name, service])


def post(env, doc_id, lines, d=D1, doc="purchase_invoice", no=None, db=None, allow=False):
    db = db or env["db"]
    ledger = StockLedgerService(db) if db is not env["db"] else env["ledger"]
    with db.atomic() as tx:
        return ledger.post(tx, doc_type=doc, source_id=doc_id, source_no=no or f"{doc}-{doc_id}",
                           movement_date=d, lines=lines, allow_negative=allow)


def remove(env, doc_id, doc="purchase_invoice", allow=False):
    with env["db"].atomic() as tx:
        return env["ledger"].remove(tx, doc_type=doc, source_id=doc_id, allow_negative=allow)


def buy(env, doc_id, qty, cost, d=D1, wh=None, kind="invoice", it=None):
    return post(env, doc_id, [S(it or env["item"], wh or env["main"], qty, kind, unit_cost=cost)], d)


def sell(env, doc_id, qty, d=D1, wh=None, kind="invoice", it=None, allow=False):
    return post(env, doc_id, [S(it or env["item"], wh or env["main"], -Decimal(str(qty)), kind)], d,
                doc="sales_invoice", allow=allow)


def cost_row(env, kind="invoice", it=None):
    return env["db"].fetch_one("SELECT qty, value, avg_cost FROM inv.item_costs WHERE item_id = %s AND stock_kind = %s",
                               [it or env["item"], kind])


def wh_qty(env, wh, kind="invoice", it=None):
    return env["db"].fetch_value("SELECT coalesce(sum(qty), 0) FROM inv.stock_balances WHERE item_id = %s "
                                 "AND warehouse_id = %s AND stock_kind = %s", [it or env["item"], wh, kind])


def check_books(env):
    """الأرصدة = مجموع الدفتر · قيمة كل نوع = مجموع قيم حركاته."""
    db = env["db"]
    bad = db.fetch_all(
        "SELECT b.* FROM inv.stock_balances b FULL JOIN (SELECT item_id, warehouse_id, stock_kind, sum(qty) q "
        "FROM inv.stock_movements GROUP BY 1, 2, 3 HAVING sum(qty) <> 0) m USING (item_id, warehouse_id, stock_kind) "
        "WHERE b.qty IS DISTINCT FROM m.q")
    assert bad == []
    bad = db.fetch_all(
        "SELECT c.* FROM inv.item_costs c JOIN (SELECT item_id, stock_kind, sum(value) v, sum(qty) q "
        "FROM inv.stock_movements WHERE NOT is_virtual GROUP BY 1, 2) m USING (item_id, stock_kind) "
        "WHERE c.value <> m.v OR c.qty <> m.q")
    assert bad == []


# ---- المتوسط المرجّح -----------------------------------------------------------------
def test_weighted_average(env):
    buy(env, 1, 10, "100", D1)
    out = buy(env, 2, 10, "130", D2)
    assert out[0]["value"] == Decimal("1300.00") and out[0]["booked_value"] == Decimal("1300.00")
    c = cost_row(env)
    assert (c["qty"], c["value"], c["avg_cost"]) == (20, Decimal("2300.00"), Decimal("115"))
    s = sell(env, 10, 5, D3)
    assert s[0]["unit_cost"] == Decimal("115") and s[0]["value"] == Decimal("-575.00")
    c = cost_row(env)
    assert (c["qty"], c["value"], c["avg_cost"]) == (15, Decimal("1725.00"), Decimal("115"))
    # الخارج مابيغيّرش المتوسط · الداخل بعده بيغيّره
    buy(env, 3, 5, "135", D4)
    assert cost_row(env)["avg_cost"] == Decimal("120")
    assert env["ledger"].average_cost(env["item"], "invoice", on_date=D3) == Decimal("115")
    assert env["ledger"].average_cost(env["item"]) == Decimal("120")
    m = env["db"].fetch_one("SELECT kind_qty_after, kind_value_after, avg_cost_after, wh_qty_after "
                            "FROM inv.stock_movements WHERE source_type = 'sales_invoice' AND source_id = 10")
    assert (m["kind_qty_after"], m["kind_value_after"], m["avg_cost_after"], m["wh_qty_after"]) == \
        (15, Decimal("1725.00"), Decimal("115"), 15)
    check_books(env)


def test_last_out_takes_remaining_value(env):
    buy(env, 1, 3, "3.333333")                      # 10.00
    a = sell(env, 10, 1, D2)[0]["value"]
    b = sell(env, 11, 1, D2)[0]["value"]
    c = sell(env, 12, 1, D3)[0]["value"]
    assert (a, b) == (Decimal("-3.33"), Decimal("-3.33")) and c == Decimal("-3.34")
    row = cost_row(env)
    assert row["qty"] == 0 and row["value"] == 0
    check_books(env)


def test_same_day_follows_posting_order(env):
    # 0052 (رد المستخدم 2026-11-28): جوه نفس اليوم بترتيب الترحيل — الصرف اللي صفّر الرصيد مايتأثرش بشراء اتسجل
    # بعده في نفس اليوم، والمتوسط بيبدأ من الأول بالسعر الجديد.
    buy(env, 1, 5, "10", D1)
    with pytest.raises(BusinessError):
        sell(env, 10, 8, D1)                        # لسه مفيش غير 5
    sell(env, 10, 5, D2)
    buy(env, 2, 5, "20", D2)                        # نفس يوم الصرف واتسجل بعده
    row = env["db"].fetch_one("SELECT unit_cost FROM inv.stock_movements WHERE source_type = 'sales_invoice'")
    assert row["unit_cost"] == Decimal("10")        # طلع بالمتوسط وقت ترحيله
    assert cost_row(env)["avg_cost"] == Decimal("20")
    # صرف أكبر من الرصيد اترحّل قبل شراء نفس اليوم (بصلاحية «من غير رصيد» — الشراء لسه مااتسجلش) ← تكلفته
    # بتتصلّح بسعر الشراء اللي غطّاه
    sell(env, 11, 8, D3, allow=True)                # الرصيد 5 بـ 20 ← 3 ناقصين
    buy(env, 3, 10, "26", D3)
    row = env["db"].fetch_one("SELECT unit_cost FROM inv.stock_movements WHERE source_type = 'sales_invoice' "
                              "AND source_id = 11")
    assert row["unit_cost"] == Decimal("22.25")     # (5×20 + 3×26) / 8
    assert cost_row(env)["avg_cost"] == Decimal("26")
    check_books(env)


# ---- الرصيد السالب ------------------------------------------------------------------
def test_negative_forbidden_any_day(env):
    buy(env, 1, 10, "100", D1)
    sell(env, 10, 8, D4)
    buy(env, 2, 10, "100", D5)
    # النهارده الرصيد 12، بس صرف 5 يوم 3 يخلّي آخر يوم 4 = −3
    with pytest.raises(BusinessError, match="بالسالب آخر يوم 04/03/2026.*ناقص 3"):
        sell(env, 11, 5, D3)
    assert env["ledger"].available(env["item"], env["main"], "invoice", on_date=D3) == 2
    assert env["ledger"].available(env["item"], env["main"], "invoice", on_date=D5) == 12
    assert env["ledger"].available(env["item"]) == 12
    sell(env, 11, 2, D3)
    # مسح الشراء اللي اتصرف منه ممنوع
    with pytest.raises(BusinessError, match="بالسالب"):
        remove(env, 1)
    assert wh_qty(env, env["main"]) == 10
    check_books(env)


def test_invoice_and_statement_are_separate(env):
    buy(env, 1, 10, "100", kind="invoice")
    with pytest.raises(BusinessError, match="«بيان»"):
        sell(env, 10, 1, D2, kind="statement")
    buy(env, 2, 4, "50", kind="statement")
    sell(env, 10, 2, D2, kind="statement")
    assert cost_row(env, "invoice")["avg_cost"] == 100 and cost_row(env, "statement")["avg_cost"] == 50
    assert env["ledger"].available(env["item"]) == 12
    assert env["ledger"].available(env["item"], stock_kind="statement") == 2
    check_books(env)


# ---- الحركة بتاريخ قديم ---------------------------------------------------------------
def test_backdated_purchase_recomputes_and_shows_difference(env):
    buy(env, 1, 10, "100", D1)
    sell(env, 10, 5, D3)                            # 500 بالمتوسط 100
    buy(env, 2, 10, "160", D2)                      # قبل الصرف ← المتوسط يوم 3 = 130
    s = env["db"].fetch_one("SELECT unit_cost, value, booked_value FROM inv.stock_movements "
                            "WHERE source_type = 'sales_invoice'")
    assert (s["unit_cost"], s["value"], s["booked_value"]) == (Decimal("130"), Decimal("-650.00"),
                                                               Decimal("-500.00"))
    diff = env["ledger"].cost_differences()
    assert [(r["source_type"], r["difference"]) for r in diff] == [("sales_invoice", Decimal("-150.00"))]
    assert cost_row(env)["value"] == Decimal("1950.00")
    # إلغاء الشراء القديم ← التكلفة ترجع زي ما اتقيّدت
    remove(env, 2)
    assert env["ledger"].cost_differences() == []
    check_books(env)


def test_repost_replaces_movements(env):
    buy(env, 1, 10, "100", D1)
    post(env, 1, [S(env["item"], env["main"], 4, unit_cost="100"),
                  S(env["item2"], env["main"], 3, unit_cost="7")], D2)
    assert wh_qty(env, env["main"]) == 4 and wh_qty(env, env["main"], it=env["item2"]) == 3
    assert env["db"].fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_id = 1") == 2
    post(env, 1, [S(env["item2"], env["main"], 5, unit_cost="7")], D2)   # الصنف الأول اتشال من المستند
    assert wh_qty(env, env["main"]) == 0 and wh_qty(env, env["main"], it=env["item2"]) == 5
    assert cost_row(env) is None
    assert remove(env, 1) and not remove(env, 1)
    check_books(env)


# ---- التحويل --------------------------------------------------------------------------
def test_transfer_same_kind_keeps_average(env):
    buy(env, 1, 10, "100")
    out = post(env, 50, [S(env["item"], env["main"], -4), S(env["item"], env["branch"], 4, transfer_of=0)], D2,
               doc="transfer")
    assert [r["value"] for r in out] == [Decimal("-400.00"), Decimal("400.00")]
    assert (wh_qty(env, env["main"]), wh_qty(env, env["branch"])) == (6, 4)
    c = cost_row(env)
    assert (c["qty"], c["value"], c["avg_cost"]) == (10, Decimal("1000.00"), Decimal("100"))
    # بيع من الفرع في نفس يوم التحويل (التحويلات قبل الصادر)
    sell(env, 10, 4, D2, wh=env["branch"])
    with pytest.raises(BusinessError, match="مخزن الفرع"):
        sell(env, 11, 1, D2, wh=env["branch"])
    summary = env["ledger"].warehouse_summary()
    assert summary[env["main"]]["value"] == Decimal("600.00") and env["branch"] not in summary
    check_books(env)


def test_transfer_invoice_to_statement(env):
    buy(env, 1, 10, "100", kind="invoice")
    buy(env, 2, 10, "40", kind="statement")
    post(env, 50, [S(env["item"], env["main"], -5, "invoice"),
                   S(env["item"], env["main"], 5, "statement", transfer_of=0)], D2, doc="transfer")
    assert cost_row(env, "invoice")["value"] == Decimal("500.00")
    st = cost_row(env, "statement")
    assert (st["qty"], st["value"], st["avg_cost"]) == (15, Decimal("900.00"), Decimal("60"))
    check_books(env)


def test_transfer_validation(env):
    buy(env, 1, 10, "100")
    bad = [
        [S(env["item"], env["main"], -4), S(env["item"], env["main"], 4, transfer_of=0)],          # نفس المخزن والنوع
        [S(env["item"], env["main"], -4), S(env["item"], env["branch"], 3, transfer_of=0)],        # كمية مختلفة
        [S(env["item"], env["main"], -4), S(env["item2"], env["branch"], 4, transfer_of=0)],       # صنف مختلف
        [S(env["item"], env["main"], -4), S(env["item"], env["branch"], 4, transfer_of=0, unit_cost=5)],
        [S(env["item"], env["branch"], 4, transfer_of=1), S(env["item"], env["main"], -4)],        # الداخل قبل الخارج
    ]
    for lines in bad:
        with pytest.raises(BusinessError):
            post(env, 50, lines, D2, doc="transfer")
    assert env["db"].fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'transfer'") == 0


# ---- الوهمي والتحقق -----------------------------------------------------------------------
def test_virtual_warehouse_quantities_only(env):
    out = post(env, 1, [S(env["item"], env["virtual"], 7)], doc="opening_stock")   # من غير تكلفة
    assert out[0]["unit_cost"] is None and out[0]["value"] == 0
    assert cost_row(env) is None and env["ledger"].total_value() == 0
    assert wh_qty(env, env["virtual"]) == 7 and env["ledger"].available(env["item"]) == 0
    with pytest.raises(BusinessError, match="بالسالب"):
        sell(env, 10, 8, D2, wh=env["virtual"])
    sell(env, 10, 7, D2, wh=env["virtual"])
    check_books(env)


def test_line_validation(env):
    service = item(env["db"], "تركيب", service=True)
    cases = [
        ([S(service, env["main"], 1, unit_cost=1)], "خدمي"),
        ([S(env["item"], env["main"], 1)], "تكلفة الوحدة مطلوبة"),
        ([S(env["item"], env["main"], 1, unit_cost=-1)], "بالسالب"),
        ([S(env["item"], env["main"], -1, unit_cost=-1)], "بالسالب"),     # خارج بتكلفة (مرتجع الشراء — 0049)
        ([S(env["item"], env["main"], 0, unit_cost=1)], "صفر"),
        ([S(env["item"], env["main"], "1.0000001", unit_cost=1)], "6 أرقام"),
        ([S(env["item"], env["main"], 1, "x", unit_cost=1)], "فاتورة"),
        ([S(env["item"], 999999, 1, unit_cost=1)], "المخزن مش موجود"),
        ([], "مافيهوش"),
    ]
    for lines, msg in cases:
        with pytest.raises(BusinessError, match=msg):
            post(env, 1, lines)
    with pytest.raises(BusinessError, match="مالوش حركة"):
        post(env, 1, [S(env["item"], env["main"], 1, unit_cost=1)], doc="receipt")


# ---- حماية القاعدة -------------------------------------------------------------------------
def test_database_guards(env):
    db = env["db"]
    buy(env, 1, 10, "100")
    mid = db.fetch_value("SELECT id FROM inv.stock_movements LIMIT 1")
    with pytest.raises(psycopg.errors.RaiseException, match="مابتتعدّلش"):
        db.execute("UPDATE inv.stock_movements SET qty = 5 WHERE id = %s", [mid])
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE inv.item_costs SET avg_cost = -1")
    with pytest.raises(psycopg.errors.RaiseException, match="مايتمسحش"):
        db.execute("DELETE FROM inv.items WHERE id = %s", [env["item"]])
    with pytest.raises(psycopg.errors.RaiseException, match="الوحدة الأساسية"):
        db.execute("UPDATE inv.items SET unit_id = (SELECT id FROM inv.units WHERE NOT is_default LIMIT 1) "
                   "WHERE id = %s", [env["item"]])
    with pytest.raises(psycopg.errors.RaiseException, match="خدمي"):
        db.execute("UPDATE inv.items SET is_service = true, default_warehouse_id = NULL, reorder_level = NULL "
                   "WHERE id = %s", [env["item"]])
    db.execute("UPDATE inv.items SET name = 'كابل نحاس 2' WHERE id = %s", [env["item"]])     # الباقي عادي
    db.execute("DELETE FROM inv.items WHERE id = %s", [env["item2"]])                         # من غير حركة ← يتمسح
    buy(env, 2, 1, "5", wh=env["branch"])
    with pytest.raises(psycopg.errors.RaiseException, match="عليه 1 حركة مخزون — مايتمسحش"):
        db.execute("DELETE FROM inv.warehouses WHERE id = %s", [env["branch"]])
    with pytest.raises(psycopg.errors.RaiseException, match="«وهمي» مايتغيّرش"):
        db.execute("UPDATE inv.warehouses SET is_virtual = true WHERE id = %s", [env["branch"]])
    with pytest.raises(psycopg.errors.RaiseException, match="حركة مخزون"):
        db.execute("DELETE FROM core.fiscal_years WHERE year_code = '2026'")
    with pytest.raises(psycopg.errors.RaiseException, match="برّا المدة"):
        db.execute("UPDATE core.fiscal_years SET start_date = '2026-06-01' WHERE year_code = '2026'")


def test_closed_year(env):
    db = env["db"]
    buy(env, 1, 10, "100", dt.date(2025, 12, 20))
    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2025'")
    with pytest.raises(BusinessError, match="مقفولة"):
        buy(env, 2, 1, "100", dt.date(2025, 12, 21))
    with pytest.raises(BusinessError, match="مقفولة"):
        remove(env, 1)
    # حركة في السنة المفتوحة عادي — والمتوسط من آخر السنة المقفولة
    assert sell(env, 10, 2, D1)[0]["unit_cost"] == Decimal("100")
    with pytest.raises(BusinessError, match="لسنة|مفيش سنة"):
        buy(env, 3, 1, "1", dt.date(2027, 1, 5))


def test_item_summary_and_reorder(env):
    db = env["db"]
    db.execute("UPDATE inv.items SET reorder_level = 5 WHERE id IN (%s, %s)", [env["item"], env["item2"]])
    assert env["ledger"].below_reorder_count() == 2
    buy(env, 1, 8, "10")
    buy(env, 2, 2, "4", wh=env["branch"], kind="statement")
    assert env["ledger"].below_reorder_count() == 1
    s = env["ledger"].item_summary(env["item"])
    assert s["qty"] == 10 and s["value"] == Decimal("88.00") and s["has_movements"]
    assert {(r["name_ar"], r["stock_kind"], r["qty"]) for r in s["warehouses"]} == \
        {(db.fetch_value("SELECT name_ar FROM inv.warehouses WHERE id = %s", [env["main"]]), "invoice", 8),
         ("مخزن الفرع", "statement", 2)}
    assert env["ledger"].total_value() == Decimal("88.00")


# ---- جهازين في نفس اللحظة ------------------------------------------------------------------
def test_two_devices_cannot_oversell(env):
    buy(env, 1, 10, "100")
    other = Database(env["db"].settings)
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def run(db, doc_id):
        try:
            barrier.wait()
            post(env, doc_id, [S(env["item"], env["main"], -6)], D2, doc="sales_invoice", db=db)
        except BusinessError as exc:
            errors.append(exc)

    try:
        threads = [threading.Thread(target=run, args=(env["db"], 10)),
                   threading.Thread(target=run, args=(other, 11))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        other.close()
    assert len(errors) == 1 and "بالسالب" in str(errors[0])
    assert wh_qty(env, env["main"]) == 4
    check_books(env)


# ---- شاشتي الأصناف والمخازن ------------------------------------------------------------------
def test_item_and_warehouse_screens_data(env):
    from app.services.item_service import ItemService
    from app.services.warehouse_service import WarehouseService

    items, warehouses = ItemService(env["db"]), WarehouseService(env["db"])
    assert items.stock_text(env["item"]) == ("0 — مفيش حركة لسه", "—")
    buy(env, 1, 4, "25")
    post(env, 2, [S(env["item"], env["virtual"], 3)], doc="opening_stock")
    balance, avg = items.stock_text(env["item"])
    assert balance == "4 (القيمة 100.00) + 3 في الوهمي" and avg == "فاتورة: 25.00"
    assert warehouses.stats()["value"] == Decimal("100.00")
    assert warehouses.stock_of(env["main"]) == {"items": 1, "value": Decimal("100.00")}
    assert warehouses.stock_of(env["virtual"]) == {"items": 1, "value": Decimal("0.00")}
    assert warehouses.stock_of(env["branch"]) is None
    version = env["db"].fetch_value("SELECT row_version FROM inv.items WHERE id = %s", [env["item"]])
    with pytest.raises(psycopg.errors.RaiseException, match="وقّفه بدل ما تمسحه"):
        items.delete_item(env["item"], expected_version=version)


# ---- البيع من غير رصيد بالصلاحية (رد المستخدم 2026-11-23) -----------------------------------------
def sale_row(env, doc_id):
    return env["db"].fetch_one("SELECT unit_cost, value, booked_value FROM inv.stock_movements "
                               "WHERE source_type = 'sales_invoice' AND source_id = %s", [doc_id])


def test_negative_needs_permission_and_cost_is_corrected(env):
    buy(env, 1, 10, "100", D1)
    sell(env, 10, 10, D2)                                    # الرصيد 0 والمتوسط 100
    with pytest.raises(NegativeStockError) as err:
        sell(env, 11, 5, D3)
    assert err.value.shortages[0]["short"] == 5 and "صلاحية" in str(err.value)
    out = sell(env, 11, 5, D3, allow=True)[0]                # بآخر متوسط مؤقتاً
    assert (out["unit_cost"], out["value"]) == (Decimal("100"), Decimal("-500.00"))
    assert wh_qty(env, env["main"]) == -5 and cost_row(env)["qty"] == -5
    buy(env, 2, 10, "120", D4)                               # الشراء غطّى الناقص ← التكلفة اتصلّحت بـ 120
    s = sale_row(env, 11)
    assert (s["unit_cost"], s["value"], s["booked_value"]) == (Decimal("120"), Decimal("-600.00"), Decimal("-500.00"))
    c = cost_row(env)
    assert (c["qty"], c["value"], c["avg_cost"]) == (5, Decimal("600.00"), Decimal("120"))
    assert [(r["source_id"], r["difference"]) for r in env["ledger"].cost_differences()] == [(11, Decimal("-100.00"))]
    check_books(env)


def test_negative_partial_cover_and_item_without_cost(env):
    sell(env, 10, 5, D1, allow=True)                         # صنف عمره مااتشترى ← تكلفة صفر مؤقتاً
    assert sale_row(env, 10)["value"] == 0
    buy(env, 1, 3, "120", D2)                                # غطّى 3 بس
    assert sale_row(env, 10)["value"] == Decimal("-360.00")
    c = cost_row(env)
    assert (c["qty"], c["value"], c["avg_cost"]) == (-2, Decimal("0.00"), Decimal("120"))
    buy(env, 2, 4, "130", D3)                                # غطّى الـ 2 الباقيين بـ 130
    s = sale_row(env, 10)
    assert s["value"] == Decimal("-620.00") and s["unit_cost"] == Decimal("124")
    c = cost_row(env)
    assert (c["qty"], c["value"], c["avg_cost"]) == (2, Decimal("260.00"), Decimal("130"))
    check_books(env)


def test_existing_negative_does_not_block_others(env):
    buy(env, 1, 10, "100", D1)
    sell(env, 10, 15, D3, allow=True)                        # يوم 3 = −5 بصلاحية
    buy(env, 2, 2, "100", D2)                                # من غير صلاحية: يوم 3 بقى −3 (أحسن) ← عادي
    with pytest.raises(NegativeStockError):
        sell(env, 11, 1, D2)                                 # يوم 3 هيبقى −4 (أوحش) ← محتاج صلاحية
    buy(env, 3, 3, "100", D5)                                # الرصيد رجع صفر
    with pytest.raises(NegativeStockError):
        remove(env, 3)                                       # مسح الشراء يرجّعه سالب ← محتاج صلاحية
    assert remove(env, 3, allow=True)
    assert wh_qty(env, env["main"]) == -3
    check_books(env)


def test_backdated_purchase_before_negative_sale(env):
    sell(env, 10, 4, D3, allow=True)
    buy(env, 1, 10, "50", D2)                                # قبل البيع ← البيع مابقاش ناقص
    s = sale_row(env, 10)
    assert (s["unit_cost"], s["value"]) == (Decimal("50"), Decimal("-200.00"))
    assert cost_row(env)["qty"] == 6
    check_books(env)


def test_transfer_between_virtual_and_real(env):
    buy(env, 1, 10, "100")
    out = post(env, 50, [S(env["item"], env["main"], -4), S(env["item"], env["virtual"], 4, transfer_of=0)], D2,
               doc="transfer")
    assert [(r["unit_cost"], r["value"]) for r in out] == [(Decimal("100"), Decimal("-400.00")), (None, 0)]
    assert cost_row(env)["value"] == Decimal("600.00") and wh_qty(env, env["virtual"]) == 4
    back = post(env, 51, [S(env["item"], env["virtual"], -2), S(env["item"], env["main"], 2, transfer_of=0)], D3,
                doc="transfer")
    assert [(r["unit_cost"], r["value"]) for r in back] == [(None, 0), (Decimal("100"), Decimal("200.00"))]
    c = cost_row(env)
    assert (c["qty"], c["value"]) == (8, Decimal("800.00"))
    assert (wh_qty(env, env["main"]), wh_qty(env, env["virtual"])) == (8, 2)
    check_books(env)


def test_cross_kind_transfer_never_goes_negative(env):
    buy(env, 1, 3, "100", kind="invoice")
    with pytest.raises(BusinessError, match="حتى بصلاحية"):
        post(env, 50, [S(env["item"], env["main"], -5, "invoice"),
                       S(env["item"], env["main"], 5, "statement", transfer_of=0)], D2, doc="transfer", allow=True)
