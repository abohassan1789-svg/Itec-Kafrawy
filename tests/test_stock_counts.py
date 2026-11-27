"""اختبارات «الجرد والتسويات» (0047 + stock_count_service + الشاشة — النموذج 1):
الترقيم والصلاحيات · عجز / زيادة / مطابق بحركات «تسوية جرد» وقيد على 5205 · اللي مااتعدّش بيفضل برصيده و«= صفر» ·
زيادة لصنف مالوش متوسط · التسوية المباشرة بسبب · المخزن الوهمي من غير قيد · جرد بتاريخ قديم وصلاحية «من غير رصيد» ·
إلغاء الترحيل والرقم الثابت والمسح · **كل سطر بإصداره** (جهازين في نفس الجرد) · حماية القاعدة · قفل السنة ·
جهازين في نفس اللحظة · المعاينة والتنبيهات · الجرد الأعمى · الإكسل · الشاشة."""

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
from app.services.stock_count_service import StockCountService, line_state
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService, StockLine
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
    env = {"db": db, "shown": shown, "service": StockCountService(db), "ledger": StockLedgerService(db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "virtual": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_virtual ORDER BY seq LIMIT 1"),
           "item": item(db, "كابل نحاس"), "item2": item(db, "مفتاح"), "item3": item(db, "حساس حرارة")}
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
                                                             unit_cost=None if wh == env["virtual"] else cost)])


def sell(env, doc_id, qty, kind="invoice", it=None, d=D3, service=None):
    ledger = service or env["ledger"]
    with ledger.db.atomic() as tx:
        ledger.post(tx, doc_type="sales_invoice", source_id=doc_id, source_no=f"SI-{doc_id}", movement_date=d,
                    lines=[StockLine(it or env["item"], env["main"], -Decimal(qty), kind)])


def line(item_id, counted=None, kind="invoice", **extra):
    out = {"item_id": item_id, "stock_kind": kind}
    if counted is not None:
        out["counted_qty"] = counted
    return {**out, **extra}


def data(env, lines, wh=None, d=D2, ctype="periodic", zero=False, note=None):
    return {"count_date": d, "warehouse_id": wh or env["main"], "count_type": ctype, "zero_uncounted": zero,
            "description": note, "lines": lines}


def qty_at(env, wh=None, kind="invoice", it=None):
    return env["db"].fetch_value("SELECT coalesce(sum(qty), 0) FROM inv.stock_balances WHERE item_id = %s "
                                 "AND warehouse_id = %s AND stock_kind = %s",
                                 [it or env["item"], wh or env["main"], kind])


def entry(env, cid):
    return env["db"].fetch_one("SELECT status, total_debit FROM acc.journal_entries "
                               "WHERE source_type = 'stock_adjustment' AND source_id = %s", [cid])


def entry_lines(env, cid):
    return env["db"].fetch_all(
        "SELECT a.code, l.debit, l.credit FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
        "WHERE e.source_type = 'stock_adjustment' AND e.source_id = %s ORDER BY l.line_no", [cid])


def saved(env, cid):
    return {(l["item_id"], l["stock_kind"]): l for l in env["service"].repository.lines(cid)}


def deny(monkeypatch, *actions):
    real = SESSION.can
    codes = {f"inv.stock_counts.{a}" for a in actions}
    monkeypatch.setattr(SESSION, "can", lambda code: code not in codes and real(code))


# ---- السكريبت ------------------------------------------------------------------
def test_migration_numbering_and_permissions(env):
    db = env["db"]
    assert db.fetch_value("SELECT prefix FROM core.document_types WHERE code = 'stock_count'") == "SC"
    target = next(t for s in SECTIONS if s.id == "inv" for t in s.targets() if t.code == "stock_counts")
    assert {"post", "unpost", "negative", "show_book", "import", "delete"} <= set(target.actions)
    for action in ("negative", "show_book"):
        assert db.fetch_value("SELECT count(*) FROM sec.permissions WHERE permission_code = %s",
                              [f"inv.stock_counts.{action}"]) == 1
    assert db.fetch_value("SELECT count(*) FROM acc.posting_rules WHERE doc_type = 'stock_adjustment'") == 2


# ---- الحسبة --------------------------------------------------------------------
def test_line_state_math():
    z = Decimal("0")
    s = line_state(direct=False, book=Decimal(10), counted=Decimal(7), avg_cost=Decimal(5))
    assert (s["state"], s["diff"], s["value"], s["warning"]) == ("short", Decimal(-3), Decimal("-15.00"),
                                                                 "فرق أكبر من 20% من الرصيد — راجع العدّ")
    s = line_state(direct=False, book=Decimal(100), counted=Decimal(102), avg_cost=Decimal(5), seen=Decimal(100))
    assert (s["state"], s["value"], s["warning"]) == ("over", Decimal("10.00"), "")
    assert "اتغيّر" in line_state(direct=False, book=Decimal(95), counted=Decimal(95), seen=Decimal(100))["warning"]
    s = line_state(direct=False, book=z, counted=Decimal(3))
    assert s["need_cost"] and "اكتب تكلفة" in s["error"]
    s = line_state(direct=False, book=z, counted=Decimal(3), typed_cost=Decimal(7))
    assert s["error"] == "" and s["value"] == Decimal("21.00")
    assert line_state(direct=False, book=Decimal(5), counted=None)["state"] == "none"
    s = line_state(direct=False, book=Decimal(5), counted=None, avg_cost=Decimal(2), zero_uncounted=True)
    assert s["auto"] and s["diff"] == Decimal(-5) and s["value"] == Decimal("-10.00")
    s = line_state(direct=True, book=Decimal(5), adjust=Decimal(-2), avg_cost=Decimal(4))
    assert (s["state"], s["after"], s["value"]) == ("short", Decimal(3), Decimal("-8.00"))
    s = line_state(direct=False, book=Decimal(5), counted=Decimal(9), is_virtual=True)
    assert s["state"] == "over" and s["value"] == z and s["error"] == ""


# ---- الجرد: عجز / زيادة / مطابق ------------------------------------------------------
def test_count_post_shortage_surplus_match(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 100, "10")
    buy(env, 2, 50, "20", it=env["item2"])
    buy(env, 3, 8, "5", it=env["item3"])
    cid = s.save_draft(None, data(env, [line(env["item"], "97"), line(env["item2"], "52"), line(env["item3"], "8")],
                                  note="جرد آخر الشهر"))
    rec = s.get_count(cid)
    assert rec["status"] == "draft" and rec["count_no"] is None and len(saved(env, cid)) == 3
    assert saved(env, cid)[(env["item"], "invoice")]["seen_book_qty"] == 100
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'stock_adjustment'") == 0
    no = s.post(cid, expected_version=rec["row_version"])
    assert no.startswith("SC-2026-")
    assert (qty_at(env), qty_at(env, it=env["item2"]), qty_at(env, it=env["item3"])) == (97, 52, 8)
    lines = saved(env, cid)
    assert (lines[(env["item"], "invoice")]["book_qty"], lines[(env["item"], "invoice")]["diff_qty"]) == (100, -3)
    assert lines[(env["item3"], "invoice")]["diff_qty"] == 0
    moves = db.fetch_all("SELECT item_id, qty, value FROM inv.stock_movements WHERE source_type = 'stock_adjustment' "
                         "AND source_id = %s ORDER BY line_no", [cid])
    assert [(m["item_id"], m["qty"], m["value"]) for m in moves] == [
        (env["item"], -3, Decimal("-30.00")), (env["item2"], 2, Decimal("40.00"))]      # المطابق مالوش حركة
    assert entry(env, cid) == {"status": "posted", "total_debit": Decimal("70.00")}
    by = {(r["code"], "dr" if r["debit"] else "cr"): r["debit"] or r["credit"] for r in entry_lines(env, cid)}
    assert by == {("1105", "dr"): Decimal("40.00"), ("5205", "cr"): Decimal("40.00"),
                  ("5205", "dr"): Decimal("30.00"), ("1105", "cr"): Decimal("30.00")}
    assert env["ledger"].average_cost(env["item2"]) == Decimal("20")                   # الزيادة بالمتوسط
    sheet = {r["item_id"]: r for r in s.sheet(env["main"], D2, cid)}
    assert sheet[env["item"]]["posted"] and sheet[env["item"]]["value"] == Decimal("-30.00")
    review = s.review({}, cid)
    assert review["totals"]["short_value"] == Decimal("30.00") and review["totals"]["match"] == 1
    assert len(review["entry"]) == 4


def test_uncounted_stays_and_zero_uncounted(env):
    s = env["service"]
    buy(env, 1, 100, "10")
    buy(env, 2, 50, "20", it=env["item2"])
    cid = s.save_draft(None, data(env, [line(env["item"], "99")]), post=True)
    assert (qty_at(env), qty_at(env, it=env["item2"])) == (99, 50)                     # اللي مااتعدّش زي ما هو
    assert len(saved(env, cid)) == 1
    # الجرد الشامل: اللي مااتعدّش = صفر
    review = s.review(data(env, [line(env["item"], "99")], d=D3, zero=True))
    assert any(c[0] == "warn" and "هيتحسبوا صفر" in c[1] for c in review["checks"])
    assert review["totals"]["short_value"] == Decimal("1000.00")
    cid2 = s.save_draft(None, data(env, [line(env["item"], "99")], d=D3, zero=True), post=True)
    lines = saved(env, cid2)
    assert lines[(env["item2"], "invoice")]["auto_zero"] and lines[(env["item2"], "invoice")]["diff_qty"] == -50
    assert qty_at(env, it=env["item2"]) == 0 and entry(env, cid2)["total_debit"] == Decimal("1000.00")
    s.unpost(cid2)
    assert qty_at(env, it=env["item2"]) == 50 and list(saved(env, cid2)) == [(env["item"], "invoice")]
    assert saved(env, cid2)[(env["item"], "invoice")]["book_qty"] is None


def test_surplus_without_average_needs_cost(env):
    s = env["service"]
    review = s.review(data(env, [line(env["item3"], "3")]))
    assert not review["ready"] and any("اكتب تكلفة الوحدة" in c[1] for c in review["checks"] if c[0] == "no")
    cid = s.save_draft(None, data(env, [line(env["item3"], "3")]))
    with pytest.raises(BusinessError, match="اكتب تكلفة الوحدة"):
        s.post(cid)
    assert s.get_count(cid)["status"] == "draft" and qty_at(env, it=env["item3"]) == 0
    v = saved(env, cid)[(env["item3"], "invoice")]["row_version"]
    s.save_draft(cid, data(env, [line(env["item3"], "3", unit_cost="7.5", row_version=v)]), post=True)
    assert qty_at(env, it=env["item3"]) == 3 and entry(env, cid)["total_debit"] == Decimal("22.50")
    assert env["ledger"].average_cost(env["item3"]) == Decimal("7.5")


def test_statement_kind_counted_separately(env):
    s = env["service"]
    buy(env, 1, 10, "10")
    buy(env, 2, 6, "8", kind="statement")
    rows = {(r["item_id"], r["stock_kind"]): r for r in s.sheet(env["main"], D2)}
    assert rows[(env["item"], "invoice")]["book_qty"] == 10 and rows[(env["item"], "statement")]["book_qty"] == 6
    s.save_draft(None, data(env, [line(env["item"], "10"), line(env["item"], "5", kind="statement")]), post=True)
    assert (qty_at(env), qty_at(env, kind="statement")) == (10, 5)


# ---- التسوية المباشرة ---------------------------------------------------------------
def test_direct_adjustment_with_reason(env, monkeypatch):
    s, db = env["service"], env["db"]
    buy(env, 1, 10, "10")
    with pytest.raises(BusinessError, match="سبب التسوية"):
        s.save_draft(None, data(env, [line(env["item"], adjust_qty="-4")], ctype="direct"))
    cid = s.save_draft(None, data(env, [line(env["item"], adjust_qty="-4", reason="damaged")], ctype="direct",
                                  note="كرتونة وقعت"), post=True)
    rec = saved(env, cid)[(env["item"], "invoice")]
    assert (rec["adjust_qty"], rec["reason"], rec["book_qty"], rec["diff_qty"]) == (-4, "damaged", 10, -4)
    assert qty_at(env) == 6 and entry(env, cid)["total_debit"] == Decimal("40.00")
    assert s.sheet(env["main"], D2, cid, "direct")[0]["adjust_qty"] == -4
    # عجز أكبر من الرصيد ← «من غير رصيد»
    deny(monkeypatch, "negative")
    review = s.review(data(env, [line(env["item"], adjust_qty="-9", reason="broken")], ctype="direct"))
    assert not review["ready"] and any("من غير رصيد" in c[1] for c in review["checks"] if c[0] == "no")
    cid2 = s.save_draft(None, data(env, [line(env["item"], adjust_qty="-9", reason="broken")], ctype="direct"))
    with pytest.raises(NegativeStockError):
        s.post(cid2)
    monkeypatch.undo()
    s.post(cid2)
    assert qty_at(env) == -3
    # القاعدة: شكل السطر على نوع المستند
    cid3 = s.save_draft(None, data(env, [line(env["item2"], adjust_qty="2", reason="samples", unit_cost="3")],
                                   ctype="direct"))
    with pytest.raises(psycopg.errors.RaiseException, match="التسوية المباشرة"):
        db.execute("INSERT INTO inv.stock_count_lines (count_id, item_id, counted_qty) VALUES (%s, %s, 1)",
                   [cid3, env["item3"]])
    with pytest.raises(psycopg.errors.RaiseException, match="امسحها الأول"):
        db.execute("UPDATE inv.stock_counts SET count_type = 'periodic' WHERE id = %s", [cid3])
    # الخدمة: تغيير النوع بيمسح السطور القديمة
    rec3 = s.get_count(cid3)
    s.save_draft(cid3, data(env, [line(env["item2"], "1", unit_cost="3")]), expected_version=rec3["row_version"])
    assert s.get_count(cid3)["count_type"] == "periodic"
    assert saved(env, cid3)[(env["item2"], "invoice")]["counted_qty"] == 1


# ---- المخزن الوهمي --------------------------------------------------------------------
def test_virtual_warehouse_quantities_only(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 40, None, wh=env["virtual"])
    review = s.review(data(env, [line(env["item"], "38")], wh=env["virtual"]))
    assert review["ready"] and review["entry"] is None
    assert any("من غير قيد" in c[1] for c in review["checks"])
    cid = s.save_draft(None, data(env, [line(env["item"], "38")], wh=env["virtual"]), post=True)
    assert qty_at(env, wh=env["virtual"]) == 38 and entry(env, cid) is None
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'stock_adjustment' "
                          "AND source_id = %s AND is_virtual", [cid]) == 1


# ---- جرد بتاريخ قديم + «من غير رصيد» --------------------------------------------------
def test_backdated_shortage_needs_negative_permission(env, monkeypatch):
    s = env["service"]
    buy(env, 1, 10, "10")
    sell(env, 9, 8, d=D3)                                           # اتصرف 8 بعد يوم الجرد
    deny(monkeypatch, "negative")
    review = s.review(data(env, [line(env["item"], "5")]))          # عجز 5 يوم D2 ← D3 يبقى −3
    assert not review["ready"] and any("بالسالب" in c[1] for c in review["checks"] if c[0] == "no")
    cid = s.save_draft(None, data(env, [line(env["item"], "5")]))
    with pytest.raises(NegativeStockError):
        s.post(cid)
    assert s.get_count(cid)["status"] == "draft" and saved(env, cid)[(env["item"], "invoice")]["book_qty"] is None
    monkeypatch.undo()
    review = s.review(data(env, [line(env["item"], "5")]), cid)
    assert review["ready"] and any(c[0] == "warn" and "بالسالب" in c[1] for c in review["checks"])
    s.post(cid)
    assert qty_at(env) == -3


def test_unpost_surplus_already_sold_needs_permission(env, monkeypatch):
    s = env["service"]
    cid = s.save_draft(None, data(env, [line(env["item3"], "3", unit_cost="7")]), post=True)
    sell(env, 9, 2, it=env["item3"], d=D3)
    deny(monkeypatch, "negative")
    with pytest.raises(NegativeStockError):
        s.unpost(cid)
    assert s.get_count(cid)["status"] == "posted"
    monkeypatch.undo()
    s.unpost(cid)
    assert qty_at(env, it=env["item3"]) == -2 and entry(env, cid) is None


# ---- إلغاء الترحيل والرقم والمسح ---------------------------------------------------------
def test_unpost_keeps_number_and_delete_rules(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 10, "10")
    cid = s.save_draft(None, data(env, [line(env["item"], "9")]), post=True)
    no = s.get_count(cid)["count_no"]
    s.unpost(cid, expected_version=s.get_count(cid)["row_version"])
    rec = s.get_count(cid)
    assert rec["status"] == "draft" and rec["count_no"] == no and qty_at(env) == 10 and entry(env, cid) is None
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'stock_adjustment'") == 0
    with pytest.raises(BusinessError, match="خد رقم رسمي"):
        s.delete_count(cid)
    assert s.post(cid) == no                                         # نفس الرقم
    with pytest.raises(BusinessError, match="مرحّل"):
        s.delete_count(cid)
    with pytest.raises(BusinessError, match="مرحّل"):
        s.save_draft(cid, data(env, [line(env["item"], "8")]))
    draft = s.save_draft(None, data(env, []))                        # مسودة فاضية (قبل العدّ) مسموح
    with pytest.raises(BusinessError, match="مفيش ولا صنف"):
        s.post(draft)
    s.delete_count(draft, expected_version=s.get_count(draft)["row_version"])
    assert s.get_count(draft) is None
    assert s.count_counts(D1, D3) == {"total": 1, "drafts": 0, "posted": 1}
    assert len(s.list_counts(D1, D3, keyword="كابل")) == 1 and len(s.list_counts(D1, D3, keyword=no)) == 1
    assert s.list_counts(D1, D3)[0]["net_value"] == Decimal("-10.00")


# ---- كل سطر بإصداره: جهازين في نفس الجرد ---------------------------------------------------
def test_line_versions_two_devices_same_count(env):
    s = env["service"]
    buy(env, 1, 100, "10")
    buy(env, 2, 50, "20", it=env["item2"])
    cid = s.save_draft(None, data(env, [line(env["item"], "100")]))
    head = s.get_count(cid)["row_version"]
    v1 = saved(env, cid)[(env["item"], "invoice")]["row_version"]
    # جهاز 1 بيعدّ الصنف التاني وجهاز 2 بيعدّل الأول — الاتنين بنفس إصدار الرأس القديم، والاتنين بيتحفظوا
    s.save_draft(cid, data(env, [line(env["item2"], "49")]), expected_version=head)
    s.save_draft(cid, data(env, [line(env["item"], "98", row_version=v1)]), expected_version=head)
    assert s.get_count(cid)["row_version"] == head                   # الرأس مااتغيّرش
    assert {k[0]: l["counted_qty"] for k, l in saved(env, cid).items()} == {env["item"]: 98, env["item2"]: 49}
    # نفس السطر بالإصدار القديم ← تعارض
    with pytest.raises(ConcurrentEditError, match="جهاز تاني"):
        s.save_draft(cid, data(env, [line(env["item"], "97", row_version=v1)]), expected_version=head)
    # الجهاز التاني شايف السطر فاضي وكتب عدّ تاني ← تعارض · ونفس القيمة بالظبط ← مفيش تعارض
    with pytest.raises(ConcurrentEditError):
        s.save_draft(cid, data(env, [line(env["item2"], "48")]), expected_version=head)
    s.save_draft(cid, data(env, [line(env["item2"], "49")]), expected_version=head)
    # مسح سطر (الخانة فاضية) بإصداره
    v2 = saved(env, cid)[(env["item2"], "invoice")]["row_version"]
    s.save_draft(cid, data(env, [line(env["item2"], "", row_version=v2)]), expected_version=head)
    assert list(saved(env, cid)) == [(env["item"], "invoice")]
    with pytest.raises(ConcurrentEditError, match="اتمسح"):
        s.save_draft(cid, data(env, [line(env["item2"], "47", row_version=v2)]), expected_version=head)
    # الرأس: جهاز غيّر التاريخ، والتاني بيغيّر الملاحظات بالإصدار القديم ← تعارض
    s.save_draft(cid, data(env, [], d=D3), expected_version=head)
    assert s.get_count(cid)["row_version"] == head + 1
    with pytest.raises(ConcurrentEditError):
        s.save_draft(cid, data(env, [], note="ملاحظة"), expected_version=head)
    with pytest.raises(ConcurrentEditError):
        s.post(cid, expected_version=head)
    s.post(cid, expected_version=head + 1)
    assert qty_at(env) == 98


def test_two_devices_save_different_lines_at_once(env, base_settings):
    from dataclasses import replace
    import threading

    s, db = env["service"], env["db"]
    buy(env, 1, 100, "10")
    buy(env, 2, 50, "20", it=env["item2"])
    cid = s.save_draft(None, data(env, []))
    other = Database(replace(base_settings, db_name=db.settings.db_name))
    errors: list[BaseException] = []

    def run(service, lines):
        try:
            service.save_draft(cid, data(env, lines))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    try:
        threads = [threading.Thread(target=run, args=(s, [line(env["item"], "99")])),
                   threading.Thread(target=run, args=(StockCountService(other), [line(env["item2"], "51")]))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        other.close()
    assert not errors
    assert {k[0]: l["counted_qty"] for k, l in saved(env, cid).items()} == {env["item"]: 99, env["item2"]: 51}


def test_two_devices_post_count_and_sale_at_once(env, base_settings):
    """جهاز بيرحّل الجرد وجهاز بيصرف من نفس الصنف في نفس اللحظة ونفس اليوم: مفيش تعليق، والدفتري اللي اتسجّل على
    السطر + الفرق = الفعلي، ورصيد المخزن = مجموع الحركات (الجرد قبل الصرف ← 3 · بعده ← 7)."""
    from dataclasses import replace
    import threading

    s, db = env["service"], env["db"]
    buy(env, 1, 10, "10")
    cid = s.save_draft(None, data(env, [line(env["item"], "7")]))
    other = Database(replace(base_settings, db_name=db.settings.db_name))
    errors: list[BaseException] = []

    def run(fn):
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    try:
        threads = [threading.Thread(target=run, args=(lambda: s.post(cid),)),
                   threading.Thread(target=run, args=(lambda: sell(env, 9, 4, d=D2,
                                                                   service=StockLedgerService(other)),))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        other.close()
    assert not errors and all(not t.is_alive() for t in threads)
    rec = saved(env, cid)[(env["item"], "invoice")]
    assert rec["book_qty"] + rec["diff_qty"] == 7 and rec["book_qty"] in (10, 6)
    total = db.fetch_value("SELECT sum(qty) FROM inv.stock_movements WHERE item_id = %s", [env["item"]])
    assert qty_at(env) == total == (3 if rec["book_qty"] == 10 else 7)


# ---- حماية القاعدة ----------------------------------------------------------------
def test_database_guards(env):
    s, db = env["service"], env["db"]
    buy(env, 1, 10, "10")
    cid = s.save_draft(None, data(env, [line(env["item"], "9")]))
    with pytest.raises(psycopg.errors.RaiseException, match="من غير الرصيد الدفتري|من غير حركات"):
        db.execute("UPDATE inv.stock_counts SET status = 'posted' WHERE id = %s", [cid])   # ترحيل من غير حركات
    s.post(cid)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        db.execute("UPDATE inv.stock_count_lines SET counted_qty = 5 WHERE count_id = %s", [cid])
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        db.execute("DELETE FROM inv.stock_counts WHERE id = %s", [cid])
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        db.execute("UPDATE inv.stock_counts SET description = 'x' WHERE id = %s", [cid])
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة"):                    # إلغاء من غير مسح الحركات
        db.execute("UPDATE inv.stock_counts SET status = 'draft' WHERE id = %s", [cid])
    svc = item(db, "خدمة تركيب", service=True)
    draft = s.save_draft(None, data(env, [], d=D3))
    with pytest.raises(BusinessError, match="خدمي"):
        s.save_draft(draft, data(env, [line(svc, "1")], d=D3))
    with pytest.raises(BusinessError, match="بالسالب"):
        s.save_draft(draft, data(env, [line(env["item"], "-1")], d=D3))
    with pytest.raises(BusinessError, match="مكرر"):
        s.save_draft(draft, data(env, [line(env["item"], "1"), line(env["item"], "2")], d=D3))
    with pytest.raises(BusinessError, match="سنة مالية"):
        s.save_draft(None, data(env, [], d=dt.date(2030, 1, 1)))
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة"):                    # السنة ماتتقفلش وفيها مسودة
        db.execute("UPDATE core.fiscal_years SET status = 'closed' WHERE year_code = '2026'")
    s.delete_count(draft)
    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2026'")
    with pytest.raises(BusinessError, match="مقفولة"):
        s.unpost(cid)


# ---- المعاينة والتنبيهات -------------------------------------------------------------
def test_review_warnings_book_changed_and_big_diff(env):
    s = env["service"]
    buy(env, 1, 100, "10")
    buy(env, 2, 50, "20", it=env["item2"])
    cid = s.save_draft(None, data(env, [line(env["item"], "100"), line(env["item2"], "30")]))
    review = s.review(data(env, [line(env["item"], "100"), line(env["item2"], "30")]), cid)
    assert review["ready"] and any(c[0] == "warn" and "20%" in c[1] for c in review["checks"])
    assert review["totals"] == {"rows": 2, "counted": 2, "uncounted": 0, "match": 1, "short_n": 1,
                                "short_value": Decimal("400.00"), "over_n": 0, "over_value": Decimal("0"),
                                "net": Decimal("-400.00")}
    sell(env, 9, 5, d=D2)                                            # جهاز تاني صرف بعد ما اتعدّ
    review = s.review(data(env, [line(env["item"], "100"), line(env["item2"], "30")]), cid)
    state = review["lines"][(env["item"], "invoice")]
    assert "الدفتري اتغيّر" in state["warning"] and state["diff"] == 5
    assert any(c[0] == "warn" and "اتغيّر بعد ما اتعدّ" in c[1] for c in review["checks"])
    # اتعدّ تاني (القيمة اتغيّرت) ← التنبيه القديم بيروح
    review = s.review(data(env, [line(env["item"], "95"), line(env["item2"], "30")]), cid)
    assert review["lines"][(env["item"], "invoice")]["warning"] == ""
    s.post(cid)
    assert saved(env, cid)[(env["item"], "invoice")]["diff_qty"] == 5   # الفرق على الدفتري وقت الترحيل


def test_blind_user_gets_no_book(env, monkeypatch):
    s = env["service"]
    buy(env, 1, 100, "10")
    cid = s.save_draft(None, data(env, [line(env["item"], "97")]))
    deny(monkeypatch, "show_book")
    row = s.sheet(env["main"], D2, cid)[0]
    assert row["book_qty"] is None and row["avg_cost"] is None and row["counted_qty"] == 97
    review = s.review(data(env, [line(env["item"], "97")]), cid)
    assert review["lines"] == {} and review["entry"] is None and review["totals"]["short_n"] == 0
    assert review["totals"]["counted"] == 1 and review["ready"]
    assert s.list_counts(D1, D3)[0]["net_value"] is None
    s.save_draft(cid, data(env, [line(env["item"], "96", row_version=row["row_version"])]))   # العدّ بيتحفظ عادي
    assert saved(env, cid)[(env["item"], "invoice")]["counted_qty"] == 96


# ---- الإكسل ---------------------------------------------------------------------
def test_excel_sheet_export_and_import(env, tmp_path, monkeypatch):
    from openpyxl import Workbook, load_workbook

    s = env["service"]
    buy(env, 1, 100, "10")
    buy(env, 2, 50, "20", it=env["item2"])
    path = tmp_path / "sheet.xlsx"
    assert s.export_sheet(path, s.sheet(env["main"], D2)) == 2
    ws = load_workbook(path).worksheets[0]
    assert [c.value for c in ws[1]] == ["كود الصنف", "اسم الصنف", "النوع", "الوحدة", "الدفتري", "الفعلي"]
    assert ws.cell(2, 5).value == 100 and ws.cell(2, 6).value is None
    deny(monkeypatch, "show_book")
    s.export_sheet(tmp_path / "blind.xlsx", s.sheet(env["main"], D2))
    assert "الدفتري" not in [c.value for c in load_workbook(tmp_path / "blind.xlsx").worksheets[0][1]]
    monkeypatch.undo()
    wb = load_workbook(path)
    ws = wb.worksheets[0]
    ws.cell(2, 6).value = 98.5
    ws.append(["itm-0003", "", "بيان", "", None, 4])                  # صنف مش في الورقة
    wb.save(path)
    good, errors = s.match_sheet(s.read_sheet(path))
    assert not errors
    assert [(g["item_id"], g["stock_kind"], g["counted_qty"]) for g in good] == [
        (env["item"], "invoice", Decimal("98.5")), (env["item3"], "statement", Decimal(4))]
    bad = Workbook()
    ws = bad.active
    ws.append(["كود الصنف", "النوع", "الفعلي"])
    for r in (["ITM-9999", "", 1], ["ITM-0001", "غلط", 1], ["ITM-0001", "", -2], ["ITM-0002", "", 1],
              ["ITM-0002", "فاتورة", 3], ["", "", 5]):
        ws.append(r)
    bad.save(tmp_path / "bad.xlsx")
    good, errors = s.match_sheet(s.read_sheet(tmp_path / "bad.xlsx"))
    assert len(good) == 1 and len(errors) == 5
    assert "مش موجود" in errors[0] and "متكرر" in errors[3]
    empty = Workbook()
    empty.active.append(["حاجة", "تانية"])
    empty.save(tmp_path / "empty.xlsx")
    with pytest.raises(BusinessError, match="ناقصه عمود"):
        s.read_sheet(tmp_path / "empty.xlsx")


# ---- الشاشة ------------------------------------------------------------------
def test_screen_count_post_unpost(env):
    from app.ui.screens.stock_counts_screen import C_BOOK, C_COST, C_DIFF, C_NOTE, C_VALUE, StockCountsScreen

    s = env["service"]
    buy(env, 1, 100, "10")
    buy(env, 2, 50, "20", it=env["item2"])
    screen = StockCountsScreen(service=s)
    screen._from, screen._to = D1, D3
    screen.new_record()
    assert screen.mode == "new" and screen.warehouse.currentData() == env["main"]
    screen.date_edit.setDate(screen.date_edit.date().fromString("02/03/2026", "dd/MM/yyyy"))
    assert [r["item_id"] for r in screen.rows] == [env["item"], env["item2"]]
    assert screen.grid.item(0, C_BOOK).text() == "100" and screen.grid.item(0, C_NOTE).text() == "مااتعدّش"
    screen.set_cell(env["item"], count="97")
    assert screen.grid.item(0, C_DIFF).text() == "▼ -3" and screen.grid.item(0, C_VALUE).text() == "-30.00"
    assert screen.grid.item(0, C_COST).text() == "10.00" and screen.grid.item(0, C_NOTE).text() == "عجز"
    assert "اتعدّ 1 من 2" in screen.total_label.text() and "عجز 1" in screen.total_label.text()
    # صنف مش في الدفتر: زيادة من غير متوسط ← لازم تكلفة
    assert screen.add_row(env["item3"], "invoice") and len(screen.rows) == 3
    screen.set_cell(env["item3"], count="3")
    assert "اكتب تكلفة الوحدة" in screen.grid.item(2, C_NOTE).text()
    screen._refresh_review()
    assert not screen._review["ready"] and "فيه مشاكل" in screen.review_box.title()
    screen.post_count()
    assert screen.mode == "new" and "اكتب تكلفة الوحدة" in env["shown"][-1]             # الترحيل وقف
    screen.set_cell(env["item3"], cost="7")
    assert screen.grid.item(2, C_VALUE).text() == "+21.00" and screen.grid.item(2, C_NOTE).text() == "زيادة"
    screen.fill_matching()
    assert screen.grid.item(1, C_NOTE).text() == "✔ مطابق"
    screen._refresh_review()
    assert screen._review["ready"] and screen.entry_table.rowCount() == 4
    assert screen.side_stack.currentIndex() == 1                                        # وقت التعديل: المراجعة
    screen.post_count()
    rec = screen.current_record
    assert screen.mode == "view" and rec["status"] == "posted" and rec["count_no"].startswith("SC-")
    assert (qty_at(env), qty_at(env, it=env["item3"])) == (97, 3)
    assert len(screen.rows) == 3 and screen.grid.item(0, C_DIFF).text() == "▼ -3"
    assert screen.unpost_button.isEnabled() and not screen.edit_button.isEnabled()
    assert screen.table.rows[0]["net_value"] == Decimal("-9.00")
    screen.unpost_count()
    assert screen.current_record["status"] == "draft" and qty_at(env) == 100
    # تعديل سطر واحد وحفظ: السطر ده بس اللي بيتبعت
    screen.edit_record()
    screen.set_cell(env["item"], count="99")
    payload = screen.collect()
    assert [l["item_id"] for l in payload["lines"]] == [env["item"]]
    screen.save()
    assert screen.mode == "view" and saved(env, screen.current_id)[(env["item"], "invoice")]["counted_qty"] == 99
    screen.close()
    screen.deleteLater()


def test_screen_direct_adjustment_and_blind(env, monkeypatch, tmp_path):
    from app.ui.screens.stock_counts_screen import C_BOOK, C_COUNT, C_DIFF, C_REASON, StockCountsScreen

    s = env["service"]
    buy(env, 1, 10, "10")
    screen = StockCountsScreen(service=s)
    screen._from, screen._to = D1, D3
    screen.new_record()
    screen.date_edit.setDate(screen.date_edit.date().fromString("02/03/2026", "dd/MM/yyyy"))
    screen.type_combo.setCurrentIndex(screen.type_combo.findData("direct"))
    assert screen._direct_grid and screen.rows == [] and not screen.grid.isColumnHidden(C_REASON)
    assert not screen.zero_check.isVisible() or not screen.zero_check.isVisibleTo(screen)
    assert screen.add_row(env["item"], "invoice")
    screen.set_cell(env["item"], adjust="-4", reason="broken")
    assert screen.grid.item(0, C_COUNT).text() == "6" and screen.grid.item(0, C_BOOK).text() == "10"
    screen.post_count()
    assert screen.current_record["status"] == "posted" and qty_at(env) == 6
    line_rec = saved(env, screen.current_id)[(env["item"], "invoice")]
    assert (line_rec["adjust_qty"], line_rec["reason"]) == (-4, "broken")
    assert screen.grid.item(0, C_DIFF).text() == "-4"
    # ورقة العدّ + استيراد الفعلي + إخفاء الدفتري
    screen.new_record()
    screen.date_edit.setDate(screen.date_edit.date().fromString("03/03/2026", "dd/MM/yyyy"))
    screen.blind_check.setChecked(True)
    assert screen.grid.isColumnHidden(C_BOOK) and screen.grid.isColumnHidden(C_DIFF)
    assert screen.total_label.text() == "اتعدّ 0 من 1" and screen.side_stack.currentIndex() == 0
    path = str(tmp_path / "count.xlsx")
    screen.export_excel(path)
    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert "الدفتري" not in [c.value for c in wb.worksheets[0][1]]
    wb.worksheets[0].cell(2, 5).value = 5
    wb.save(path)
    screen.import_excel(path)
    assert screen.grid.item(0, C_COUNT).text() == "5" and "اتعدّ 1 من 1" in screen.total_label.text()
    screen.blind_check.setChecked(False)
    assert not screen.grid.isColumnHidden(C_BOOK) and screen.grid.item(0, C_DIFF).text() == "▼ -1"
    screen.save()
    assert screen.mode == "view" and screen.current_record["status"] == "draft"
    screen.close()
    screen.deleteLater()
    # مستخدم من غير «إظهار الدفتري»: الأعمدة مستخبّية ومفيش «تسوية مباشرة»
    deny(monkeypatch, "show_book")
    blind = StockCountsScreen(service=s)
    assert blind.grid.isColumnHidden(C_BOOK) and blind.type_combo.findData("direct") < 0
    assert not blind.blind_check.isVisibleTo(blind)
    blind.close()
    blind.deleteLater()
