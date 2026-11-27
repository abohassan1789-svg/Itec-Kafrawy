"""«التحويل بين المخازن» — المرحلة 6، النموذج 8 «لوحة المراجعة قبل الترحيل» (اختيار المستخدم 2026-11-27).

* **خطوة واحدة**: من مخزن + نوع (فاتورة / بيان) ← إلى مخزن + نوع، والنوعين **في الرأس** للتحويل كله · نفس المخزن
  مسموح لو النوع اتغيّر · السطر = صنف + وحدة من وحداته + كمية (نفس الصنف مرة واحدة — بيتجمّع).
* **«حفظ مسودة»** · **«ترحيل»** (أو حفظ + ترحيل في نفس الـ transaction) ← القاعدة بتصرف رقم ``TR-2026-00001`` أول
  مرة بس، و**دفتر المخزون** بيسجّل لكل سطر خارج من «من» بالمتوسط وداخل «إلى» بنفس القيمة (``transfer_of``)، ولو
  المخزن الوهمي داخل فيه **محرك الترحيل** بيعمل القيد بقيمة الحركات: حقيقي ← وهمي = مدين «بضاعة المخزن الوهمي» /
  دائن المخزون · وهمي ← حقيقي = العكس. بين مخزنين حقيقيين (أو فاتورة ↔ بيان) **مفيش قيد**.
* الرصيد مش مكفي ← **ممنوع إلا بصلاحية «من غير رصيد»** للشاشة دي (``inv.stock_transfers.negative``) · فاتورة ↔ بيان
  مايعدّيش رصيد النوع في الشركة حتى بالصلاحية (المحرك).
* **«إلغاء الترحيل»** بالصلاحية في السنة المفتوحة: الحركات والقيد بيتمسحوا والتحويل يرجع مسودة **برقمه** — ولو اللي
  اتصرف بعده هيخلّي «إلى» بالسالب محتاج «من غير رصيد».
* ``review`` = لوحة المراجعة (النموذج 8): كل الشروط ✔ / ✖ / ⚠ / ℹ + «قبل / بعد» للمخزنين + القيد اللي هيتعمل.
* كل حاجة في ``sec.audit_log`` قبل/بعد (بالسطور) + ``row_version`` (جهازين على نفس التحويل).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.stock_transfer_repository import STATUSES, StockTransferRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService
from app.services.stock_ledger_service import KINDS, StockLedgerService, StockLine, fmt_qty

PAGE_SIZE = 50
ZERO = Decimal("0")
CENT = Decimal("0.01")
QTY_STEP = Decimal("0.000001")
MAX_QTY = Decimal("999999999999")
MAX_LINES = 2000
DOC_TYPE = "stock_transfer"          # القيد (acc.posting_doc_types)
STOCK_DOC = "transfer"               # دفتر المخزون (inv.stock_doc_types)
ENTITY, ENTITY_LABEL = "inv.stock_transfers", "تحويل بين المخازن"
PERM = "inv.stock_transfers"
# نوع التحويل للعرض: (الاسم، اللون)
ROUTE_TYPES: dict[str, tuple[str, str]] = {
    "wh": ("بين مخزنين", "#137A38"),
    "kind": ("فاتورة ↔ بيان", "#B45309"),
    "virtual": ("للوهمي / من الوهمي", "#6D28D9"),
}


def transfer_label(row: dict[str, Any]) -> str:
    """«TR-2026-00012» أو «مسودة 7»."""
    return row.get("transfer_no") or f"مسودة {row.get('draft_no')}"


def route_type(from_id: Any, to_id: Any, from_virtual: bool, to_virtual: bool) -> str:
    if from_virtual or to_virtual:
        return "virtual"
    return "kind" if from_id == to_id else "wh"


def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def parse_qty(value: Any, label: str = "الكمية") -> Decimal:
    text = str(value if value is not None else "").strip().replace(",", "").replace("٫", ".")
    if not text:
        return ZERO
    try:
        qty = Decimal(text)
    except InvalidOperation:
        raise BusinessError(f"«{label}»: «{value}» مش رقم.") from None
    if not qty.is_finite():
        raise BusinessError(f"«{label}»: «{value}» مش رقم.")
    if qty != qty.quantize(QTY_STEP):
        raise BusinessError(f"«{label}»: الكمية فيها كسور أكتر من 6 أرقام.")
    if abs(qty) > MAX_QTY:
        raise BusinessError(f"«{label}»: الكمية أكبر من المسموح.")
    return qty


class StockTransferService:
    def __init__(self, db: Database | None = None, repository: StockTransferRepository | None = None,
                 ledger: StockLedgerService | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or StockTransferRepository(self.db)
        self.ledger = ledger or StockLedgerService(self.db)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_transfers(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                       keyword: str = "", warehouse_id: int | None = None, page: int = 1,
                       page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        rows = self.repository.list_transfers(date_from, date_to, status, keyword.strip(), warehouse_id,
                                              limit=page_size, offset=offset)
        for row in rows:
            row["route_type"] = route_type(row["from_warehouse_id"], row["to_warehouse_id"], row["from_virtual"],
                                           row["to_virtual"])
        return rows

    def count_transfers(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                        keyword: str = "", warehouse_id: int | None = None) -> dict[str, int]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_transfers(date_from, date_to, status, keyword.strip(), warehouse_id)
        return {k: int(row[k]) for k in ("total", "drafts", "posted")}

    def get_transfer(self, transfer_id: int) -> dict[str, Any] | None:
        row = self.repository.get_transfer(int(transfer_id))
        if row is not None:
            row["label"] = transfer_label(row)
            row["lines"] = self.repository.lines(int(transfer_id))
            row["route_type"] = route_type(row["from_warehouse_id"], row["to_warehouse_id"], row["from_virtual"],
                                           row["to_virtual"])
        return row

    def choices(self) -> dict[str, Any]:
        """المخازن والأصناف (مش الخدمية) بوحداتها: ``units[item_id]`` = [(unit_id, الاسم، المعامل)] — الأساسية الأول."""
        items = self.repository.item_choices()
        units: dict[int, list[tuple[int, str, Decimal]]] = {
            int(i["id"]): [(int(i["unit_id"]), i["unit_name"], Decimal(1))] for i in items}
        for u in self.repository.item_units():
            units.setdefault(int(u["item_id"]), []).append((int(u["unit_id"]), u["unit_name"], Decimal(u["factor"])))
        return {"warehouses": self.repository.warehouse_choices(), "items": items, "units": units}

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    def _header(self, data: dict[str, Any]) -> dict[str, Any]:
        transfer_date = data.get("transfer_date")
        if isinstance(transfer_date, dt.datetime):
            transfer_date = transfer_date.date()
        if not isinstance(transfer_date, dt.date):
            raise BusinessError("تاريخ التحويل مطلوب.")
        from_wh, to_wh = self._id(data.get("from_warehouse_id")), self._id(data.get("to_warehouse_id"))
        if from_wh is None:
            raise BusinessError("اختار مخزن «من».")
        if to_wh is None:
            raise BusinessError("اختار مخزن «إلى».")
        from_kind, to_kind = data.get("from_kind") or "invoice", data.get("to_kind") or "invoice"
        if from_kind not in KINDS or to_kind not in KINDS:
            raise BusinessError("نوع المخزون لازم «فاتورة» أو «بيان».")
        if from_wh == to_wh and from_kind == to_kind:
            raise BusinessError("«من» و«إلى» نفس المخزن ونفس النوع — اختار مخزن تاني أو غيّر النوع (فاتورة ↔ بيان).")
        description = " ".join(str(data.get("description") or "").split()) or None
        if description and len(description) > 500:
            raise BusinessError("«الملاحظات» أطول من المسموح (500 حرف).")
        return {"transfer_date": transfer_date, "from_warehouse_id": from_wh, "to_warehouse_id": to_wh,
                "from_kind": from_kind, "to_kind": to_kind, "description": description}

    def _lines(self, raw: list[dict[str, Any]], *, strict: bool = True) -> list[dict[str, Any]]:
        """السطور: الصنف + الوحدة (من وحداته) + الكمية · السطر الفاضي بيتشال · **نفس الصنف بيتجمّع** (بنفس الوحدة،
        ولو الوحدات مختلفة بالوحدة الأساسية). ``strict=False`` (المعاينة) بيتجاهل السطر الناقص بدل الغلط."""
        rows = [r for r in (raw or []) if self._id(r.get("item_id")) or str(r.get("qty") or "").strip()]
        if len(rows) > MAX_LINES:
            raise BusinessError(f"التحويل الواحد لحد {MAX_LINES:,} صنف.")
        items = self.repository.items([self._id(r.get("item_id")) for r in rows if self._id(r.get("item_id"))])
        units: dict[int, dict[int, Decimal]] = {}
        for u in self.repository.item_units(list(items)):
            units.setdefault(int(u["item_id"]), {})[int(u["unit_id"])] = Decimal(u["factor"])
        merged: dict[int, dict[str, Any]] = {}
        for n, r in enumerate(rows, start=1):
            item_id = self._id(r.get("item_id"))
            try:
                if item_id is None or item_id not in items:
                    raise BusinessError(f"سطر {n}: اختار الصنف.")
                item = items[item_id]
                label = f"سطر {n} ({item['code']} {item['name']})"
                if item["is_service"]:
                    raise BusinessError(f"{label}: الصنف خدمي — مالوش رصيد يتحوّل.")
                unit_id = self._id(r.get("unit_id")) or int(item["unit_id"])
                factor = Decimal(1) if unit_id == int(item["unit_id"]) else units.get(item_id, {}).get(unit_id)
                if factor is None:
                    raise BusinessError(f"{label}: الوحدة دي مش من وحدات الصنف.")
                qty = parse_qty(r.get("qty"), f"{label} — الكمية")
                if qty <= 0:
                    raise BusinessError(f"{label}: اكتب كمية أكبر من صفر.")
                if (qty * factor).quantize(QTY_STEP, rounding=ROUND_HALF_UP) <= 0:
                    raise BusinessError(f"{label}: الكمية صغيرة أوي.")
            except BusinessError:
                if strict:
                    raise
                continue
            row = merged.get(item_id)
            if row is None:
                merged[item_id] = {"item_id": item_id, "unit_id": unit_id, "factor": factor, "qty": qty,
                                   "item": item}
            elif row["unit_id"] == unit_id:
                row["qty"] += qty
            else:                                                   # وحدات مختلفة ← بالوحدة الأساسية
                row["qty"] = row["qty"] * row["factor"] + qty * factor
                row["unit_id"], row["factor"] = int(item["unit_id"]), Decimal(1)
        out = list(merged.values())
        for row in out:
            row["base_qty"] = (row["qty"] * row["factor"]).quantize(QTY_STEP, rounding=ROUND_HALF_UP)
            if parse_qty(row["qty"]) > MAX_QTY or row["base_qty"] > MAX_QTY:
                raise BusinessError(f"الصنف {row['item']['code']}: الكمية أكبر من المسموح.")
        if strict and not out:
            raise BusinessError("ضيف صنف واحد على الأقل للتحويل.")
        return out

    def _clean(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return self._header(data), self._lines(data.get("lines") or [])

    # ---- المعاينة: لوحة المراجعة (النموذج 8) ---------------------------------------------
    def _value_lines(self, header: dict[str, Any], lines: list[dict[str, Any]], whs: dict[int, dict],
                     values: dict[int, Decimal]) -> list[PostingLine]:
        """سطور القيد (لو الوهمي داخل): حقيقي ← وهمي بمبلغ موجب · وهمي ← حقيقي بالسالب (الجنب بيتقلب)."""
        from_v = bool(whs[header["from_warehouse_id"]]["is_virtual"])
        to_v = bool(whs[header["to_warehouse_id"]]["is_virtual"])
        if from_v == to_v:
            return []
        sign = Decimal(1) if to_v else Decimal(-1)
        out: list[PostingLine] = []
        for line in lines:
            value = values.get(line["item_id"], ZERO)
            if value:
                out.append(PostingLine("virtual", sign * value))
                out.append(PostingLine("inventory", sign * value, category_id=line["item"].get("category_id")))
        return out

    def review(self, data: dict[str, Any], transfer_id: int | None = None) -> dict[str, Any]:
        """لوحة المراجعة قبل الترحيل (من غير كتابة):

        ``checks`` = [(الحالة ok / no / warn / info، النص)] · ``lines`` = لكل صنف: المتاح في «من» يوم التحويل وبعده ·
        رصيد «إلى» وبعده · التكلفة والقيمة التقريبية · غلط السطر · ``entry`` = سطور القيد اللي هيتعمل (أو ``None``) ·
        ``ready`` = مفيش ✖ (والرصيد الناقص محسوب بصلاحية «من غير رصيد»).
        """
        checks: list[tuple[str, str]] = []
        out: dict[str, Any] = {"checks": checks, "lines": [], "entry": None, "entry_error": "", "ready": False,
                               "value": ZERO, "route_type": "wh"}
        try:
            header = self._header(data)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            return out
        line_error = ""
        try:
            lines = self._lines(data.get("lines") or [], strict=False)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            return out
        try:
            self._lines(data.get("lines") or [])
        except BusinessError as exc:
            line_error = str(exc)
        whs = self.repository.warehouses([header["from_warehouse_id"], header["to_warehouse_id"]])
        fw, tw = whs.get(header["from_warehouse_id"]), whs.get(header["to_warehouse_id"])
        if fw is None or tw is None:
            checks.append(("no", "المخزن مش موجود (ممكن يكون اتمسح)."))
            return out
        route = route_type(fw["id"], tw["id"], fw["is_virtual"], tw["is_virtual"])
        out["route_type"] = route
        day = header["transfer_date"]
        fk, tk = header["from_kind"], header["to_kind"]
        same = fw["id"] == tw["id"]
        checks.append(("ok", f"نفس المخزن «{fw['name_ar']}» بس النوع مختلف ({KINDS[fk]} ← {KINDS[tk]})" if same else
                       f"من «{fw['name_ar']}» ({KINDS[fk]}) ← إلى «{tw['name_ar']}» ({KINDS[tk]})"))
        inactive = [w["name_ar"] for w in (fw, tw) if not w["is_active"]]
        if inactive:
            checks.append(("no", f"المخزن «{inactive[0]}» موقوف — اختار مخزن نشط."))
        if line_error:
            checks.append(("no", line_error))
        else:
            checks.append(("ok", f"{len(lines)} صنف — كل الكميات أكبر من صفر"))

        can_negative = SESSION.can(f"{PERM}.negative")
        short_rows, kind_rows = [], []
        posted_values = self._posted_values(transfer_id) if transfer_id else None
        values: dict[int, Decimal] = {}
        for line in lines:
            item_id, qty = line["item_id"], line["base_qty"]
            if posted_values is not None:
                available = self.ledger.available(item_id, fw["id"], fk)
                to_now = self.ledger.available(item_id, tw["id"], tk)
                after_from, after_to = available, to_now
            else:
                available = self.ledger.available(item_id, fw["id"], fk, on_date=day)
                to_now = self.ledger.available(item_id, tw["id"], tk)
                after_from, after_to = available - qty, to_now + qty
            if posted_values is not None and item_id in posted_values:
                unit_cost, value = posted_values[item_id]
            elif fw["is_virtual"] and tw["is_virtual"]:
                unit_cost, value = None, ZERO
            else:
                unit_cost = self.ledger.average_cost(item_id, tk if fw["is_virtual"] else fk)
                value = (qty * unit_cost).quantize(CENT, rounding=ROUND_HALF_UP)
            values[item_id] = value
            error = ""
            if posted_values is None:
                if fk != tk and not fw["is_virtual"] and not tw["is_virtual"]:
                    company = self.ledger.available(item_id, None, fk)
                    if qty > company:
                        error = (f"مايعدّيش رصيد «{KINDS[fk]}» في الشركة كلها ({fmt_qty(company)}) — "
                                 "حتى بصلاحية «من غير رصيد»")
                        kind_rows.append(line)
                if not error and qty > available:
                    error = f"ناقص {fmt_qty(qty - available)} {line['item']['unit_name']} في «{fw['name_ar']}»"
                    short_rows.append(line)
            out["lines"].append({"item_id": item_id, "item_code": line["item"]["code"],
                                 "item_name": line["item"]["name"], "unit_name": line["item"]["unit_name"],
                                 "qty": qty, "from_before": available, "from_after": after_from,
                                 "to_before": to_now, "to_after": after_to, "unit_cost": unit_cost, "value": value,
                                 "error": error})
        out["value"] = sum(values.values(), ZERO)
        if posted_values is None and lines:
            if short_rows:
                checks.append(("warn" if can_negative else "no",
                               f"{len(short_rows)} صنف رصيده مش مكفي في «{fw['name_ar']}» يوم {day:%d/%m/%Y} — " +
                               ("هيتحوّل بصلاحية «من غير رصيد» وتكلفته بآخر متوسط لحد ما شراء يغطّيه"
                                if can_negative else "ممنوع من غير صلاحية «من غير رصيد»")))
            else:
                checks.append(("ok", f"الرصيد مكفي في «{fw['name_ar']}» يوم {day:%d/%m/%Y}"))
            if fk != tk and not fw["is_virtual"] and not tw["is_virtual"]:
                checks.append(("no", f"{len(kind_rows)} صنف التحويل فيه أكبر من رصيد «{KINDS[fk]}» في الشركة"
                               ) if kind_rows else ("ok", f"{KINDS[fk]} ← {KINDS[tk]}: مايعدّيش رصيد النوع في الشركة"))
        year = self.repository.year_of(day)
        if year is None:
            checks.append(("no", f"مفيش سنة مالية فيها تاريخ {day:%d/%m/%Y}."))
        elif year["status"] == "closed":
            checks.append(("no", f"السنة المالية {year['year_code']} مقفولة."))
        else:
            checks.append(("ok", f"التاريخ في سنة {year['year_code']} المفتوحة"))

        # القيد
        posting = self._value_lines(header, lines, whs, values)
        if fw["is_virtual"] == tw["is_virtual"]:
            checks.append(("info", "من وهمي لوهمي: كميات بس — مفيش قيد" if fw["is_virtual"] else
                           "مفيش قيد — المخزنين على نفس حساب المخزون (المخزن بُعد)، والحركة في دفتر المخزون بس"))
        elif not posting:
            checks.append(("info", "القيمة صفر — مفيش قيد"))
        else:
            try:
                built = self.posting.build(DOC_TYPE, posting)
                out["entry"] = self._named(built)
                what = "مصروف ← المخزون" if tw["is_virtual"] else "المخزون ← مصروف"
                checks.append(("info", f"هيتعمل قيد {fmt(out['value'])} ({what}) بقيمة الحركات وقت الترحيل"
                               if posted_values is None else f"اتعمل قيد {fmt(out['value'])} ({what})"))
            except BusinessError as exc:
                out["entry_error"] = str(exc)
                checks.append(("no", f"القيد: {exc}"))
        if posted_values is None:
            checks.append(("info", "وقت الترحيل الأصناف بتتقفل — جهاز تاني بيصرف نفس الصنف بيستنى ثواني"))
        out["ready"] = bool(lines) and not any(c[0] == "no" for c in checks)
        return out

    def _named(self, built: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self.posting_accounts([ln["account_id"] for ln in built])
        return [{"account_code": names.get(ln["account_id"], {}).get("code", ""),
                 "account_name": names.get(ln["account_id"], {}).get("name_ar", ""),
                 "debit": ln["debit"], "credit": ln["credit"]} for ln in built]

    def posting_accounts(self, ids: list[int]) -> dict[int, dict]:
        rows = self.db.fetch_all("SELECT id, code, name_ar FROM acc.accounts WHERE id = ANY(%s)",
                                 [sorted(set(ids))])
        return {int(r["id"]): r for r in rows}

    def _posted_values(self, transfer_id: int) -> dict[int, tuple[Decimal | None, Decimal]] | None:
        """التحويل المرحّل: تكلفة وقيمة كل صنف من حركاته (الخارج الحقيقي، أو الداخل لو «من» وهمي)."""
        rec = self.repository.get_transfer(int(transfer_id))
        if rec is None or rec["status"] != "posted":
            return None
        out: dict[int, tuple[Decimal | None, Decimal]] = {}
        for m in self.repository.movements(int(transfer_id)):
            if m["is_virtual"]:
                out.setdefault(int(m["item_id"]), (None, ZERO))
                continue
            if m["qty"] < 0 or rec["from_virtual"]:
                out[int(m["item_id"])] = (m["unit_cost"], abs(Decimal(m["value"])))
        return out

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, transfer_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_transfer(int(transfer_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=transfer_id)
        if before is None:
            raise BusinessError("التحويل ده مش موجود (ممكن يكون اتمسح).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — تحويلاتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"التحويل {transfer_label(before)} مرحّل — الغي ترحيله الأول عشان تعدّله.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"التحويل {transfer_label(before)} مش مرحّل.")
        before["lines"] = self.repository.lines(int(transfer_id), ex=tx)
        return before

    def _snapshot(self, tx: Executor, transfer_id: int) -> dict[str, Any]:
        row = self.repository.get_transfer(int(transfer_id), ex=tx)
        row["lines"] = self.repository.lines(int(transfer_id), ex=tx)
        return row

    def _record(self, tx: Executor, action: str, transfer_id: int, before: dict | None, after: dict | None) -> None:
        row = after or before or {}
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=transfer_id, entity_label=ENTITY_LABEL,
                          record_label=f"{transfer_label(row)} — {row.get('from_name') or ''} ← "
                                       f"{row.get('to_name') or ''} — {len(row.get('lines') or [])} صنف",
                          before=before, after=after)

    def _post(self, tx: Executor, transfer_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """الحالة ← مرحّل (القاعدة بتصرف الرقم) ← حركات دفتر المخزون ← القيد لو الوهمي داخل — كله في نفس الـ transaction."""
        updated = self.repository.set_status(transfer_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=transfer_id)
        v = self._snapshot(tx, transfer_id)
        stock: list[StockLine] = []
        for line in v["lines"]:
            qty = Decimal(line["base_qty"])
            out_index = len(stock)
            stock.append(StockLine(line["item_id"], v["from_warehouse_id"], -qty, v["from_kind"]))
            stock.append(StockLine(line["item_id"], v["to_warehouse_id"], qty, v["to_kind"], transfer_of=out_index))
        moved = self.ledger.post(tx, doc_type=STOCK_DOC, source_id=transfer_id, source_no=v["transfer_no"],
                                 movement_date=v["transfer_date"], lines=stock,
                                 allow_negative=SESSION.can(f"{PERM}.negative"))
        if bool(v["from_virtual"]) != bool(v["to_virtual"]):
            values: dict[int, Decimal] = {}
            for m in moved:
                if not m["value"]:
                    continue
                item_id = int(m["item_id"])
                values[item_id] = values.get(item_id, ZERO) + abs(Decimal(m["booked_value"] or m["value"]))
            whs = {v["from_warehouse_id"]: {"is_virtual": v["from_virtual"]},
                   v["to_warehouse_id"]: {"is_virtual": v["to_virtual"]}}
            lines = [{"item_id": ln["item_id"], "item": {"category_id": ln["category_id"]}} for ln in v["lines"]]
            posting = self._value_lines(v, lines, whs, values)
            if posting:
                note = v["description"] or f"تحويل {v['from_name']} ← {v['to_name']}"
                self.posting.post_document(tx, doc_type=DOC_TYPE, source_id=transfer_id, source_no=v["transfer_no"],
                                           entry_date=v["transfer_date"],
                                           description=f"{note} — {v['from_name']} ← {v['to_name']}",
                                           lines=posting)
        self._record(tx, "post", transfer_id, before, v)
        return v

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, transfer_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        """يحفظ المسودة بسطورها (ولو ``post`` يرحّلها في نفس الـ transaction — يا الاتنين يا ولا حاجة)."""
        PermissionService.require(f"{PERM}.create" if transfer_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        header, lines = self._clean(data)
        try:
            with self.db.atomic() as tx:
                before = None
                if transfer_id is None:
                    transfer_id = self.repository.insert_transfer({**header, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock(tx, int(transfer_id), expected_version, draft_only=True)
                    updated = self.repository.update_transfer(int(transfer_id), header, ex=tx,
                                                              expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=transfer_id)
                self.repository.replace_lines(int(transfer_id), lines, ex=tx)
                after = self._snapshot(tx, int(transfer_id))
                self._record(tx, "create" if before is None else "update", int(transfer_id), before, after)
                if post:
                    self._post(tx, int(transfer_id), after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(transfer_id)

    def post(self, transfer_id: int, expected_version: int | None = None) -> str:
        """ترحيل مسودة محفوظة ← بيرجّع الرقم الرسمي."""
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(transfer_id), expected_version, draft_only=True)
                after = self._post(tx, int(transfer_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["transfer_no"]

    def unpost(self, transfer_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل (بالصلاحية بس): الحركات والقيد بيتمسحوا والتحويل بيرجع مسودة برقمه."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(transfer_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=int(transfer_id))
                self.ledger.remove(tx, doc_type=STOCK_DOC, source_id=int(transfer_id),
                                   allow_negative=SESSION.can(f"{PERM}.negative"))
                updated = self.repository.set_status(int(transfer_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=transfer_id)
                self._record(tx, "unpost", int(transfer_id), before, self._snapshot(tx, int(transfer_id)))
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_transfer(self, transfer_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(transfer_id), expected_version, draft_only=True)
                if before["transfer_no"]:
                    raise BusinessError(f"التحويل {before['transfer_no']} خد رقم رسمي قبل كده — مايتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).")
                deleted = self.repository.delete_transfer(int(transfer_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=transfer_id)
                self._record(tx, "delete", int(transfer_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def entry_for(self, transfer_id: int) -> dict[str, Any] | None:
        """قيد التحويل المرحّل بسطوره (للوهمي بس)."""
        return self.posting.entry_for(DOC_TYPE, int(transfer_id))
