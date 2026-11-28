"""«دفتر المخزون» — محرك حركة المخزون (المرحلة 6 · من غير شاشة · سكريبت 0043 + 0044).

كل مستند بيحرّك مخزون (بضاعة أول المدة · فاتورة / مردود شراء · إذن صرف / مردود بيع · تحويل · تسوية جرد)
**مابيحسبش تكلفة بنفسه**: بيبعت سطور ``StockLine`` (الصنف · المخزن · فاتورة/بيان · الكمية بالوحدة الأساسية
+ داخل / − خارج · وتكلفة الوحدة للداخل)، والمحرك جوه transaction المستند:

1. بيقفل صفوف الأصناف (``FOR NO KEY UPDATE`` بالترتيب) — جهازين بيبيعوا نفس الصنف بيتصفّوا ورا بعض.
2. بيمسح حركات المستند القديمة (لو بيتعدّل) ويسجّل الجديدة — **التاريخ القديم مسموح** (رد المستخدم).
3. **بيعيد حساب** الصنف من أقدم يوم اتأثر لحد آخر حركة:
   * **متوسط مرجّح مستمر على مستوى الشركة لكل (صنف + فاتورة/بيان)** — الداخل بتكلفته بيغيّر المتوسط، والخارج
     بيطلع بالمتوسط ومابيغيّروش · التحويل بين مخزنين حقيقيين: الخارج بالمتوسط والداخل **بنفس القيمة** ·
     **مرتجع المشتريات** (0049): الخارج **بالتكلفة اللي المستند بعتها** (سعر الشراء) والمتوسط بيتحسب من الباقي.
   * ترتيب نفس اليوم: **الوارد بتكلفة ← التحويلات ← الصادر** (وجوه كل مجموعة بالترتيب اللي اتسجلت بيه).
   * آخر خروج بيصفّر الكمية بياخد **كل القيمة الباقية** (مفيش قروش معلّقة في المخزون).
   * **الرصيد السالب بالصلاحية بس** (رد المستخدم — صلاحية «من غير رصيد» في كل شاشة لوحدها، والمستند بيبعت
     ``allow_negative``): من غيرها أي يوم في أي مخزن يبقى آخره بالسالب **أكتر من الأول** ← ``NegativeStockError``.
     اللي اتصرف من غير رصيد بيطلع **بآخر متوسط مؤقتاً**، ولما شراء ييجي يغطّيه **تكلفته بتتصلّح بسعر الشراء ده**
     (الأقدم الأول) — والفرق بيبان في ``cost_differences``.
   * **المخزن الوهمي** كميات بس (من غير تكلفة ولا قيمة) · التحويل بينه وبين الحقيقي مسموح في الاتجاهين
     (رد المستخدم): حقيقي ← وهمي = خروج بالمتوسط (بيطلع من قيمة المخزون) · وهمي ← حقيقي = دخول **بآخر متوسط**.
   * التحويل **من فاتورة لبيان (أو العكس)** مايعدّيش رصيد النوع في الشركة حتى بالصلاحية (التكلفة بتتنقل لمتوسط تاني).
4. بيرجّع لكل سطر **تكلفته وقيمته** (المستند بيعمل بيهم سطور «المخزون» و«تكلفة المبيعات» في محرك الترحيل).
   القيمة اللي اتقيّدت بتتحفظ (``booked_value``)، ولو إعادة الحساب غيّرت قيمة حركة، الفرق بيبان في
   ``cost_differences`` (بند «فروق التكلفة» هيقيّده).

الصلاحيات بتاعة المستند نفسه (المحرك بيتنادى من Service المستند بعد ما يتأكد منها) · سجل التعديلات بتاع المستند.
"""

from __future__ import annotations

import bisect
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
_SAVED = ("unit_cost", "value", "kind_qty_after", "kind_value_after", "avg_cost_after", "wh_qty_after", "day_seq")


@dataclass(frozen=True)
class StockLine:
    """سطر حركة من المستند: الكمية **بالوحدة الأساسية** (+ داخل · − خارج)."""

    item_id: int
    warehouse_id: int
    qty: Any
    stock_kind: str = "invoice"
    unit_cost: Any = None            # الداخل بس (شراء · أول المدة · مردود بيع بتكلفته الأصلية · زيادة جرد)
    transfer_of: int | None = None   # الداخل في التحويل ← رقم سطر الخارج (من 0) في نفس المستند


class NegativeStockError(BusinessError):
    """الرصيد هيبقى بالسالب والمستخدم مالوش صلاحية «من غير رصيد» — ``shortages`` فيها كل صنف ومخزن ويوم."""

    def __init__(self, shortages: list[dict[str, Any]]) -> None:
        self.shortages = shortages
        first = shortages[0]
        text = (f"الصنف {first['item_code']} «{first['item_name']}»: رصيد «{KINDS[first['stock_kind']]}» في المخزن "
                f"«{first['warehouse_name']}» هيبقى بالسالب آخر يوم {first['date']:%d/%m/%Y} "
                f"(ناقص {fmt_qty(first['short'])} {first['unit_name']})")
        if len(shortages) > 1:
            text += f" — و{len(shortages) - 1} تاني"
        super().__init__(text + " — الصرف من غير رصيد محتاج صلاحية.")


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


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


class _History:
    """رصيد كل (مخزن + نوع) آخر أي يوم **قبل التعديل** — عشان السالب اللي كان موجود (بصلاحية) مايترفضش تاني."""

    def __init__(self, rows: list[dict]) -> None:
        self.days: dict[tuple[int, str], list[dt.date]] = {}
        self.totals: dict[tuple[int, str], list[Decimal]] = {}
        for r in rows:                                       # مترتبين بالتاريخ
            key = (int(r["warehouse_id"]), r["stock_kind"])
            total = (self.totals.get(key) or [ZERO])[-1] + r["qty"]
            self.days.setdefault(key, []).append(r["movement_date"])
            self.totals.setdefault(key, []).append(total)

    def balance(self, key: tuple[int, str], day: dt.date) -> Decimal:
        days = self.days.get(key)
        if not days:
            return ZERO
        n = bisect.bisect_right(days, day)
        return self.totals[key][n - 1] if n else ZERO


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
            row = {"line": n, "label": label, "item_id": int(line.item_id), "warehouse_id": int(line.warehouse_id),
                   "stock_kind": kind, "qty": qty, "pair": None, "pair_of": None,
                   "is_virtual": bool(wh["is_virtual"]), "given_cost": None, "mode": None}
            if line.transfer_of is not None:
                src = int(line.transfer_of)
                if not 0 <= src < len(lines) or src == n - 1:
                    raise BusinessError(f"{label}: سطر الخارج بتاع التحويل مش موجود.")
                if src >= n - 1:
                    raise BusinessError(f"{label}: سطر الخارج بتاع التحويل لازم ييجي قبل الداخل.")
                out = clean[src]
                if qty <= 0 or out["qty"] != -qty or out["item_id"] != row["item_id"] or out["pair_of"] is not None:
                    raise BusinessError(f"{label}: الداخل في التحويل لازم نفس الصنف والكمية بتاعة سطر الخارج.")
                if (out["warehouse_id"], out["stock_kind"]) == (row["warehouse_id"], kind):
                    raise BusinessError(f"{label}: التحويل لنفس المخزن ونفس النوع مالوش معنى.")
                if line.unit_cost not in (None, ""):
                    raise BusinessError(f"{label}: الداخل في التحويل بياخد تكلفته لوحده — ماتكتبلوش تكلفة.")
                row["pair"] = src
                out["pair_of"] = n - 1
                row["mode"] = "none" if row["is_virtual"] else ("current" if out["is_virtual"] else "transfer")
            elif row["is_virtual"]:
                row["mode"] = "none"
            elif qty > 0:
                if line.unit_cost in (None, ""):
                    raise BusinessError(f"{label}: تكلفة الوحدة مطلوبة للداخل.")
                cost = _decimal(line.unit_cost, "تكلفة الوحدة")
                if cost < 0:
                    raise BusinessError(f"{label}: تكلفة الوحدة مينفعش تبقى بالسالب.")
                if cost > MAX_COST:
                    raise BusinessError(f"{label}: تكلفة الوحدة أكبر من المسموح.")
                row["given_cost"] = cost.quantize(COST_STEP, rounding=ROUND_HALF_UP)
                row["mode"] = "given"
            elif line.unit_cost not in (None, ""):
                # خارج بتكلفة من المستند (مرتجع المشتريات — رد المستخدم 2026-11-28: «بنفس السعر اللي اشتريت بيه»).
                cost = _decimal(line.unit_cost, "تكلفة الوحدة")
                if cost < 0:
                    raise BusinessError(f"{label}: تكلفة الوحدة مينفعش تبقى بالسالب.")
                if cost > MAX_COST:
                    raise BusinessError(f"{label}: تكلفة الوحدة أكبر من المسموح.")
                row["given_cost"] = cost.quantize(COST_STEP, rounding=ROUND_HALF_UP)
                row["mode"] = "given_out"
            else:
                row["mode"] = "average"
            if row["is_virtual"] and line.unit_cost not in (None, "") and line.transfer_of is None:
                raise BusinessError(f"{label}: المخزن الوهمي كميات بس — ماتكتبلوش تكلفة.")
            clean.append(row)
        return clean

    # ---- الكتابة (جوه transaction المستند) -------------------------------------------
    def post(self, tx: Executor, *, doc_type: str, source_id: int, source_no: str, movement_date: dt.date,
             lines: list[StockLine], allow_negative: bool = False) -> list[dict[str, Any]]:
        """يسجّل حركات المستند (أو يبدّل حركاته القديمة) ← لكل سطر: التكلفة والقيمة بعد إعادة الحساب.

        لازم يتنادى جوه ``db.atomic()`` بتاع المستند. ``allow_negative`` = المستخدم عنده صلاحية «من غير رصيد»
        في شاشة المستند. أي غلط ← ``BusinessError`` (والسالب من غير صلاحية ← ``NegativeStockError``) والمستند
        كله بيرجع.
        """
        doc = self._doc(tx, doc_type)
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
            start = self._starts(old)
            for row in clean:
                start[row["item_id"]] = min(start.get(row["item_id"], movement_date), movement_date)
            history = {i: _History(self.repository.daily_qty(i, ex=tx)) for i in start}
            if old:
                self.repository.delete_source(doc["code"], int(source_id), ex=tx)
            ids: list[int] = []
            for row in clean:
                pair_id = ids[row["pair"]] if row["pair"] is not None else None
                ids.append(self.repository.insert_movement({
                    "source_type": doc["code"], "source_id": int(source_id), "source_no": source_no,
                    "line_no": row["line"], "movement_date": movement_date, "item_id": row["item_id"],
                    "warehouse_id": row["warehouse_id"], "stock_kind": row["stock_kind"], "qty": row["qty"],
                    "cost_mode": row["mode"], "given_cost": row["given_cost"], "pair_id": pair_id,
                    "is_virtual": row["is_virtual"], "created_by": SESSION.user_id}, ex=tx))
            self._replay_all(tx, items, start, history, allow_negative)
            self.repository.book(ids, ex=tx)
            return self.repository.source_lines(doc["code"], int(source_id), ex=tx)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def remove(self, tx: Executor, *, doc_type: str, source_id: int, allow_negative: bool = False) -> bool:
        """يمسح حركات المستند (إلغاء الترحيل / المسح) ويعيد الحساب ← ``False`` لو مالوش حركات.

        لو اللي اتصرف بعده هيخلّي الرصيد بالسالب ومفيش صلاحية «من غير رصيد» ← ``NegativeStockError`` ومابيتمسحش.
        """
        doc = self._doc(tx, doc_type)
        try:
            old = self.repository.source_movements(doc["code"], int(source_id), ex=tx)
            if not old:
                return False
            items = self.repository.lock_items([int(m["item_id"]) for m in old], tx)
            start = self._starts(old)
            history = {i: _History(self.repository.daily_qty(i, ex=tx)) for i in start}
            self.repository.delete_source(doc["code"], int(source_id), ex=tx)
            self._replay_all(tx, items, start, history, allow_negative)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return True

    def _doc(self, tx: Executor, doc_type: str) -> dict:
        doc = self.repository.doc_type(str(doc_type), ex=tx)
        if doc is None:
            raise BusinessError(f"نوع المستند «{doc_type}» مالوش حركة مخزون.")
        return doc

    @staticmethod
    def _starts(movements: list[dict]) -> dict[int, dt.date]:
        start: dict[int, dt.date] = {}
        for m in movements:
            item_id = int(m["item_id"])
            start[item_id] = min(start.get(item_id, m["movement_date"]), m["movement_date"])
        return start

    def _replay_all(self, tx: Executor, items: dict[int, dict], start: dict[int, dt.date],
                    history: dict[int, _History], allow_negative: bool) -> None:
        shortages: list[dict[str, Any]] = []
        for item_id in sorted(start):
            shortages += self._replay(tx, items[item_id], start[item_id], history[item_id])
        if shortages and not allow_negative:
            raise NegativeStockError(shortages)

    # ---- إعادة الحساب -----------------------------------------------------------------
    def _safe_start(self, tx: Executor, item_id: int, day: dt.date) -> dt.date:
        """يرجع لأول يوم الرصيد قبله مش بالسالب — عشان اللي اتصرف من غير رصيد يتصلّح بالشراء اللي غطّاه."""
        while True:
            states = self.repository.chain_state_before(item_id, day, ex=tx)
            if all((s["qty"] or ZERO) >= 0 for s in states.values()):
                return day
            previous = self.repository.previous_day(item_id, day, ex=tx)
            if previous is None:
                return day
            day = previous

    def _replay(self, tx: Executor, item: dict[str, Any], day: dt.date, history: _History) -> list[dict[str, Any]]:
        item_id = int(item["id"])
        day = self._safe_start(tx, item_id, day)
        chains: dict[str, dict[str, Any]] = {}
        for kind, row in self.repository.chain_state_before(item_id, day, ex=tx).items():
            chains[kind] = {"qty": row["qty"] or ZERO, "value": row["value"] or ZERO, "avg": row["avg"] or ZERO,
                            "short": []}
        wh_qty = dict(self.repository.warehouse_qty_before(item_id, day, ex=tx))
        movements = self.repository.movements_from(item_id, day, ex=tx)
        by_day: dict[dt.date, list[dict]] = {}
        for m in movements:
            by_day.setdefault(m["movement_date"], []).append(m)
        results: dict[int, dict[str, Any]] = {}
        for current, rows in by_day.items():
            ins = {int(m["pair_id"]): m for m in rows if m["pair_id"] is not None}
            ordered = [m for m in rows if m["qty"] > 0 and m["pair_id"] is None]
            for m in rows:
                if m["qty"] < 0 and int(m["id"]) in ins:
                    ordered += [m, ins[int(m["id"])]]
            ordered += [m for m in rows if m["qty"] < 0 and int(m["id"]) not in ins]
            for seq, m in enumerate(ordered, start=1):
                pair = ins.get(int(m["id"])) if m["qty"] < 0 else None
                if int(m["id"]) not in results:
                    self._apply(item, m, pair, chains, results)
                key = (int(m["warehouse_id"]), m["stock_kind"])
                wh_qty[key] = wh_qty.get(key, ZERO) + m["qty"]
                results[int(m["id"])]["wh_qty_after"] = wh_qty[key]
                results[int(m["id"])]["day_seq"] = seq
        for m in movements:
            computed = results[int(m["id"])]
            if any(computed[c] != m[c] for c in _SAVED):
                self.repository.save_computed(int(m["id"]), computed, ex=tx)
        self.repository.refresh_balances(item_id, ex=tx)
        return self._shortages(tx, item, day, history)

    def _shortages(self, tx: Executor, item: dict[str, Any], day: dt.date, before: _History) -> list[dict[str, Any]]:
        """كل (مخزن + نوع) آخره بالسالب **أكتر من قبل التعديل** في أي يوم من ``day`` (أيام الحركات القديمة والجديدة)."""
        after = _History(self.repository.daily_qty(int(item["id"]), ex=tx))
        found: list[dict[str, Any]] = []
        for key in sorted(set(before.days) | set(after.days)):
            days = sorted({d for d in before.days.get(key, []) + after.days.get(key, []) if d >= day})
            for current in days:
                balance = after.balance(key, current)
                if balance < 0 and balance < before.balance(key, current):
                    found.append({"item_id": int(item["id"]), "item_code": item_code(item["seq"]),
                                  "item_name": item["name"], "unit_name": item["unit_name"], "warehouse_id": key[0],
                                  "stock_kind": key[1], "date": current, "short": -balance})
                    break
        if found:
            names = self.repository.warehouses([f["warehouse_id"] for f in found], ex=tx)
            for f in found:
                f["warehouse_name"] = names[f["warehouse_id"]]["name_ar"]
        return found

    @staticmethod
    def _snapshot(chain: dict[str, Any] | None) -> dict[str, Any]:
        if chain is None:
            return {"kind_qty_after": None, "kind_value_after": None, "avg_cost_after": None}
        return {"kind_qty_after": chain["qty"], "kind_value_after": chain["value"], "avg_cost_after": chain["avg"]}

    def _apply(self, item: dict, m: dict[str, Any], pair: dict[str, Any] | None,
               chains: dict[str, dict[str, Any]], results: dict[int, dict]) -> None:
        """يحسب الحركة (ولو خارج تحويل، الداخل اللي قصاده معاه)."""
        mid = int(m["id"])
        if m["is_virtual"]:
            results[mid] = {"unit_cost": None, "value": ZERO, **self._snapshot(None)}
            if pair is not None and not pair["is_virtual"]:          # وهمي ← حقيقي: الداخل بآخر متوسط
                chain = self._chain(chains, pair["stock_kind"])
                self._receive(pair, chain, chain["avg"], None, results)
            elif pair is not None:
                results[int(pair["id"])] = {"unit_cost": None, "value": ZERO, **self._snapshot(None)}
            return
        chain = self._chain(chains, m["stock_kind"])
        if m["qty"] > 0:                                              # داخل بتكلفة من المستند
            self._receive(m, chain, m["given_cost"], None, results)
            return
        if m["cost_mode"] == "given_out":                             # خارج بتكلفة من المستند (مرتجع شراء)
            self._issue_given(m, chain, results)
            return
        if pair is not None and not pair["is_virtual"] and pair["stock_kind"] == m["stock_kind"]:
            # تحويل بين مخزنين حقيقيين بنفس النوع: الشركة مااتغيّرش — الخارج بالمتوسط والداخل بنفس القيمة.
            value = _money(m["qty"] * chain["avg"])
            results[mid] = {"unit_cost": chain["avg"], "value": value, **self._snapshot(chain)}
            results[int(pair["id"])] = {"unit_cost": chain["avg"], "value": -value, **self._snapshot(chain)}
            return
        if pair is not None and not pair["is_virtual"] and chain["qty"] + m["qty"] < 0:
            raise BusinessError(
                f"الصنف {item_code(item['seq'])} «{item['name']}»: التحويل من «{KINDS[m['stock_kind']]}» لـ "
                f"«{KINDS[pair['stock_kind']]}» أكبر من رصيد «{KINDS[m['stock_kind']]}» في الشركة يوم "
                f"{m['movement_date']:%d/%m/%Y} — مينفعش حتى بصلاحية «من غير رصيد».")
        self._issue(m, chain, results)
        if pair is None:
            return
        if pair["is_virtual"]:                                        # حقيقي ← وهمي: الداخل كمية بس
            results[int(pair["id"])] = {"unit_cost": None, "value": ZERO, **self._snapshot(None)}
            return
        out = results[mid]                                            # فاتورة ↔ بيان: بنفس القيمة في النوع التاني
        self._receive(pair, self._chain(chains, pair["stock_kind"]), out["unit_cost"], -out["value"], results)

    @staticmethod
    def _chain(chains: dict[str, dict[str, Any]], kind: str) -> dict[str, Any]:
        return chains.setdefault(kind, {"qty": ZERO, "value": ZERO, "avg": ZERO, "short": []})

    def _issue(self, m: dict[str, Any], chain: dict[str, Any], results: dict[int, dict]) -> None:
        """خارج بالمتوسط — اللي مش موجود بيطلع بآخر متوسط مؤقتاً ويتسجل «ناقص» يتصلّح بالشراء الجاي."""
        qty, have, avg = m["qty"], chain["qty"], chain["avg"]
        if have + qty >= 0:
            value = -chain["value"] if have + qty == 0 else _money(qty * avg)
            unit_cost = avg
        else:
            missing = -(have + qty) if have > 0 else -qty
            value = (-chain["value"] if have > 0 else ZERO) - _money(missing * avg)
            unit_cost = (value / qty).quantize(COST_STEP, rounding=ROUND_HALF_UP)
            chain["short"].append([int(m["id"]), missing, avg, qty])   # [الحركة، الناقص، بكام طلع، كميتها]
        chain["qty"] += qty
        chain["value"] += value
        results[int(m["id"])] = {"unit_cost": unit_cost, "value": value, **self._snapshot(chain)}

    def _issue_given(self, m: dict[str, Any], chain: dict[str, Any], results: dict[int, dict]) -> None:
        """خارج **بالتكلفة اللي المستند بعتها** (مرتجع المشتريات بسعر الشراء) — والمتوسط بيتحسب من الباقي.

        * لو الرصيد مش مغطّي الكمية (أو بالسالب) ← بيطلع زي أي خارج (بالمتوسط + «ناقص» يتصلّح بالشراء الجاي).
        * لو بيصفّر الكمية ← بياخد كل القيمة الباقية · ولو السعر أكبر من قيمة الباقي ← بياخد القيمة كلها (المتوسط صفر).
        * الفرق بين القيمة دي وسعر المستند بيتقيّد في «فروق تكلفة المخزون» من المستند نفسه.
        """
        qty, have = m["qty"], chain["qty"]
        if have <= 0 or have + qty < 0:
            self._issue(m, chain, results)
            return
        value = -chain["value"] if have + qty == 0 else _money(qty * m["given_cost"])
        if chain["value"] + value < 0:
            value = -chain["value"]
        chain["qty"] += qty
        chain["value"] += value
        if chain["qty"] > 0:
            chain["avg"] = max(ZERO, (chain["value"] / chain["qty"]).quantize(COST_STEP, rounding=ROUND_HALF_UP))
        unit_cost = (value / qty).quantize(COST_STEP, rounding=ROUND_HALF_UP)
        results[int(m["id"])] = {"unit_cost": unit_cost, "value": value, **self._snapshot(chain)}

    @staticmethod
    def _receive(m: dict[str, Any], chain: dict[str, Any], unit_cost: Decimal, value: Decimal | None,
                 results: dict[int, dict]) -> None:
        """داخل بتكلفة — لو فيه «ناقص» (اتصرف من غير رصيد) بيغطّيه الأول وتكلفته بتتصلّح بالتكلفة دي."""
        qty = m["qty"]
        value = _money(qty * unit_cost) if value is None else value
        cover, adjusted, last = qty, ZERO, None
        while cover > 0 and chain["short"]:
            entry = chain["short"][0]
            part = min(cover, entry[1])
            adj = _money(part * (unit_cost - entry[2]))
            if adj:
                out = results[entry[0]]
                out["value"] -= adj
                out["unit_cost"] = (out["value"] / entry[3]).quantize(COST_STEP, rounding=ROUND_HALF_UP)
                adjusted += adj
                last = (out, entry[3])
            entry[1] -= part
            cover -= part
            if entry[1] == 0:
                chain["short"].pop(0)
        chain["qty"] += qty
        chain["value"] += value - adjusted
        if chain["qty"] == 0 and chain["value"] != 0 and last is not None:
            out, out_qty = last                              # قروش التقريب على آخر خارج اتصلّح
            out["value"] -= chain["value"]
            out["unit_cost"] = (out["value"] / out_qty).quantize(COST_STEP, rounding=ROUND_HALF_UP)
            chain["value"] = ZERO
        if chain["qty"] > 0:
            chain["avg"] = max(ZERO, (chain["value"] / chain["qty"]).quantize(COST_STEP, rounding=ROUND_HALF_UP))
        else:
            chain["avg"] = unit_cost
        results[int(m["id"])] = {"unit_cost": unit_cost, "value": value, "kind_qty_after": chain["qty"],
                                 "kind_value_after": chain["value"], "avg_cost_after": chain["avg"]}

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
