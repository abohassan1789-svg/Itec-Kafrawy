"""«بضاعة أول المدة» (المرحلة 6 — النموذج 4 «زي الإكسل»، اختيار المستخدم 2026-11-23 · سكريبت 0045).

ردود المستخدم (2026-11-23):
* **مستند واحد** بتاريخ «الأرصدة الافتتاحية» — **بيترحّل معاها** (مفيش ترحيل لوحده): ترحيلها بيدخّل السطور
  «دفتر المخزون» وقيمتها على حساب المخزون (من نوع الصنف ← الافتراضي 1105) والفرق على 34.
* السطر = **صنف + مخزن + فاتورة/بيان** (مايتكررش) بالرصيد (بالوحدة الأساسية) والسعر · **السعر صفر مسموح بتحذير**
  · المخزن الوهمي كمية بس · الصنف الخدمي مالوش رصيد · بعد ترحيل الأرصدة الافتتاحية كله مقفول.
* بيتكتب من الشاشة (جدول كل الأصناف) **أو** من شاشة الأصناف (``set_item_line``) — الاتنين بيعدّلوا نفس السطر.
* **الاستيراد من إكسل**: كود الصنف · المخزن (كود أو اسم) · فاتورة/بيان · الرصيد · السعر — المراجعة بتعلّم الغلط
  (كود مش موجود — مايتعملش صنف · مخزن مش موجود · خدمي · رصيد ≤ 0 · سعر ناقص · **المتكرر في الملف = غلط**)
  و«إضافة وتحديث» أو «استبدال الكل» في transaction واحدة.

**جهازين (10–20 جهاز):** كل سطر بيتحفظ لوحده بالإصدار اللي الشاشة شافته (``line_id`` + ``row_version``) ←
جهازين بيكتبوا أصناف مختلفة الاتنين بيتحفظوا، ونفس السطر التاني بيترفض بتعارض.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.opening_balance_repository import OpeningBalanceRepository
from app.repositories.opening_stock_repository import OpeningStockRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "inv.opening_stock_lines", "بضاعة أول المدة"
PERM = "inv.opening_stock"
KINDS = {"invoice": "فاتورة", "statement": "بيان"}
KIND_WORDS = {"فاتورة": "invoice", "فاتوره": "invoice", "invoice": "invoice", "ف": "invoice",
              "بيان": "statement", "statement": "statement", "ب": "statement", "": "invoice"}
POSTED = "الأرصدة الافتتاحية مترحّلة — بضاعة أول المدة مقفولة (الغي ترحيل الأرصدة الافتتاحية الأول)."
QTY_STEP = Decimal("0.000001")
MAX_QTY = Decimal("999999999999")
MAX_COST = Decimal("999999999999")
MAX_IMPORT = 30000
ZERO = Decimal("0")
CENT = Decimal("0.01")
# أعمدة ملف الإكسل (الاستيراد = نفس التصدير = النموذج الفاضي)
EXCEL_COLUMNS = ("كود الصنف", "اسم الصنف", "المخزن", "فاتورة / بيان", "الرصيد", "السعر")
_HEADER_WORDS = {
    "code": ("كود الصنف", "الكود", "كود", "code", "item code"),
    "name": ("اسم الصنف", "الصنف", "الاسم", "name", "item"),
    "warehouse": ("المخزن", "مخزن", "warehouse"),
    "kind": ("فاتورة / بيان", "فاتورة/بيان", "النوع", "kind"),
    "qty": ("الرصيد", "رصيد أول المدة", "الكمية", "qty", "quantity"),
    "cost": ("السعر", "سعر الوحدة", "التكلفة", "cost", "price"),
}


def _number(value: Any, label: str) -> Decimal | None:
    """الفاضي ← None · «1,250.5» / «١٢٥» ← رقم · مش رقم ← BusinessError."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = Decimal(str(value))
    else:
        text = str(value).strip().replace(",", "").replace("٬", "").replace("٫", ".")
        text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        if text == "":
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise BusinessError(f"{label} «{value}» مش رقم.") from None
    if not number.is_finite():
        raise BusinessError(f"{label} «{value}» مش رقم.")
    return number


def parse_qty(value: Any, label: str = "الرصيد") -> Decimal | None:
    """الرصيد: الفاضي / الصفر ← None (= مالوش رصيد) · > 0 بـ 6 كسور بالكتير."""
    qty = _number(value, label)
    if qty is None or qty == 0:
        return None
    if qty < 0:
        raise BusinessError(f"{label} مينفعش يبقى بالسالب.")
    if qty != qty.quantize(QTY_STEP):
        raise BusinessError(f"{label} فيه كسور أكتر من 6 أرقام.")
    if qty > MAX_QTY:
        raise BusinessError(f"{label} أكبر من المسموح.")
    return qty.quantize(QTY_STEP)


def parse_cost(value: Any, label: str = "السعر") -> Decimal:
    """السعر: الفاضي ← صفر (مسموح بتحذير) · ≥ 0."""
    cost = _number(value, label)
    if cost is None:
        return ZERO
    if cost < 0:
        raise BusinessError(f"{label} مينفعش يبقى بالسالب.")
    if cost > MAX_COST:
        raise BusinessError(f"{label} أكبر من المسموح.")
    return cost.quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def line_value(qty: Any, cost: Any, is_virtual: bool = False) -> Decimal:
    if is_virtual or qty in (None, "") or cost in (None, ""):
        return ZERO
    return (Decimal(qty) * Decimal(cost)).quantize(CENT, rounding=ROUND_HALF_UP)


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def _key(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


class OpeningStockService:
    def __init__(self, db: Database | None = None, repository: OpeningStockRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or OpeningStockRepository(self.db)
        self.balances = OpeningBalanceRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------------
    def status(self, ex: Executor | None = None) -> dict[str, Any]:
        """حالة «الأرصدة الافتتاحية» (مسودة / مرحّلة) وتاريخها — بضاعة أول المدة بتمشي معاها."""
        header = self.balances.header(ex=ex) or {}
        return {"status": header.get("status", "draft"), "balance_date": header.get("balance_date"),
                "locked": header.get("status") == "posted"}

    def warehouses(self, active_only: bool = False) -> list[dict[str, Any]]:
        rows = self.repository.warehouses()
        return [w for w in rows if w["is_active"]] if active_only else rows

    def default_warehouse_id(self) -> int | None:
        rows = self.repository.warehouses()
        main = next((w for w in rows if w["is_default"] and w["is_active"]), None) \
            or next((w for w in rows if w["is_active"] and not w["is_virtual"]), None)
        return int(main["id"]) if main else None

    def grid(self, warehouse_id: int, stock_kind: str = "invoice") -> list[dict[str, Any]]:
        """كل الأصناف المخزنية في المخزن والنوع دول — مع كل صنف رصيده وسعره وقيمته (الفاضي = مالوش رصيد)."""
        if stock_kind not in KINDS:
            raise BusinessError("النوع لازم «فاتورة» أو «بيان».")
        wh = next((w for w in self.repository.warehouses() if w["id"] == int(warehouse_id)), None)
        virtual = bool(wh and wh["is_virtual"])
        rows = self.repository.grid(int(warehouse_id), stock_kind)
        for r in rows:
            r["warehouse_id"], r["stock_kind"], r["is_virtual"] = int(warehouse_id), stock_kind, virtual
            r["value"] = line_value(r["qty"], r["unit_cost"], virtual)
        return rows

    def lines(self, item_id: int | None = None) -> list[dict[str, Any]]:
        return self.repository.lines(item_id=item_id)

    def summary(self) -> dict[str, Any]:
        """الكروت: أصناف عليها رصيد · قيمة فاتورة · بيان · الإجمالي (← حساب المخزون) + حالة الأرصدة الافتتاحية."""
        s = dict(self.repository.summary())
        s.update(self.status())
        s["inventory_account"] = self.repository.inventory_account()
        return s

    def item_line(self, item_id: int | None, warehouse_id: int | None = None,
                  stock_kind: str = "invoice") -> dict[str, Any]:
        """لشاشة الأصناف: سطر (الصنف + المخزن + النوع) + عدد سطور الصنف التانية + الحالة."""
        info = self.status()
        info.update({"line": None, "others": 0, "warehouse_id": warehouse_id, "stock_kind": stock_kind})
        if not item_id:
            return info
        lines = self.repository.lines(item_id=int(item_id))
        if warehouse_id is None:
            item = self.repository.item(int(item_id)) or {}
            warehouse_id = item.get("default_warehouse_id") or self.default_warehouse_id()
            if lines and not any(l["warehouse_id"] == warehouse_id and l["stock_kind"] == stock_kind for l in lines):
                warehouse_id, stock_kind = lines[0]["warehouse_id"], lines[0]["stock_kind"]
        line = next((l for l in lines if l["warehouse_id"] == warehouse_id and l["stock_kind"] == stock_kind), None)
        info.update({"line": line, "others": len(lines) - (1 if line else 0),
                     "warehouse_id": warehouse_id, "stock_kind": stock_kind})
        return info

    # ---- مساعدات ------------------------------------------------------------------
    def _check_open(self, tx: Executor) -> dict[str, Any]:
        header = self.balances.header(ex=tx) or {}
        if header.get("status") == "posted":
            raise BusinessError(POSTED)
        return header

    @staticmethod
    def _label(row: dict[str, Any]) -> str:
        return (f"{row.get('item_code', '')} «{row.get('item_name', '')}» — {row.get('warehouse_name', '')} "
                f"({KINDS.get(row.get('stock_kind'), '')})")

    def _record(self, tx: Executor, action: str, line_id: int, before: dict | None, after: dict | None) -> None:
        row = after or before or {}
        keep = ("item_id", "warehouse_id", "stock_kind", "qty", "unit_cost", "row_version")
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=line_id, entity_label=ENTITY_LABEL,
                          record_label=self._label(row)[:200],
                          before={k: before.get(k) for k in keep} if before else None,
                          after={k: after.get(k) for k in keep} if after else None)

    def _conflict(self, tx: Executor, line_id: int | None, label: str, deleted: bool | None = None
                  ) -> ConcurrentEditError:
        if line_id:
            base = self.guard.conflict(tx, ENTITY, line_id, deleted=deleted)
            text = str(base)
        else:
            base, text = None, "الرصيد ده اتسجل من جهاز تاني بعد ما فتحت الشاشة — دوس «تحديث» وعدّل تاني."
        return ConcurrentEditError(f"رصيد {label}:\n{text}", deleted=bool(base and base.deleted),
                                   changed_by=getattr(base, "changed_by", None),
                                   changed_at=getattr(base, "changed_at", None), device=getattr(base, "device", None))

    def _apply(self, tx: Executor, change: dict[str, Any], n: int) -> bool:
        """تغيير سطر واحد بالإصدار اللي الشاشة شافته ← ``True`` لو حاجة اتغيّرت."""
        label = change.get("label") or f"السطر {n}"
        try:
            item_id, warehouse_id = int(change["item_id"]), int(change["warehouse_id"])
        except (KeyError, TypeError, ValueError):
            raise BusinessError(f"{label}: الصنف والمخزن مطلوبين.") from None
        kind = str(change.get("stock_kind") or "invoice")
        if kind not in KINDS:
            raise BusinessError(f"{label}: النوع لازم «فاتورة» أو «بيان».")
        qty = parse_qty(change.get("qty"), f"رصيد {label}")
        cost = parse_cost(change.get("unit_cost"), f"سعر {label}")
        line_id = int(change["line_id"]) if change.get("line_id") else None
        version = change.get("row_version")
        if line_id:
            current = self.repository.line(line_id, tx, for_update=True)
            if current is None:
                raise self._conflict(tx, line_id, label, deleted=True)
            if (current["item_id"], current["warehouse_id"], current["stock_kind"]) != (item_id, warehouse_id, kind):
                raise BusinessError(f"{label}: السطر ده مش بتاع الصنف والمخزن دول — دوس «تحديث».")
            if version is not None and int(version) != int(current["row_version"]):
                raise self._conflict(tx, line_id, label, deleted=False)
            if qty is None:
                self.repository.delete(line_id, tx, expected_version=current["row_version"])
                self._record(tx, "delete", line_id, current, None)
                return True
            if current["is_virtual"]:
                cost = ZERO
            if (Decimal(current["qty"]), Decimal(current["unit_cost"])) == (qty, cost):
                return False
            if not self.repository.update(line_id, qty, cost, SESSION.user_id, tx,
                                          expected_version=current["row_version"]):
                raise self._conflict(tx, line_id, label)
            self._record(tx, "update", line_id, current, self.repository.line(line_id, tx))
            return True
        existing = self.repository.find(item_id, warehouse_id, kind, tx, for_update=True)
        if existing is not None:
            if version is None and qty is not None:            # من غير تحقق (أوامر داخلية)
                return self._apply(tx, {**change, "line_id": existing["id"]}, n)
            raise self._conflict(tx, int(existing["id"]), label, deleted=False)
        if qty is None:
            return False
        new_id = self.repository.insert({"item_id": item_id, "warehouse_id": warehouse_id, "stock_kind": kind,
                                         "qty": qty, "unit_cost": cost}, SESSION.user_id, tx)
        self._record(tx, "create", new_id, None, self.repository.line(new_id, tx))
        return True

    # ---- الحفظ ----------------------------------------------------------------------
    def save(self, changes: list[dict[str, Any]]) -> int:
        """يحفظ السطور اللي اتغيّرت بس ← عددها.

        كل تغيير: ``item_id`` · ``warehouse_id`` · ``stock_kind`` · ``qty`` (الفاضي / الصفر = امسح السطر) ·
        ``unit_cost`` · ``line_id`` + ``row_version`` (اللي الشاشة شافته — من غير سطر = الشاشة شافته فاضي) · ``label``.
        """
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if not changes:
            return 0
        try:
            with self.db.atomic() as tx:
                self._check_open(tx)
                return sum(1 for n, change in enumerate(changes, start=1) if self._apply(tx, change, n))
        except psycopg.errors.UniqueViolation as exc:
            raise ConcurrentEditError("رصيد صنف اتسجل من جهاز تاني في نفس اللحظة (مايتكررش) — "
                                      "دوس «تحديث» وعدّل تاني.") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def set_item_line(self, tx: Executor, item_id: int, warehouse_id: int | None, stock_kind: str, qty: Any,
                      unit_cost: Any, line_id: int | None = None, row_version: int | None = None,
                      label: str = "") -> bool:
        """من شاشة الأصناف (جوه transaction حفظ الصنف): نفس سطر (الصنف + المخزن + النوع) ← ``True`` لو اتغيّر.

        ``line_id`` / ``row_version`` = السطر اللي الشاشة شافته (``row_version=0`` = شافته فاضي) · ``None`` = من غير تحقق.
        """
        qty_value = parse_qty(qty, f"رصيد أول المدة ({label or 'الصنف'})")
        if qty_value is None and not line_id:
            return False                                  # الشاشة شافته فاضي وفضل فاضي
        if not warehouse_id:
            raise BusinessError("اختار مخزن رصيد أول المدة.")
        if stock_kind not in KINDS:
            raise BusinessError("نوع رصيد أول المدة لازم «فاتورة» أو «بيان».")
        try:
            header = self.balances.header(ex=tx) or {}
            if header.get("status") == "posted":
                current = (self.repository.line(int(line_id), tx) if line_id else
                           self.repository.find(int(item_id), int(warehouse_id), stock_kind, tx))
                same = (current is None and qty_value is None) or (
                    current is not None and qty_value is not None and Decimal(current["qty"]) == qty_value
                    and (current["is_virtual"] or Decimal(current["unit_cost"]) == parse_cost(unit_cost)))
                if same:
                    return False
                raise BusinessError(POSTED)
            if line_id:
                version = None if row_version is None else int(row_version)
            else:
                version = 0 if row_version == 0 else None
            return self._apply(tx, {"item_id": item_id, "warehouse_id": warehouse_id, "stock_kind": stock_kind,
                                    "qty": qty, "unit_cost": unit_cost, "line_id": line_id or None,
                                    "row_version": version, "label": label or "الصنف"}, 1)
        except psycopg.errors.UniqueViolation as exc:
            raise ConcurrentEditError("رصيد أول المدة ده اتسجل من جهاز تاني في نفس اللحظة — دوس «تحديث».") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    # ---- الإكسل -------------------------------------------------------------------
    def read_excel(self, path: str | Path) -> list[dict[str, Any]]:
        """يقرا الملف ← صفوف (رقم الصف في الإكسل + القيم زي ما هي) — أول صف فيه العناوين."""
        PermissionService.require(f"{PERM}.import")
        from openpyxl import load_workbook  # noqa: PLC0415

        try:
            wb = load_workbook(Path(path), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - ملف بايظ / مش إكسل
            raise BusinessError(f"الملف ده مش ملف إكسل سليم ({type(exc).__name__}).") from None
        try:
            ws = wb.worksheets[0]
            rows = ws.iter_rows(values_only=True)
            head = next(rows, None)
            if not head:
                raise BusinessError("الملف فاضي.")
            cols: dict[str, int] = {}
            for i, title in enumerate(head):
                t = _key(title)
                for field, words in _HEADER_WORDS.items():
                    if field not in cols and t in {_key(w) for w in words}:
                        cols[field] = i
            missing = [EXCEL_COLUMNS[i] for i, f in ((0, "code"), (2, "warehouse"), (4, "qty")) if f not in cols]
            if missing:
                raise BusinessError("الملف ناقصه عمود: " + " · ".join(f"«{m}»" for m in missing)
                                    + " — استعمل «نموذج فاضي».")
            out: list[dict[str, Any]] = []
            for n, values in enumerate(rows, start=2):
                def cell(field: str) -> Any:
                    i = cols.get(field)
                    return values[i] if i is not None and i < len(values) else None
                if all(v in (None, "") for v in values):
                    continue
                out.append({"row": n, "code": cell("code"), "name": cell("name"), "warehouse": cell("warehouse"),
                            "kind": cell("kind"), "qty": cell("qty"), "cost": cell("cost")})
                if len(out) > MAX_IMPORT:
                    raise BusinessError(f"الملف فيه أكتر من {MAX_IMPORT:,} صف.")
            return out
        finally:
            wb.close()

    def review(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """المراجعة قبل الاستيراد: لكل صف الصنف والمخزن والنوع والرصيد والسعر + ``errors`` (الأحمر) و``warnings``."""
        items = self.repository.items_by_code()
        whs = self.repository.warehouses()
        by_code = {_key(w["code"]): w for w in whs}
        by_name = {_key(w["name_ar"]): w for w in whs}
        seen: dict[tuple[int, int, str], int] = {}
        out: list[dict[str, Any]] = []
        for r in rows:
            errors: list[str] = []
            warnings: list[str] = []
            code = " ".join(str(r.get("code") if r.get("code") is not None else "").split())
            if isinstance(r.get("code"), float) and r["code"].is_integer():
                code = str(int(r["code"]))
            item = items.get(code.upper()) if code else None
            if not code:
                errors.append("كود الصنف فاضي")
            elif item is None:
                errors.append(f"الكود «{code}» مش موجود")
            elif item["is_service"]:
                errors.append("الصنف خدمي")
            wh_text = r.get("warehouse")
            if isinstance(wh_text, float) and wh_text.is_integer():
                wh_text = int(wh_text)
            wh = by_code.get(_key(wh_text)) or by_name.get(_key(wh_text)) if wh_text not in (None, "") else None
            if wh_text in (None, ""):
                errors.append("المخزن فاضي")
            elif wh is None:
                errors.append(f"المخزن «{wh_text}» مش موجود")
            elif not wh["is_active"]:
                warnings.append("المخزن موقوف")
            kind = KIND_WORDS.get(_key(r.get("kind")))
            if kind is None:
                errors.append(f"النوع «{r.get('kind')}» لازم فاتورة أو بيان")
            qty = cost = None
            try:
                raw_qty = _number(r.get("qty"), "الرصيد")
                if raw_qty is None or raw_qty <= 0:
                    errors.append("الرصيد لازم أكبر من صفر")
                else:
                    qty = parse_qty(raw_qty)
            except BusinessError as exc:
                errors.append(str(exc))
            virtual = bool(wh and wh["is_virtual"])
            try:
                raw_cost = _number(r.get("cost"), "السعر")
                if raw_cost is None and not virtual:
                    errors.append("السعر ناقص")
                else:
                    cost = ZERO if virtual else parse_cost(raw_cost)
                    if cost == 0 and not virtual:
                        warnings.append("السعر صفر")
            except BusinessError as exc:
                errors.append(str(exc))
            if item and wh and kind:
                key = (int(item["id"]), int(wh["id"]), kind)
                if key in seen:
                    errors.append(f"متكرر في الملف (الصف {seen[key]})")
                else:
                    seen[key] = r["row"]
            out.append({"row": r["row"], "code": code, "item_id": item["id"] if item else None,
                        "item_name": item["name"] if item else (r.get("name") or ""),
                        "warehouse_id": wh["id"] if wh else None,
                        "warehouse_name": wh["name_ar"] if wh else (str(wh_text) if wh_text is not None else ""),
                        "stock_kind": kind, "qty": qty, "unit_cost": cost, "is_virtual": virtual,
                        "value": line_value(qty, cost, virtual) if qty is not None and cost is not None else ZERO,
                        "errors": errors, "warnings": warnings})
        return out

    def import_rows(self, reviewed: list[dict[str, Any]], replace: bool = False) -> dict[str, int]:
        """«إضافة وتحديث» (الموجود يتعدّل والجديد يتضاف) أو «استبدال الكل» — transaction واحدة.

        أي صف فيه غلط ← مفيش حاجة بتتحفظ. ← عدد اللي اتضاف / اتعدّل / اتمسح.
        """
        PermissionService.require(f"{PERM}.import")
        PermissionService.require(f"{PERM}.save")
        bad = [r for r in reviewed if r.get("errors")]
        if bad:
            raise BusinessError(f"الملف فيه {len(bad)} صف غلط (بالأحمر) — صلّحهم في الإكسل واستورد تاني.")
        if not reviewed:
            raise BusinessError("الملف مافيهوش ولا صف.")
        counts = {"created": 0, "updated": 0, "deleted": 0}
        try:
            with self.db.atomic() as tx:
                self._check_open(tx)
                before = self.repository.lines(ex=tx)
                if replace:
                    counts["deleted"] = self.repository.delete_all(tx)
                for r in reviewed:
                    current = None if replace else self.repository.find(
                        int(r["item_id"]), int(r["warehouse_id"]), r["stock_kind"], tx, for_update=True)
                    if current is None:
                        self.repository.insert({"item_id": r["item_id"], "warehouse_id": r["warehouse_id"],
                                                "stock_kind": r["stock_kind"], "qty": r["qty"],
                                                "unit_cost": r["unit_cost"]}, SESSION.user_id, tx)
                        counts["created"] += 1
                    elif (Decimal(current["qty"]), Decimal(current["unit_cost"])) != (r["qty"], r["unit_cost"]):
                        self.repository.update(int(current["id"]), r["qty"], r["unit_cost"], SESSION.user_id, tx)
                        counts["updated"] += 1
                keep = ("item_code", "warehouse_code", "stock_kind", "qty", "unit_cost")
                self.audit.record(tx, action="update", entity=ENTITY, entity_id=0, entity_label=ENTITY_LABEL,
                                  record_label=f"استيراد من إكسل ({'استبدال الكل' if replace else 'إضافة وتحديث'})",
                                  lines={ENTITY: {"before": [{k: b.get(k) for k in keep} for b in before],
                                                  "after": [{k: a.get(k) for k in keep}
                                                            for a in self.repository.lines(ex=tx)]}},
                                  extra={"import": "replace" if replace else "merge", **counts})
        except psycopg.errors.UniqueViolation as exc:
            raise ConcurrentEditError("رصيد صنف اتسجل من جهاز تاني في نفس اللحظة — استورد تاني.") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return counts

    def write_template(self, path: str | Path) -> None:
        """نموذج إكسل فاضي بالأعمدة + صف مثال من أول صنف ومخزن."""
        PermissionService.require(f"{PERM}.import")
        whs = self.warehouses(active_only=True)
        sample = next(iter(sorted(self.repository.items_by_code().values(), key=lambda i: str(i["code"]))), None)
        rows = []
        if sample and whs:
            rows.append([sample["code"], sample["name"], whs[0]["name_ar"], "فاتورة", 10, 100])
        self._write(path, rows, note="المخزن: الكود أو الاسم · فاتورة / بيان (الفاضي = فاتورة) · "
                                     "الرصيد بالوحدة الأساسية · السعر لوحدة واحدة (المخزن الوهمي كمية بس)")

    def export(self, path: str | Path) -> int:
        """كل السطور بنفس أعمدة الاستيراد (ينفع يتعدّل ويتستورد تاني) ← عدد السطور."""
        PermissionService.require(f"{PERM}.export")
        lines = self.repository.lines()
        self._write(path, [[l["item_code"], l["item_name"], l["warehouse_name"], KINDS[l["stock_kind"]],
                            float(l["qty"]), float(l["unit_cost"])] for l in lines])
        return len(lines)

    @staticmethod
    def _write(path: str | Path, rows: list[list[Any]], note: str = "") -> None:
        from openpyxl import Workbook  # noqa: PLC0415
        from openpyxl.styles import Alignment, Font, PatternFill  # noqa: PLC0415

        wb = Workbook()
        ws = wb.active
        ws.title = ENTITY_LABEL
        ws.sheet_view.rightToLeft = True
        ws.append(list(EXCEL_COLUMNS))
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E79")
            cell.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append(r)
        for col, width in zip("ABCDEF", (14, 34, 20, 14, 14, 14), strict=True):
            ws.column_dimensions[col].width = width
        if note:
            info = wb.create_sheet("طريقة الملء")
            info.sheet_view.rightToLeft = True
            info.append([note])
            info.column_dimensions["A"].width = 110
        wb.save(Path(path))

