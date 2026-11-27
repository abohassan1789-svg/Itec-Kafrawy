"""«الجرد والتسويات» — المرحلة 6، النموذج 1 «الكلاسيك» (اختيار المستخدم 2026-11-27).

* **الجرد مستند لمخزن واحد**: لكل (صنف + نوع فاتورة / بيان) «الفعلي» بالوحدة الأساسية قصاد **الرصيد الدفتري**
  (من دفتر المخزون آخر يوم الجرد). الفرق = الفعلي − الدفتري: **عجز** خروج بالمتوسط · **زيادة** دخول بالمتوسط
  (ولو الصنف مالوش متوسط التكلفة بتتكتب على السطر) · المطابق مالوش حركة.
* **اللي مااتعدّش بيفضل برصيده** — إلا لو «اللي مااتعدّش = صفر» (الجرد الشامل): الترحيل بيعمله سطر بصفر لوحده.
* **«تسوية مباشرة»** (نوع من أنواع المستند، من غير جرد): السطر = صنف + نوع + كمية ± + السبب (تالف · كسر · عينات ·
  فرق عد · منتهي).
* **«حفظ مسودة»**: السطور اللي اتغيّرت بس، **كل سطر بإصداره** (جهازين بيعدّوا أصناف مختلفة في نفس الجرد الاتنين
  بيتحفظوا · نفس السطر التاني بيترفض بتعارض)، والرأس بيتعدّل (بإصداره) بس لو بياناته اتغيّرت.
* **«ترحيل»**: الأصناف بتتقفل ← الدفتري بيتحسب لحظتها ← الدفتري والفرق بيتكتبوا على السطور ← القاعدة بتصرف رقم
  ``SC-2026-00001`` ← **دفتر المخزون** بيسجّل حركة «تسوية جرد» لكل فرق ← **محرك الترحيل** بيعمل القيد بقيمة
  الحركات: الزيادة مدين المخزون / دائن «تسوية جرد المخزون» والعجز العكس. **المخزن الوهمي كميات بس من غير قيد.**
* الرصيد هيبقى بالسالب (جرد بتاريخ قديم واتصرف بعده) ← **ممنوع إلا بصلاحية «من غير رصيد»** للشاشة دي.
* **«إلغاء الترحيل»** بالصلاحية في السنة المفتوحة: الحركات والقيد بيتمسحوا والجرد يرجع مسودة **برقمه**.
* **«إظهار الدفتري»** (صلاحية): اللي مالوش الصلاحية بيعدّ «أعمى» — الخدمة مابترجّعلوش الدفتري ولا المتوسط.
* كل حاجة في ``sec.audit_log`` (الرأس + السطور اللي اتغيّرت).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.stock_count_repository import FIELDS, LINES, STATUSES, STOCK_DOC, StockCountRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService
from app.services.stock_ledger_service import KINDS, StockLedgerService, StockLine, fmt_qty

PAGE_SIZE = 50
ZERO = Decimal("0")
CENT = Decimal("0.01")
QTY_STEP = Decimal("0.000001")
MAX_QTY = Decimal("999999999999")
MAX_COST = Decimal("999999999999")
MAX_LINES = 20000
MAX_IMPORT = 20000
BIG_DIFF = Decimal("0.2")            # فرق أكبر من 20% من الرصيد ← تنبيه
NEGATIVE_CHECK_LIMIT = 60            # أكتر من كده المعاينة مابتسألش عن «المتاح» سطر سطر (الترحيل بيتأكد)
DOC_TYPE = STOCK_DOC                 # القيد (acc.posting_doc_types) ودفتر المخزون (inv.stock_doc_types)
ENTITY, ENTITY_LABEL = "inv.stock_counts", "جرد وتسوية مخزون"
PERM = "inv.stock_counts"
COUNT_TYPES: dict[str, str] = {"periodic": "دوري", "annual": "سنوي", "partial": "جزئي", "direct": "تسوية مباشرة"}
REASONS: dict[str, str] = {"damaged": "تالف", "broken": "كسر", "samples": "عينات", "count_diff": "فرق عد",
                           "expired": "منتهي الصلاحية"}
KIND_WORDS = {"فاتورة": "invoice", "invoice": "invoice", "بيان": "statement", "statement": "statement", "": "invoice"}
SHEET_COLUMNS = ("كود الصنف", "اسم الصنف", "النوع", "الوحدة", "الدفتري", "الفعلي")
_HEADER_WORDS = {"code": ("كود الصنف", "الكود", "كود", "code"), "kind": ("النوع", "فاتورة / بيان", "kind"),
                 "counted": ("الفعلي", "العدّ", "العد", "الكمية", "counted", "qty")}


def count_label(row: dict[str, Any]) -> str:
    """«SC-2026-00012» أو «مسودة 7»."""
    return row.get("count_no") or f"مسودة {row.get('draft_no')}"


def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def _number(value: Any, label: str) -> Decimal | None:
    """الفاضي ← ``None`` · غير كده رقم بـ 6 كسور بالكتير."""
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value if value is not None else "").strip().replace(",", "").replace("٫", ".")
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise BusinessError(f"«{label}»: «{value}» مش رقم.") from None
    if not number.is_finite():
        raise BusinessError(f"«{label}»: «{value}» مش رقم.")
    if number != number.quantize(QTY_STEP):
        raise BusinessError(f"«{label}»: فيه كسور أكتر من 6 أرقام.")
    return number


def parse_counted(value: Any, label: str = "الفعلي") -> Decimal | None:
    """الفعلي: الفاضي = مااتعدّش (``None``) · الصفر مسموح (الصنف مش موجود) · السالب ممنوع."""
    qty = _number(value, label)
    if qty is None:
        return None
    if qty < 0:
        raise BusinessError(f"«{label}»: الفعلي مايبقاش بالسالب.")
    if qty > MAX_QTY:
        raise BusinessError(f"«{label}»: الكمية أكبر من المسموح.")
    return qty


def parse_adjust(value: Any, label: str = "التسوية") -> Decimal | None:
    """كمية التسوية المباشرة: + زيادة · − عجز · الفاضي / الصفر = مفيش تسوية (``None``)."""
    qty = _number(value, label)
    if qty is None or qty == 0:
        return None
    if abs(qty) > MAX_QTY:
        raise BusinessError(f"«{label}»: الكمية أكبر من المسموح.")
    return qty


def parse_cost(value: Any, label: str = "التكلفة") -> Decimal | None:
    cost = _number(value, label)
    if cost is None:
        return None
    if cost < 0:
        raise BusinessError(f"«{label}»: التكلفة مينفعش تبقى بالسالب.")
    if cost > MAX_COST:
        raise BusinessError(f"«{label}»: التكلفة أكبر من المسموح.")
    return cost


def line_state(*, direct: bool, book: Decimal, counted: Decimal | None = None, adjust: Decimal | None = None,
               avg_cost: Decimal = ZERO, typed_cost: Decimal | None = None, seen: Decimal | None = None,
               is_virtual: bool = False, zero_uncounted: bool = False) -> dict[str, Any]:
    """حالة سطر (للشاشة والمعاينة والترحيل — نفس الحسبة):

    ``state`` = none (مااتعدّش) · match (مطابق) · short (عجز) · over (زيادة) · ``diff`` · ``after`` (الرصيد بعد
    الترحيل) · ``cost`` و ``value`` (القيمة بالإشارة — تقريبية للعجز لحد الترحيل) · ``need_cost`` = زيادة لصنف مالوش
    متوسط (خانة التكلفة بتتفتح) · ``error`` (بيمنع الترحيل) · ``warning`` · ``auto`` = اتحسب صفر من «اللي مااتعدّش».
    """
    out: dict[str, Any] = {"state": "none", "diff": None, "after": book, "cost": None, "value": ZERO,
                           "need_cost": False, "error": "", "warning": "", "auto": False}
    if direct:
        if adjust is None:
            return out
        diff = adjust
    elif counted is None:
        if not zero_uncounted or not book:
            return out
        diff, out["auto"] = -book, True
    else:
        diff = counted - book
    out["diff"], out["after"] = diff, book + diff
    out["state"] = "match" if diff == 0 else ("short" if diff < 0 else "over")
    if diff and not is_virtual:
        if diff > 0 and not avg_cost:
            out["need_cost"] = True
            if typed_cost is None:
                out["error"] = "زيادة لصنف مالوش متوسط تكلفة — اكتب تكلفة الوحدة"
            cost = typed_cost
        else:
            cost = avg_cost
        out["cost"] = cost
        if cost is not None:
            out["value"] = (diff * cost).quantize(CENT, rounding=ROUND_HALF_UP)
    if not out["auto"] and not direct:
        if seen is not None and seen != book:
            out["warning"] = f"الدفتري اتغيّر بعد ما اتعدّ (كان {fmt_qty(seen)} بقى {fmt_qty(book)}) — راجع العدّ"
        elif diff and book > 0 and abs(diff) > book * BIG_DIFF:
            out["warning"] = "فرق أكبر من 20% من الرصيد — راجع العدّ"
    return out


def _key(text: Any) -> str:
    return " ".join(str(text if text is not None else "").split()).lower()


class StockCountService:
    def __init__(self, db: Database | None = None, repository: StockCountRepository | None = None,
                 ledger: StockLedgerService | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or StockCountRepository(self.db)
        self.ledger = ledger or StockLedgerService(self.db)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    @staticmethod
    def can_show_book() -> bool:
        """المستخدم يشوف الرصيد الدفتري والفروق؟ (من غيرها = جرد أعمى)."""
        return SESSION.can(f"{PERM}.show_book")

    # ---- قراية -------------------------------------------------------------
    def list_counts(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                    keyword: str = "", page: int = 1, page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        rows = self.repository.list_counts(date_from, date_to, status, keyword.strip(), limit=page_size,
                                           offset=offset)
        if not self.can_show_book():
            for row in rows:
                row["net_value"] = None
        return rows

    def count_counts(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                     keyword: str = "") -> dict[str, int]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_counts(date_from, date_to, status, keyword.strip())
        return {k: int(row[k]) for k in ("total", "drafts", "posted")}

    def get_count(self, count_id: int) -> dict[str, Any] | None:
        row = self.repository.get_count(int(count_id))
        if row is not None:
            row["label"] = count_label(row)
        return row

    def choices(self) -> dict[str, Any]:
        return {"warehouses": self.repository.warehouse_choices(), "items": self.repository.item_choices()}

    def sheet(self, warehouse_id: int | None, count_date: dt.date, count_id: int | None = None,
              count_type: str = "periodic") -> list[dict[str, Any]]:
        """صفوف جدول العدّ: كل (صنف + نوع) ليه رصيد دفتري في المخزن آخر يوم الجرد + السطور المحفوظة (التسوية
        المباشرة: المحفوظ بس) · الجرد المرحّل: سطوره بالدفتري والفرق والقيمة اللي اترحّلت."""
        rec = self.repository.get_count(int(count_id)) if count_id else None
        saved = self.repository.lines(int(count_id)) if count_id else []
        show = self.can_show_book()
        if rec is not None and rec["status"] == "posted":
            moves = {(int(m["item_id"]), m["stock_kind"]): m for m in self.repository.movements(int(count_id))}
            rows = []
            for line in saved:
                m = moves.get((int(line["item_id"]), line["stock_kind"]))
                rows.append(self._row(line, line, line["book_qty"], None, show, posted={
                    "diff_qty": line["diff_qty"], "cost": m["unit_cost"] if m and not m["is_virtual"] else None,
                    "value": Decimal(m["value"]) if m and not m["is_virtual"] else ZERO}))
            return rows
        if not warehouse_id:
            return []
        direct = count_type == "direct"
        usable = rec is not None and int(rec["warehouse_id"]) == int(warehouse_id) and \
            (rec["count_type"] == "direct") == direct
        lines = {(int(l["item_id"]), l["stock_kind"]): l for l in saved} if usable else {}
        book = self.repository.book_balances(int(warehouse_id), count_date)
        keys = set(lines) | (set() if direct else set(book))
        items = self.repository.items([k[0] for k in keys])
        avgs = self.repository.average_costs(list(items), count_date)
        rows = []
        for key in keys:
            item = items.get(key[0])
            if item is None or item["is_service"]:
                continue
            base = {"item_id": key[0], "stock_kind": key[1], "item_code": item["code"], "item_name": item["name"],
                    "item_active": item["is_active"], "unit_name": item["unit_name"],
                    "category_name": item["category_name"]}
            rows.append(self._row(base, lines.get(key), book.get(key, ZERO), avgs.get(key, ZERO), show))
        rows.sort(key=lambda r: (str(r["item_code"]), r["stock_kind"]))
        return rows

    @staticmethod
    def _row(base: dict[str, Any], line: dict[str, Any] | None, book: Any, avg: Any, show: bool,
             posted: dict[str, Any] | None = None) -> dict[str, Any]:
        line = line or {}
        row = {"item_id": int(base["item_id"]), "stock_kind": base["stock_kind"], "item_code": base["item_code"],
               "item_name": base["item_name"], "item_active": base.get("item_active", True),
               "unit_name": base.get("unit_name") or "", "category_name": base.get("category_name") or "",
               "book_qty": Decimal(book or 0) if show else None,
               "avg_cost": Decimal(avg or 0) if show and avg is not None else None,
               "line_id": line.get("id"), "row_version": line.get("row_version"),
               "counted_qty": line.get("counted_qty"), "adjust_qty": line.get("adjust_qty"),
               "reason": line.get("reason"), "unit_cost": line.get("unit_cost"),
               "seen_book_qty": line.get("seen_book_qty") if show else None,
               "auto_zero": bool(line.get("auto_zero")), "posted": posted is not None}
        if posted is not None:
            row["diff_qty"] = posted["diff_qty"] if show else None
            row["cost"] = posted["cost"] if show else None
            row["value"] = posted["value"] if show else None
        return row

    def item_state(self, item_id: int, stock_kind: str, warehouse_id: int, count_date: dt.date) -> dict[str, Any]:
        """صف جديد في جدول العدّ (صنف مش في الدفتر / سطر تسوية مباشرة): بيانات الصنف + الدفتري والمتوسط."""
        item = self.repository.items([int(item_id)]).get(int(item_id))
        if item is None:
            raise BusinessError("الصنف مش موجود (ممكن يكون اتمسح).")
        if item["is_service"]:
            raise BusinessError(f"الصنف {item['code']} «{item['name']}» خدمي — مالوش رصيد يتجرد.")
        if stock_kind not in KINDS:
            raise BusinessError("نوع المخزون لازم «فاتورة» أو «بيان».")
        key = (int(item_id), stock_kind)
        book = self.repository.book_balances(int(warehouse_id), count_date).get(key, ZERO)
        avg = self.repository.average_costs([int(item_id)], count_date).get(key, ZERO)
        base = {"item_id": int(item_id), "stock_kind": stock_kind, "item_code": item["code"],
                "item_name": item["name"], "item_active": item["is_active"], "unit_name": item["unit_name"],
                "category_name": item["category_name"]}
        return self._row(base, None, book, avg, self.can_show_book())

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    def _header(self, data: dict[str, Any]) -> dict[str, Any]:
        count_date = data.get("count_date")
        if isinstance(count_date, dt.datetime):
            count_date = count_date.date()
        if not isinstance(count_date, dt.date):
            raise BusinessError("تاريخ الجرد مطلوب.")
        warehouse_id = self._id(data.get("warehouse_id"))
        if warehouse_id is None:
            raise BusinessError("اختار المخزن.")
        count_type = data.get("count_type") or "periodic"
        if count_type not in COUNT_TYPES:
            raise BusinessError("نوع الجرد لازم دوري أو سنوي أو جزئي أو تسوية مباشرة.")
        description = " ".join(str(data.get("description") or "").split()) or None
        if description and len(description) > 500:
            raise BusinessError("«الملاحظات» أطول من المسموح (500 حرف).")
        return {"count_date": count_date, "warehouse_id": warehouse_id, "count_type": count_type,
                "zero_uncounted": bool(data.get("zero_uncounted")) and count_type != "direct",
                "description": description}

    def _line(self, raw: dict[str, Any], direct: bool, n: int) -> dict[str, Any]:
        """سطر من الشاشة ← قيم نضيفة · ``delete`` = الخانة فاضية (السطر يتشال)."""
        item_id = self._id(raw.get("item_id"))
        label = raw.get("label") or f"سطر {n}"
        if item_id is None:
            raise BusinessError(f"{label}: اختار الصنف.")
        kind = str(raw.get("stock_kind") or "invoice")
        if kind not in KINDS:
            raise BusinessError(f"{label}: النوع لازم «فاتورة» أو «بيان».")
        out: dict[str, Any] = {"item_id": item_id, "stock_kind": kind, "label": label, "counted_qty": None,
                               "adjust_qty": None, "reason": None,
                               "unit_cost": parse_cost(raw.get("unit_cost"), f"تكلفة {label}"),
                               "row_version": self._id(raw.get("row_version"))}
        if direct:
            out["adjust_qty"] = parse_adjust(raw.get("adjust_qty"), f"تسوية {label}")
            if out["adjust_qty"] is not None:
                reason = raw.get("reason") or ""
                if reason not in REASONS:
                    raise BusinessError(f"{label}: اختار سبب التسوية.")
                out["reason"] = reason
        else:
            out["counted_qty"] = parse_counted(raw.get("counted_qty"), f"فعلي {label}")
        out["delete"] = out["counted_qty"] is None and out["adjust_qty"] is None
        if out["delete"]:
            out["unit_cost"] = None
        return out

    def _lines(self, raw: list[dict[str, Any]], direct: bool, *, strict: bool = True) -> list[dict[str, Any]]:
        rows = list(raw or [])
        if len(rows) > MAX_LINES:
            raise BusinessError(f"الجرد الواحد لحد {MAX_LINES:,} سطر.")
        out: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()
        for n, r in enumerate(rows, start=1):
            try:
                line = self._line(r, direct, n)
            except BusinessError:
                if strict:
                    raise
                continue
            key = (line["item_id"], line["stock_kind"])
            if key in seen:
                if strict:
                    raise BusinessError(f"{line['label']}: الصنف مكرر بنفس النوع في الجرد.")
                continue
            seen.add(key)
            out.append(line)
        return out

    # ---- المعاينة (الإجماليات · الأخطاء · القيد اللي هيتعمل) ------------------------------------
    def review(self, data: dict[str, Any], count_id: int | None = None) -> dict[str, Any]:
        """معاينة من غير كتابة — ``data["lines"]`` = **كل** السطور اللي على الشاشة (مش اللي اتغيّر بس).

        ``checks`` = [(ok / no / warn / info، النص)] · ``lines`` = {(الصنف، النوع): حالة السطر} · ``totals`` ·
        ``entry`` = سطور القيد اللي هيتعمل (أو ``None``) · ``ready`` = مفيش ✖. للجرد المرحّل (``count_id``):
        الإجماليات والقيد اللي اترحّلوا.
        """
        checks: list[tuple[str, str]] = []
        totals = {"rows": 0, "counted": 0, "uncounted": 0, "match": 0, "short_n": 0, "short_value": ZERO,
                  "over_n": 0, "over_value": ZERO, "net": ZERO}
        out: dict[str, Any] = {"checks": checks, "lines": {}, "totals": totals, "entry": None, "entry_error": "",
                               "ready": False, "show_book": self.can_show_book()}
        rec = self.repository.get_count(int(count_id)) if count_id else None
        if rec is not None and rec["status"] == "posted":
            return self._posted_review(rec, out)
        try:
            header = self._header(data)
            direct = header["count_type"] == "direct"
            lines = self._lines(data.get("lines") or [], direct, strict=False)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            return out
        line_error = ""
        try:
            self._lines(data.get("lines") or [], direct)
        except BusinessError as exc:
            line_error = str(exc)
        wh = self.repository.warehouse(header["warehouse_id"])
        if wh is None:
            checks.append(("no", "المخزن مش موجود (ممكن يكون اتمسح)."))
            return out
        if not wh["is_active"]:
            checks.append(("no", f"المخزن «{wh['name_ar']}» موقوف — اختار مخزن نشط."))
        day = header["count_date"]
        virtual = bool(wh["is_virtual"])
        entered = {(l["item_id"], l["stock_kind"]): l for l in lines if not l["delete"]}
        book = self.repository.book_balances(header["warehouse_id"], day)
        keys = set(entered) | (set() if direct else set(book))
        items = self.repository.items([k[0] for k in keys])
        avgs = self.repository.average_costs(list(items), day)
        saved = {(int(l["item_id"]), l["stock_kind"]): l for l in self.repository.lines(int(count_id))} \
            if count_id else {}
        errors, changed, big = [], 0, 0
        values: list[tuple[Decimal, Any]] = []
        for key in sorted(keys):
            item = items.get(key[0])
            if item is None or item["is_service"]:
                continue
            line = entered.get(key)
            old = saved.get(key)
            state = line_state(
                direct=direct, book=book.get(key, ZERO), counted=line["counted_qty"] if line else None,
                adjust=line["adjust_qty"] if line else None, avg_cost=avgs.get(key, ZERO),
                typed_cost=line["unit_cost"] if line else None,
                seen=old["seen_book_qty"] if old is not None and line is not None
                and old["counted_qty"] is not None and Decimal(old["counted_qty"]) == line["counted_qty"]
                else None,                                      # اتعدّ تاني دلوقتي ← مفيش تنبيه قديم
                is_virtual=virtual, zero_uncounted=header["zero_uncounted"])
            state["category_id"] = item["category_id"]
            state["label"] = f"{item['code']} «{item['name']}» ({KINDS[key[1]]})"
            out["lines"][key] = state
            totals["rows"] += 1
            if line is None:
                totals["uncounted"] += 1
            else:
                totals["counted"] += 1
            if state["state"] == "match":
                totals["match"] += 1
            elif state["state"] == "short":
                totals["short_n"] += 1
                totals["short_value"] += -state["value"]
            elif state["state"] == "over":
                totals["over_n"] += 1
                totals["over_value"] += state["value"]
            if state["error"]:
                errors.append(f"{state['label']}: {state['error']}")
            if state["warning"].startswith("الدفتري"):
                changed += 1
            elif state["warning"]:
                big += 1
            if state["value"]:
                values.append((state["value"], item["category_id"]))
        totals["net"] = totals["over_value"] - totals["short_value"]

        what = "تسوية" if direct else "اتعدّ"
        if line_error:
            checks.append(("no", line_error))
        if not totals["counted"] and not (header["zero_uncounted"] and totals["rows"]):
            checks.append(("no", "مفيش ولا سطر تسوية — ضيف صنف واكتب الكمية." if direct else
                           "مفيش ولا صنف اتعدّ — اكتب الفعلي لصنف واحد على الأقل."))
        elif direct:
            checks.append(("ok", f"{totals['counted']} سطر {what} في «{wh['name_ar']}»"))
        else:
            checks.append(("ok", f"{what} {totals['counted']} من {totals['rows']} سطر (صنف + نوع) في "
                                 f"«{wh['name_ar']}»"))
        for text in errors[:5]:
            checks.append(("no", text))
        if len(errors) > 5:
            checks.append(("no", f"و{len(errors) - 5} سطر تاني فيهم نفس المشكلة."))
        if changed:
            checks.append(("warn", f"{changed} سطر الرصيد الدفتري بتاعه اتغيّر بعد ما اتعدّ (حركة من جهاز تاني) — "
                                   "راجع العدّ؛ الفرق بيتحسب على الدفتري وقت الترحيل."))
        if big:
            checks.append(("warn", f"{big} سطر الفرق فيه أكبر من 20% من الرصيد — راجع العدّ."))
        if not direct and totals["uncounted"]:
            checks.append(("warn", f"{totals['uncounted']} سطر مااتعدّش هيتحسبوا صفر (عجز بكل رصيدهم) — "
                                   "«اللي مااتعدّش = صفر» متعلّم.") if header["zero_uncounted"] else
                          ("info", f"{totals['uncounted']} سطر مااتعدّش — هيفضلوا برصيدهم زي ما هم."))
        if totals["match"]:
            checks.append(("ok", f"{totals['match']} سطر مطابق — من غير حركة"))
        if totals["short_n"]:
            checks.append(("info", f"عجز: {totals['short_n']} سطر" +
                           ("" if virtual else f" بقيمة {fmt(totals['short_value'])} (بالمتوسط)")))
        if totals["over_n"]:
            checks.append(("info", f"زيادة: {totals['over_n']} سطر" +
                           ("" if virtual else f" بقيمة {fmt(totals['over_value'])}")))
        self._negative_check(checks, out["lines"], header, wh, count_id)
        year = self.repository.year_of(day)
        if year is None:
            checks.append(("no", f"مفيش سنة مالية فيها تاريخ {day:%d/%m/%Y}."))
        elif year["status"] == "closed":
            checks.append(("no", f"السنة المالية {year['year_code']} مقفولة."))
        else:
            checks.append(("ok", f"التاريخ في سنة {year['year_code']} المفتوحة"))

        if virtual:
            checks.append(("info", "المخزن الوهمي: الكميات بتتصلّح بس — من غير قيد"))
        elif not values:
            checks.append(("info", "مفيش فروق ليها قيمة — مفيش قيد"))
        else:
            try:
                built = self.posting.build(DOC_TYPE, self._posting_lines(values))
                out["entry"] = self._named(built)
                checks.append(("info", f"هيتعمل قيد على «تسوية جرد المخزون» — الصافي {fmt(totals['net'])} "
                                       "(بقيمة الحركات وقت الترحيل)"))
            except BusinessError as exc:
                out["entry_error"] = str(exc)
                checks.append(("no", f"القيد: {exc}"))
        out["ready"] = not any(c[0] == "no" for c in checks)
        return self._hide_book(out)

    @staticmethod
    def _hide_book(out: dict[str, Any]) -> dict[str, Any]:
        """من غير صلاحية «إظهار الدفتري»: المعاينة بترجّع عدد اللي اتعدّ والأغلاط بس (من غير فروق ولا قيم ولا قيد)."""
        if out["show_book"]:
            return out
        totals = out["totals"]
        for key in ("match", "short_n", "over_n"):
            totals[key] = 0
        for key in ("short_value", "over_value", "net"):
            totals[key] = ZERO
        out["lines"], out["entry"], out["entry_error"] = {}, None, ""
        out["checks"][:] = [c for c in out["checks"] if c[0] == "no"]
        return out

    def _negative_check(self, checks: list[tuple[str, str]], states: dict[tuple[int, str], dict[str, Any]],
                        header: dict[str, Any], wh: dict[str, Any], count_id: int | None) -> None:
        """العجز بتاريخ الجرد هيخلّي يوم بعده بالسالب؟ (اتصرف من الصنف بعد يوم الجرد)."""
        shorts = [(k, s) for k, s in states.items() if s["state"] == "short"]
        if not shorts or len(shorts) > NEGATIVE_CHECK_LIMIT:
            return
        bad = []
        for key, state in shorts:
            available = self.ledger.available(key[0], header["warehouse_id"], key[1], on_date=header["count_date"])
            if -state["diff"] > available:
                bad.append(state["label"])
        if not bad:
            return
        can = SESSION.can(f"{PERM}.negative")
        tail = "هيترحّل بصلاحية «من غير رصيد»" if can else "ممنوع من غير صلاحية «من غير رصيد»"
        checks.append(("warn" if can else "no",
                       f"{len(bad)} صنف العجز بتاعه هيخلّي رصيد «{wh['name_ar']}» بالسالب ({bad[0]}"
                       f"{' …' if len(bad) > 1 else ''}) — {tail}"))

    def _posted_review(self, rec: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
        totals, checks = out["totals"], out["checks"]
        lines = self.repository.lines(int(rec["id"]))
        moves = {(int(m["item_id"]), m["stock_kind"]): m for m in self.repository.movements(int(rec["id"]))}
        for line in lines:
            key = (int(line["item_id"]), line["stock_kind"])
            diff = Decimal(line["diff_qty"] or 0)
            m = moves.get(key)
            value = Decimal(m["value"]) if m and not m["is_virtual"] else ZERO
            totals["rows"] += 1
            totals["counted"] += 1
            if diff == 0:
                totals["match"] += 1
            elif diff < 0:
                totals["short_n"] += 1
                totals["short_value"] += -value
            else:
                totals["over_n"] += 1
                totals["over_value"] += value
            out["lines"][key] = {"state": "match" if diff == 0 else ("short" if diff < 0 else "over"),
                                 "diff": diff, "after": Decimal(line["book_qty"] or 0) + diff,
                                 "cost": m["unit_cost"] if m and not m["is_virtual"] else None, "value": value,
                                 "need_cost": False, "error": "", "warning": "", "auto": bool(line["auto_zero"])}
        totals["net"] = totals["over_value"] - totals["short_value"]
        entry = self.posting.entry_for(DOC_TYPE, int(rec["id"]))
        if entry:
            out["entry"] = [{"account_code": ln["account_code"], "account_name": ln["account_name"],
                             "debit": ln["debit"], "credit": ln["credit"]} for ln in entry.get("lines") or []]
        checks.append(("ok", f"مرحّل برقم {rec['count_no']} — {totals['short_n']} عجز · {totals['over_n']} زيادة · "
                             f"{totals['match']} مطابق"))
        out["ready"] = True
        return self._hide_book(out)

    @staticmethod
    def _posting_lines(values: list[tuple[Decimal, Any]]) -> list[PostingLine]:
        """الزيادة بمبلغ موجب (مدين المخزون / دائن التسوية) · العجز بالسالب (الجنب بيتقلب)."""
        lines: list[PostingLine] = []
        for value, category_id in values:
            if value:
                lines.append(PostingLine("inventory", value, category_id=category_id))
                lines.append(PostingLine("adjustment", value))
        return lines

    def _named(self, built: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = sorted({ln["account_id"] for ln in built})
        names = {int(r["id"]): r for r in self.db.fetch_all(
            "SELECT id, code, name_ar FROM acc.accounts WHERE id = ANY(%s)", [ids])}
        return [{"account_code": names.get(ln["account_id"], {}).get("code", ""),
                 "account_name": names.get(ln["account_id"], {}).get("name_ar", ""),
                 "debit": ln["debit"], "credit": ln["credit"]} for ln in built]

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, count_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_count(int(count_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=count_id)
        if before is None:
            raise BusinessError("الجرد ده مش موجود (ممكن يكون اتمسح).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — جرودها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"الجرد {count_label(before)} مرحّل — الغي ترحيله الأول عشان تعدّله.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"الجرد {count_label(before)} مش مرحّل.")
        return before

    @staticmethod
    def _line_audit(line: dict[str, Any] | None) -> dict[str, Any] | None:
        if line is None:
            return None
        return {k: line.get(k) for k in ("id", "item_id", "item_code", "item_name", "stock_kind", "counted_qty",
                                         "adjust_qty", "reason", "unit_cost", "book_qty", "diff_qty", "auto_zero")}

    def _record(self, tx: Executor, action: str, count_id: int, before: dict | None, after: dict | None,
                changes: list[tuple[dict | None, dict | None]] | None = None) -> None:
        row = after or before or {}
        lines = None
        if changes:
            lines = {LINES: {"before": [self._line_audit(b) for b, _a in changes if b is not None],
                             "after": [self._line_audit(a) for _b, a in changes if a is not None]}}
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=count_id, entity_label=ENTITY_LABEL,
                          record_label=f"{count_label(row)} — {row.get('warehouse_name') or ''} — "
                                       f"{COUNT_TYPES.get(row.get('count_type'), '')}",
                          before=before, after=after, lines=lines)

    @staticmethod
    def _line_conflict(label: str, deleted: bool = False) -> ConcurrentEditError:
        if deleted:
            return ConcurrentEditError(f"{label}: السطر ده اتمسح من جهاز تاني بعد ما فتحت الجرد — دوس «تحديث» "
                                       "وشوف العدّ الجديد.", deleted=False)
        return ConcurrentEditError(f"{label}: الصنف ده اتعدّ (أو اتعدّل) من جهاز تاني بعد ما فتحت الجرد — دوس "
                                   "«تحديث» وشوف العدّ الجديد.")

    def _apply_line(self, tx: Executor, count_id: int, change: dict[str, Any],
                    book: dict[tuple[int, str], Decimal]) -> tuple[dict | None, dict | None] | None:
        """تغيير سطر واحد بالإصدار اللي الشاشة شافته ← (قبل، بعد) أو ``None`` لو مفيش حاجة اتغيّرت."""
        key = (change["item_id"], change["stock_kind"])
        label = change["label"]
        existing = self.repository.find_line(count_id, key[0], key[1], tx, for_update=True)
        version = change["row_version"]
        values = {"counted_qty": change["counted_qty"], "adjust_qty": change["adjust_qty"],
                  "reason": change["reason"], "unit_cost": change["unit_cost"]}
        if existing is None:
            if change["delete"]:
                return None                                       # الشاشة شايفاه فاضي (أو اتمسح خلاص)
            if version:
                raise self._line_conflict(label, deleted=True)
            new_id = self.repository.insert_line(
                count_id, {**values, "item_id": key[0], "stock_kind": key[1], "seen_book_qty": book.get(key, ZERO)},
                SESSION.user_id, tx)
            return None, self.repository.find_line(count_id, key[0], key[1], tx) or {"id": new_id}
        same = not change["delete"] and all(
            (existing[k] is None) == (v is None) and (v is None or (
                Decimal(existing[k]) == v if isinstance(v, Decimal) else existing[k] == v))
            for k, v in values.items())
        if not version or int(version) != int(existing["row_version"]):
            if same:
                return None                                       # الجهاز التاني كتب نفس القيمة — مفيش تعارض
            raise self._line_conflict(label)
        if change["delete"]:
            if not self.repository.delete_line(int(existing["id"]), tx, expected_version=existing["row_version"]):
                raise self._line_conflict(label)
            return existing, None
        if same:
            return None
        if not self.repository.update_line(int(existing["id"]), {**values, "seen_book_qty": book.get(key, ZERO)},
                                           SESSION.user_id, tx, expected_version=existing["row_version"]):
            raise self._line_conflict(label)
        return existing, self.repository.find_line(count_id, key[0], key[1], tx)

    def _post(self, tx: Executor, count_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """قفل الأصناف ← الدفتري والفرق على السطور ← الحالة مرحّل (القاعدة بتصرف الرقم) ← حركات «تسوية جرد» ←
        القيد بقيمتها — كله في نفس الـ transaction."""
        v = self.repository.get_count(count_id, ex=tx)
        direct = v["count_type"] == "direct"
        wh, day, virtual = int(v["warehouse_id"]), v["count_date"], bool(v["is_virtual"])
        lines = self.repository.lines(count_id, ex=tx)
        item_ids = {int(l["item_id"]) for l in lines}
        book: dict[tuple[int, str], Decimal] = {}
        for _ in range(4):                 # الأصناف بتتقفل وبعدين الدفتري يتقرا — محدش يحرّكها وإحنا بنحسب
            if item_ids:
                self.ledger.repository.lock_items(sorted(item_ids), tx)
            book = self.repository.book_balances(wh, day, ex=tx)
            extra = {k[0] for k in book} - item_ids if v["zero_uncounted"] else set()
            if not extra:
                break
            item_ids |= extra
        if v["zero_uncounted"]:
            have = {(int(l["item_id"]), l["stock_kind"]) for l in lines}
            for key in sorted(set(book) - have):
                self.repository.insert_line(count_id, {"item_id": key[0], "stock_kind": key[1], "counted_qty": ZERO,
                                                       "seen_book_qty": book[key], "auto_zero": True},
                                            SESSION.user_id, tx)
            lines = self.repository.lines(count_id, ex=tx)
        if not lines:
            raise BusinessError("مفيش ولا صنف اتعدّ — اكتب الفعلي لصنف واحد على الأقل قبل الترحيل.")
        if len(lines) > MAX_LINES:
            raise BusinessError(f"الجرد الواحد لحد {MAX_LINES:,} سطر.")
        avgs = self.repository.average_costs(sorted(item_ids), day, ex=tx)
        stock: list[StockLine] = []
        categories: list[Any] = []
        for line in lines:
            key = (int(line["item_id"]), line["stock_kind"])
            b = book.get(key, ZERO)
            diff = Decimal(line["adjust_qty"]) if direct else Decimal(line["counted_qty"]) - b
            self.repository.set_posted_values(int(line["id"]), b, diff, tx)
            if diff == 0:
                continue
            cost = None
            if diff > 0 and not virtual:
                cost = avgs.get(key) or line["unit_cost"]
                if cost is None:
                    raise BusinessError(f"الصنف {line['item_code']} «{line['item_name']}» "
                                        f"({KINDS[line['stock_kind']]}): زيادة لصنف مالوش متوسط تكلفة — "
                                        "اكتب تكلفة الوحدة.")
            stock.append(StockLine(key[0], wh, diff, key[1], unit_cost=cost))
            categories.append(line["category_id"])
        updated = self.repository.set_status(count_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=count_id)
        v = self.repository.get_count(count_id, ex=tx)
        if stock:
            moved = self.ledger.post(tx, doc_type=STOCK_DOC, source_id=count_id, source_no=v["count_no"],
                                     movement_date=day, lines=stock,
                                     allow_negative=SESSION.can(f"{PERM}.negative"))
            values: list[tuple[Decimal, Any]] = []
            if not virtual:
                for m in moved:
                    amount = abs(Decimal(m["booked_value"] or m["value"] or 0))
                    if amount:
                        values.append((amount if m["qty"] > 0 else -amount, categories[int(m["line_no"]) - 1]))
            posting = self._posting_lines(values)
            if posting:
                kind = "تسوية مباشرة" if direct else f"جرد {COUNT_TYPES[v['count_type']]}"
                note = v["description"] or kind
                self.posting.post_document(tx, doc_type=DOC_TYPE, source_id=count_id, source_no=v["count_no"],
                                           entry_date=day, description=f"{note} — {v['warehouse_name']}",
                                           lines=posting)
        self._record(tx, "post", count_id, before, v,
                     [(None, l) for l in self.repository.lines(count_id, ex=tx) if l["diff_qty"]])
        return v

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, count_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        """يحفظ المسودة: الرأس (بإصداره لو اتغيّر) + **السطور اللي اتغيّرت بس** (``data["lines"]`` — كل سطر
        بالإصدار اللي الشاشة شافته) · ولو ``post`` يرحّلها في نفس الـ transaction (يا الاتنين يا ولا حاجة)."""
        PermissionService.require(f"{PERM}.create" if count_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        header = self._header(data)
        direct = header["count_type"] == "direct"
        changes = self._lines(data.get("lines") or [], direct)
        try:
            with self.db.atomic() as tx:
                before = None
                if count_id is None:
                    count_id = self.repository.insert_count({**header, "user_id": SESSION.user_id}, ex=tx)
                else:
                    count_id = int(count_id)
                    before = self._lock(tx, count_id, None, draft_only=True)
                    if any(before[k] != header[k] for k in FIELDS):
                        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=count_id)
                        if int(before["warehouse_id"]) != header["warehouse_id"] or \
                                (before["count_type"] == "direct") != direct:
                            self.repository.delete_lines(count_id, tx)          # عدّ مخزن تاني / شكل تاني
                        updated = self.repository.update_count(count_id, header, ex=tx,
                                                               expected_version=expected_version)
                        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=count_id)
                book = self.repository.book_balances(header["warehouse_id"], header["count_date"], ex=tx) \
                    if any(not c["delete"] for c in changes) else {}
                applied = [r for r in (self._apply_line(tx, count_id, c, book) for c in changes) if r is not None]
                after = self.repository.get_count(count_id, ex=tx)
                if before is None or applied or any(before[k] != after[k] for k in FIELDS):
                    self._record(tx, "create" if before is None else "update", count_id, before, after, applied)
                if post:
                    self._post(tx, count_id, after)
        except psycopg.errors.UniqueViolation as exc:
            raise ConcurrentEditError("صنف في الجرد اتعدّ من جهاز تاني في نفس اللحظة — دوس «تحديث» وشوف العدّ "
                                      "الجديد.") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(count_id)

    def post(self, count_id: int, expected_version: int | None = None) -> str:
        """ترحيل مسودة محفوظة ← بيرجّع الرقم الرسمي."""
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(count_id), expected_version, draft_only=True)
                after = self._post(tx, int(count_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["count_no"]

    def unpost(self, count_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل (بالصلاحية بس): الحركات والقيد بيتمسحوا والجرد بيرجع مسودة برقمه."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(count_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=int(count_id))
                self.ledger.remove(tx, doc_type=STOCK_DOC, source_id=int(count_id),
                                   allow_negative=SESSION.can(f"{PERM}.negative"))
                updated = self.repository.set_status(int(count_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=count_id)
                self.repository.clear_posted_values(int(count_id), tx)
                self._record(tx, "unpost", int(count_id), before, self.repository.get_count(int(count_id), ex=tx))
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_count(self, count_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(count_id), expected_version, draft_only=True)
                if before["count_no"]:
                    raise BusinessError(f"الجرد {before['count_no']} خد رقم رسمي قبل كده — مايتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).")
                old = self.repository.lines(int(count_id), ex=tx)
                deleted = self.repository.delete_count(int(count_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=count_id)
                self._record(tx, "delete", int(count_id), before, None, [(l, None) for l in old])
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def entry_for(self, count_id: int) -> dict[str, Any] | None:
        """قيد الجرد المرحّل بسطوره."""
        return self.posting.entry_for(DOC_TYPE, int(count_id))

    # ---- إكسل: نموذج العدّ + استيراد الفعلي ------------------------------------------------
    def export_sheet(self, path: str | Path, rows: list[dict[str, Any]], blind: bool = False) -> int:
        """ورقة العدّ: الكود · الاسم · النوع · الوحدة · (الدفتري) · الفعلي ← عدد السطور. ``blind`` (أو من غير صلاحية
        «إظهار الدفتري») = من غير عمود الدفتري. نفس الملف بيتملى ويتستورد تاني."""
        PermissionService.require(f"{PERM}.export")
        from openpyxl import Workbook  # noqa: PLC0415
        from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415

        with_book = not blind and self.can_show_book()
        heads = [c for c in SHEET_COLUMNS if with_book or c != "الدفتري"]
        wb = Workbook()
        ws = wb.active
        ws.title = "ورقة العدّ"
        ws.sheet_view.rightToLeft = True
        ws.append(heads)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E79")
            cell.alignment = Alignment(horizontal="center")
        for r in rows:
            values = [r["item_code"], r["item_name"], KINDS[r["stock_kind"]], r.get("unit_name") or ""]
            if with_book:
                values.append(float(r["book_qty"]) if r.get("book_qty") is not None else None)
            values.append(float(r["counted_qty"]) if r.get("counted_qty") is not None else None)
            ws.append(values)
        for col, width in zip("ABCDEF", (14, 40, 12, 12, 14, 14), strict=True):
            ws.column_dimensions[col].width = width
        info = wb.create_sheet("طريقة الملء")
        info.sheet_view.rightToLeft = True
        info.append(["اكتب الكمية اللي اتعدّت في عمود «الفعلي» بالوحدة الأساسية · الفاضي = مااتعدّش · "
                     "النوع: فاتورة / بيان (الفاضي = فاتورة) · تقدر تزوّد صفوف بكود صنف مش في الورقة."])
        info.column_dimensions["A"].width = 110
        wb.save(Path(path))
        return len(rows)

    def read_sheet(self, path: str | Path) -> list[dict[str, Any]]:
        """يقرا ورقة العدّ ← صفوف (رقم الصف + الكود + النوع + الفعلي زي ما هم) — أول صف فيه العناوين."""
        PermissionService.require(f"{PERM}.import")
        from openpyxl import load_workbook  # noqa: PLC0415

        try:
            wb = load_workbook(Path(path), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - ملف بايظ / مش إكسل
            raise BusinessError(f"الملف ده مش ملف إكسل سليم ({type(exc).__name__}).") from None
        try:
            rows = wb.worksheets[0].iter_rows(values_only=True)
            head = next(rows, None)
            if not head:
                raise BusinessError("الملف فاضي.")
            cols: dict[str, int] = {}
            for i, title in enumerate(head):
                t = _key(title)
                for field, words in _HEADER_WORDS.items():
                    if field not in cols and t in {_key(w) for w in words}:
                        cols[field] = i
            missing = [name for field, name in (("code", "كود الصنف"), ("counted", "الفعلي")) if field not in cols]
            if missing:
                raise BusinessError("الملف ناقصه عمود: " + " · ".join(f"«{m}»" for m in missing)
                                    + " — استعمل «نموذج العدّ».")
            out: list[dict[str, Any]] = []
            for n, values in enumerate(rows, start=2):
                def cell(field: str) -> Any:
                    i = cols.get(field)
                    return values[i] if i is not None and i < len(values) else None
                if all(v in (None, "") for v in values):
                    continue
                out.append({"row": n, "code": cell("code"), "kind": cell("kind"), "counted": cell("counted")})
                if len(out) > MAX_IMPORT:
                    raise BusinessError(f"الملف فيه أكتر من {MAX_IMPORT:,} صف.")
            return out
        finally:
            wb.close()

    def match_sheet(self, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """صفوف الملف ← (السطور السليمة بالصنف والفعلي، الأغلاط) · الصف اللي «الفعلي» فيه فاضي بيتساب (مااتعدّش)."""
        items = self.repository.items_by_code()
        good: list[dict[str, Any]] = []
        errors: list[str] = []
        seen: dict[tuple[int, str], int] = {}
        for r in rows:
            where = f"صف {r['row']}"
            if r.get("counted") in (None, ""):
                continue
            code = str(r.get("code") if r.get("code") is not None else "").strip().upper()
            item = items.get(code)
            if not code:
                errors.append(f"{where}: كود الصنف فاضي.")
                continue
            if item is None:
                errors.append(f"{where}: الكود «{code}» مش موجود (أو صنف خدمي).")
                continue
            kind = KIND_WORDS.get(_key(r.get("kind")))
            if kind is None:
                errors.append(f"{where}: النوع «{r.get('kind')}» لازم «فاتورة» أو «بيان».")
                continue
            try:
                counted = parse_counted(r["counted"] if not isinstance(r["counted"], float)
                                        else format(Decimal(str(r["counted"])), "f"), f"{where} — الفعلي")
            except BusinessError as exc:
                errors.append(str(exc))
                continue
            key = (int(item["id"]), kind)
            if key in seen:
                errors.append(f"{where}: الصنف «{code}» ({KINDS[kind]}) متكرر في الملف (صف {seen[key]}).")
                continue
            seen[key] = r["row"]
            good.append({"item_id": int(item["id"]), "item_code": item["code"], "item_name": item["name"],
                         "stock_kind": kind, "counted_qty": counted})
        return good, errors
