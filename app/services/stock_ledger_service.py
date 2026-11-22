"""«دفتر المخزون» — محرك حركة المخزون (المرحلة 6 · من غير شاشة · سكريبت 0043).

كل مستند بيحرّك مخزون (بضاعة أول المدة · فاتورة / مردود شراء · إذن صرف / مردود بيع · تحويل · تسوية جرد)
**مابيحسبش تكلفة بنفسه**: بيبعت سطور ``StockLine`` (الصنف · المخزن · فاتورة/بيان · الكمية بالوحدة الأساسية
+ داخل / − خارج · وتكلفة الوحدة للداخل)، والمحرك جوه transaction المستند:

1. بيقفل صفوف الأصناف (``FOR NO KEY UPDATE`` بالترتيب) — جهازين بيبيعوا نفس الصنف بيتصفّوا ورا بعض.
2. بيمسح حركات المستند القديمة (لو بيتعدّل) ويسجّل الجديدة.
3. **بيعيد حساب** الصنف من أقدم يوم اتأثر لحد آخر حركة (حركة بتاريخ قديم بتغيّر متوسط اللي بعدها):
   * **متوسط مرجّح مستمر على مستوى الشركة لكل (صنف + فاتورة/بيان)** — الداخل بتكلفته بيغيّر المتوسط، والخارج
     بيطلع بالمتوسط ومابيغيّروش · التحويل: الخارج بالمتوسط والداخل **بنفس القيمة** (لو من نوع لنوع بيدخل في
     متوسط النوع التاني).
   * ترتيب نفس اليوم: **الوارد بتكلفة ← التحويلات ← الصادر** (وجوه كل مجموعة بالترتيب اللي اتسجلت بيه).
   * آخر خروج بيصفّر الكمية بياخد **كل القيمة الباقية** (مفيش قروش معلّقة في المخزون).
   * **الرصيد السالب ممنوع** في أي مخزن، آخر أي يوم (مش النهارده بس) ← ``BusinessError`` بالعربي والمستند كله بيرجع.
   * **المخزن الوهمي** كميات بس: من غير تكلفة ولا قيمة ولا متوسط.
4. بيرجّع لكل سطر **تكلفته وقيمته** (المستند بيعمل بيهم سطور «المخزون» و«تكلفة المبيعات» في محرك الترحيل).
   القيمة اللي اتقيّدت بتتحفظ (``booked_value``)، ولو إعادة الحساب غيّرت قيمة حركة قديمة، الفرق بيبان في
   ``cost_differences`` (بند «فروق التكلفة» هيقيّده).

الصلاحيات بتاعة المستند نفسه (المحرك بيتنادى من Service المستند بعد ما يتأكد منها) · سجل التعديلات بتاع المستند.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.stock_ledger_repository import StockLedgerRepository
from app.security.session import SESSION
from app.services.errors import BusinessError

KINDS = {"invoice": "فاتورة", "statement": "بيان"}
CENT = Decimal("0.01")
QTY_STEP = Decimal("0.000001")
COST_STEP = Decimal("0.000001")
MAX_QTY = Decimal("999999999999")
MAX_COST = Decimal("999999999999")
MAX_LINES = 30000
ZERO = Decimal("0")


@dataclass(frozen=True)
class StockLine:
    """سطر حركة من المستند: الكمية **بالوحدة الأساسية** (+ داخل · − خارج)."""

    item_id: int
    warehouse_id: int
    qty: Any
    stock_kind: str = "invoice"
    unit_cost: Any = None            # الداخل بس (شراء · أول المدة · مردود بيع بتكلفته الأصلية · زيادة جرد)
    transfer_of: int | None = None   # الداخل في التحويل ← رقم سطر الخارج (من 0) في نفس المستند


def item_code(seq: int) -> str:
    return f"ITM-{int(seq):04d}"


def fmt_qty(qty: Decimal) -> str:
    text = f"{qty:,.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, Decimal):
        number = value
    else:
        try:
            number = Decimal(str(value).strip().replace(",", ""))
        except (InvalidOperation, ValueError):
            raise BusinessError(f"{label} «{value}» مش رقم.") from None
    if not number.is_finite():
        raise BusinessError(f"{label} «{value}» مش رقم.")
    return number


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


class StockLedgerService:
    def __init__(self, db: Database | None = None, repository: StockLedgerRepository | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or StockLedgerRepository(self.db)

    # ---- التحقق من السطور -----------------------------------------------------------
    def _clean(self, tx: Executor, lines: list[StockLine], items: dict[int, dict]) -> list[dict[str, Any]]:
        warehouses = self.repository.warehouses([int(l.warehouse_id) for l in lines], ex=tx)
        clean: list[dict[str, Any]] = []
        for n, line in enumerate(lines, start=1):
            where = f"سطر {n}"
            item = items.get(int(line.item_id))
            if item is None:
                raise BusinessError(f"{where}: الصنف مش موجود (ممكن يكون اتمسح).")
            label = f"{where} ({item_code(item['seq'])} {item['name']})"
            if item["is_service"]:
                raise BusinessError(f"{label}: الصنف خدمي — مالوش حركة مخزون.")
            wh = warehouses.get(int(line.warehouse_id))
            if wh is None:
                raise BusinessError(f"{label}: المخزن مش موجود (ممكن يكون اتمسح).")
            kind = str(line.stock_kind or "")
            if kind not in KINDS:
                raise BusinessError(f"{label}: نوع المخزون لازم «فاتورة» أو «بيان».")
            qty = _decimal(line.qty, "الكمية")
            if qty != qty.quantize(QTY_STEP):
                raise BusinessError(f"{label}: الكمية فيها كسور أكتر من 6 أرقام.")
            if qty == 0:
                raise BusinessError(f"{label}: الكمية صفر.")
            if abs(qty) > MAX_QTY:
                raise BusinessError(f"{label}: الكمية أكبر من المسموح.")
            row = {"line": n, "label": label, "item": item, "warehouse": wh, "item_id": int(line.item_id),
                   "warehouse_id": int(line.warehouse_id), "stock_kind": kind, "qty": qty, "pair": None,
                   "is_virtual": bool(wh["is_virtual"]), "given_cost": None}
            if line.transfer_of is not None:
                src = int(line.transfer_of)
                if not 0 <= src < len(lines) or src == n - 1:
                    raise BusinessError(f"{label}: سطر الخارج بتاع التحويل مش موجود.")
                if src >= n - 1:
                    raise BusinessError(f"{label}: سطر الخارج بتاع التحويل لازم ييجي قبل الداخل.")
                out = clean[src]
                if qty <= 0 or out["qty"] != -qty or out["item_id"] != row["item_id"] or out["pair_of"]:
                    raise BusinessError(f"{label}: الداخل في التحويل لازم نفس الصنف والكمية بتاعة سطر الخارج.")
                if out["is_virtual"] != row["is_virtual"]:
                    raise BusinessError(f"{label}: مينفعش تحويل بين مخزن وهمي ومخزن حقيقي.")
                if (out["warehouse_id"], out["stock_kind"]) == (row["warehouse_id"], kind):
                    raise BusinessError(f"{label}: التحويل لنفس المخزن ونفس النوع مالوش معنى.")
                if line.unit_cost not in (None, ""):
                    raise BusinessError(f"{label}: الداخل في التحويل بياخد تكلفة الخارج — ماتكتبلوش تكلفة.")
                row["pair"] = src
                out["pair_of"] = n - 1
            elif qty > 0 and not row["is_virtual"]:
                if line.unit_cost in (None, ""):
                    raise BusinessError(f"{label}: تكلفة الوحدة مطلوبة للداخل.")
                cost = _decimal(line.unit_cost, "تكلفة الوحدة")
                if cost < 0:
                    raise BusinessError(f"{label}: تكلفة الوحدة مينفعش تبقى بالسالب.")
                if cost > MAX_COST:
                    raise BusinessError(f"{label}: تكلفة الوحدة أكبر من المسموح.")
                row["given_cost"] = cost.quantize(COST_STEP, rounding=ROUND_HALF_UP)
            elif qty < 0 and line.unit_cost not in (None, ""):
                raise BusinessError(f"{label}: الخارج بيطلع بمتوسط التكلفة — ماتكتبلوش تكلفة.")
            row["pair_of"] = None
            clean.append(row)
        return clean

    @staticmethod
    def _mode(row: dict[str, Any]) -> str:
        if row["is_virtual"]:
            return "none"
        if row["pair"] is not None:
            return "transfer"
        return "given" if row["qty"] > 0 else "average"

    # ---- الكتابة (جوه transaction المستند) -------------------------------------------
    def post(self, tx: Executor, *, doc_type: str, source_id: int, source_no: str, movement_date: dt.date,
             lines: list[StockLine]) -> list[dict[str, Any]]:
        """يسجّل حركات المستند (أو يبدّل حركاته القديمة) ← لكل سطر: التكلفة والقيمة بعد إعادة الحساب.

        لازم يتنادى جوه ``db.atomic()`` بتاع المستند. أي غلط (رصيد سالب · سنة مقفولة · بيانات ناقصة) ←
        ``BusinessError`` بالعربي والمستند كله بيرجع.
        """
        doc = self.repository.doc_type(str(doc_type), ex=tx)
        if doc is None:
            raise BusinessError(f"نوع المستند «{doc_type}» مالوش حركة مخزون.")
        if isinstance(movement_date, dt.datetime):
            movement_date = movement_date.date()
        if not isinstance(movement_date, dt.date):
            raise BusinessError("تاريخ المستند مطلوب عشان حركة المخزون.")
        source_no = " ".join(str(source_no or "").split())[:40]
        if not source_no:
            raise BusinessError("رقم المستند مطلوب عشان حركة المخزون.")
        lines = list(lines or [])
        if not lines:
            raise BusinessError("المستند مافيهوش أصناف تتحرك في المخزون.")
        if len(lines) > MAX_LINES:
            raise BusinessError("عدد السطور أكبر من المسموح في مستند واحد.")
        try:
            old = self.repository.source_movements(doc["code"], int(source_id), ex=tx)
            items = self.repository.lock_items([int(l.item_id) for l in lines] + [int(m["item_id"]) for m in old], tx)
            clean = self._clean(tx, lines, items)
            start: dict[int, dt.date] = {}
            for m in old:
                start[int(m["item_id"])] = min(start.get(int(m["item_id"]), m["movement_date"]), m["movement_date"])
            if old:
                self.repository.delete_source(doc["code"], int(source_id), ex=tx)
            ids: list[int] = []
            for row in clean:
                pair_id = ids[row["pair"]] if row["pair"] is not None else None
                ids.append(self.repository.insert_movement({
                    "source_type": doc["code"], "source_id": int(source_id), "source_no": source_no,
                    "line_no": row["line"], "movement_date": movement_date, "item_id": row["item_id"],
                    "warehouse_id": row["warehouse_id"], "stock_kind": row["stock_kind"], "qty": row["qty"],
                    "cost_mode": self._mode(row), "given_cost": row["given_cost"], "pair_id": pair_id,
                    "is_virtual": row["is_virtual"], "created_by": SESSION.user_id}, ex=tx))
                start[row["item_id"]] = min(start.get(row["item_id"], movement_date), movement_date)
            for item_id in sorted(start):
                self._replay(tx, items[item_id], start[item_id])
            self.repository.book(ids, ex=tx)
            out = self.repository.source_lines(doc["code"], int(source_id), ex=tx)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        except psycopg.errors.CheckViolation as exc:
            raise BusinessError("الرصيد هيبقى بالسالب — مينفعش (اعمل «تحديث» وجرّب تاني).") from exc
        return out

    def remove(self, tx: Executor, *, doc_type: str, source_id: int) -> bool:
        """يمسح حركات المستند (إلغاء الترحيل / المسح) ويعيد الحساب ← ``False`` لو مالوش حركات.

        لو اللي اتصرف بعده هيخلّي الرصيد بالسالب ← ``BusinessError`` ومابيتمسحش.
        """
        doc = self.repository.doc_type(str(doc_type), ex=tx)
        if doc is None:
            raise BusinessError(f"نوع المستند «{doc_type}» مالوش حركة مخزون.")
        try:
            old = self.repository.source_movements(doc["code"], int(source_id), ex=tx)
            if not old:
                return False
            items = self.repository.lock_items([int(m["item_id"]) for m in old], tx)
            start: dict[int, dt.date] = {}
            for m in old:
                start[int(m["item_id"])] = min(start.get(int(m["item_id"]), m["movement_date"]), m["movement_date"])
            self.repository.delete_source(doc["code"], int(source_id), ex=tx)
            for item_id in sorted(start):
                self._replay(tx, items[item_id], start[item_id])
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        except psycopg.errors.CheckViolation as exc:
            raise BusinessError("الرصيد هيبقى بالسالب — مينفعش (اعمل «تحديث» وجرّب تاني).") from exc
        return True

    # ---- إعادة الحساب -----------------------------------------------------------------
    def _replay(self, tx: Executor, item: dict[str, Any], day: dt.date) -> None:
        item_id = int(item["id"])
        chains: dict[str, dict[str, Decimal]] = {}
        for kind, row in self.repository.chain_state_before(item_id, day, ex=tx).items():
            chains[kind] = {"qty": row["qty"] or ZERO, "value": row["value"] or ZERO, "avg": row["avg"] or ZERO}
        wh_qty = dict(self.repository.warehouse_qty_before(item_id, day, ex=tx))
        movements = self.repository.movements_from(item_id, day, ex=tx)
        if movements:
            names = self.repository.warehouses([int(m["warehouse_id"]) for m in movements], ex=tx)
        by_day: dict[dt.date, list[dict]] = {}
        for m in movements:
            by_day.setdefault(m["movement_date"], []).append(m)
        for current, rows in by_day.items():
            ins = {int(m["pair_id"]): m for m in rows if m["pair_id"] is not None}
            ordered = [m for m in rows if m["qty"] > 0 and m["pair_id"] is None]
            for m in rows:
                if m["qty"] < 0 and int(m["id"]) in ins:
                    ordered += [m, ins[int(m["id"])]]
            ordered += [m for m in rows if m["qty"] < 0 and int(m["id"]) not in ins]
            done: dict[int, dict] = {}
            touched: set[tuple[int, str]] = set()
            for seq, m in enumerate(ordered, start=1):
                computed = self._apply(m, chains, done)
                key = (int(m["warehouse_id"]), m["stock_kind"])
                wh_qty[key] = wh_qty.get(key, ZERO) + m["qty"]
                touched.add(key)
                computed["wh_qty_after"] = wh_qty[key]
                computed["day_seq"] = seq
                done[int(m["id"])] = computed
                if any(computed[c] != m[c] for c in computed):
                    self.repository.save_computed(int(m["id"]), computed, ex=tx)
            for key in sorted(touched):
                if wh_qty[key] < 0:
                    self._negative(item, names[key[0]], key[1], current, wh_qty[key])
        self.repository.refresh_balances(item_id, ex=tx)

    @staticmethod
    def _apply(m: dict[str, Any], chains: dict[str, dict[str, Decimal]], done: dict[int, dict]) -> dict[str, Any]:
        if m["is_virtual"]:
            return {"unit_cost": None, "value": ZERO, "kind_qty_after": None, "kind_value_after": None,
                    "avg_cost_after": None}
        chain = chains.setdefault(m["stock_kind"], {"qty": ZERO, "value": ZERO, "avg": ZERO})
        qty = m["qty"]
        if m["cost_mode"] == "given":
            unit_cost = m["given_cost"]
            value = (qty * unit_cost).quantize(CENT, rounding=ROUND_HALF_UP)
        elif m["cost_mode"] == "transfer":
            source = done[int(m["pair_id"])]
            unit_cost = source["unit_cost"]
            value = -source["value"]
        else:                                                    # خارج بالمتوسط
            unit_cost = chain["avg"]
            if chain["qty"] + qty == 0:
                value = -chain["value"]                          # آخر خروج بياخد الباقي كله
            else:
                value = (qty * unit_cost).quantize(CENT, rounding=ROUND_HALF_UP)
        chain["qty"] += qty
        chain["value"] += value
        if qty > 0 and chain["qty"] > 0:
            chain["avg"] = (chain["value"] / chain["qty"]).quantize(COST_STEP, rounding=ROUND_HALF_UP)
            if chain["avg"] < 0:
                chain["avg"] = ZERO
        return {"unit_cost": unit_cost, "value": value, "kind_qty_after": chain["qty"],
                "kind_value_after": chain["value"], "avg_cost_after": chain["avg"]}

    @staticmethod
    def _negative(item: dict, warehouse: dict, kind: str, day: dt.date, qty: Decimal) -> None:
        raise BusinessError(
            f"الصنف {item_code(item['seq'])} «{item['name']}»: رصيد «{KINDS[kind]}» في المخزن «{warehouse['name_ar']}» "
            f"هيبقى بالسالب آخر يوم {day:%d/%m/%Y} (ناقص {fmt_qty(-qty)} {item['unit_name']}) — "
            "الرصيد السالب ممنوع.")

    # ---- قراية --------------------------------------------------------------------
    def available(self, item_id: int, warehouse_id: int | None = None, stock_kind: str | None = None, *,
                  on_date: dt.date | None = None, ex: Executor | None = None) -> Decimal:
        """الكمية المتاحة. من غير تاريخ = الرصيد دلوقتي (المخزن المحدد أو كل المخازن الحقيقية) ·
        بتاريخ (لمخزن + نوع) = أقل رصيد من اليوم ده لقدّام — اللي ينفع يخرج من غير ما يوم بعده يبقى بالسالب."""
        if on_date is None:
            return self.repository.balance_now(int(item_id), warehouse_id, stock_kind, ex=ex)
        if warehouse_id is None or stock_kind not in KINDS:
            raise BusinessError("المتاح بتاريخ محتاج المخزن ونوع المخزون (فاتورة / بيان).")
        return max(ZERO, self.repository.min_balance_from(int(item_id), int(warehouse_id), stock_kind, on_date, ex=ex))

    def average_cost(self, item_id: int, stock_kind: str = "invoice", *, on_date: dt.date | None = None,
                     ex: Executor | None = None) -> Decimal:
        """متوسط التكلفة (دلوقتي أو آخر يوم معيّن) — صفر لو مفيش حركة."""
        return self.repository.avg_cost(int(item_id), stock_kind, on_date, ex=ex) or ZERO

    def item_summary(self, item_id: int) -> dict[str, Any]:
        """رصيد الصنف لشاشة الأصناف: الإجمالي (المخازن الحقيقية) · القيمة · المتوسط لكل نوع · لكل مخزن."""
        data = self.repository.item_summary(int(item_id))
        data["qty"] = sum((r["qty"] for r in data["kinds"]), ZERO)
        data["value"] = sum((r["value"] for r in data["kinds"]), ZERO)
        return data

    def below_reorder_count(self) -> int:
        return self.repository.below_reorder_count()

    def total_value(self) -> Decimal:
        return self.repository.total_value()

    def warehouse_summary(self) -> dict[int, dict]:
        return self.repository.warehouse_summary()

    def cost_differences(self) -> list[dict]:
        """المستندات اللي قيمة حركاتها اتغيّرت بعد ما اتقيّدت (حركة بتاريخ قديم) — لبند «فروق التكلفة»."""
        return self.repository.cost_differences()

    def movements_of(self, doc_type: str, source_id: int) -> list[dict]:
        return self.repository.source_lines(str(doc_type), int(source_id))
