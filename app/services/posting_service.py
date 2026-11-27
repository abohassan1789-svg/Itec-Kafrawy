"""محرك الترحيل المركزي (المرحلة 4 — من غير شاشة · سكريبت 0032 «قواعد الترحيل»).

كل مستند (فاتورة / مردود / سند / تسوية / أرصدة افتتاحية) **مابيكتبش حسابات**: بيبعت سطور ``PostingLine`` فيها
**دور السطر ومبلغه** (والصنف / الطرف / كود الضريبة / الخزنة … اللي محتاجهم الدور)، والمحرك:

1. بيجيب **الحساب** من ``acc.posting_rules`` (الافتراضي · نوع الصنف ← أقرب أب ← الافتراضي · مجموعة الطرف ·
   كود الضريبة · الخزنة · الحساب البنكي · نوع المصروف · أو حساب من المستند نفسه).
2. بيحط **الجنب** من القاعدة (والمبلغ **السالب بيقلب الجنب** — عجز الجرد · الرصيد الافتتاحي الدائن).
3. بيجمّع السطور المتشابهة (نفس الحساب والجنب والأبعاد والبيان) ويقرّب لقرشين.
4. **فرق التقريب** (لو صغير — نص قرش لكل سطر بالكتير) بيتحط على **أكبر سطر** في الجنب الناقص، عشان المدين = الدائن
   بالظبط. لو الفرق أكبر ← المستند فيه غلط في الحسبة ← **خطأ ومايترحّلش**.
5. بيعمل القيد (أو يعدّل قيد المستند القديم) **ويرحّله في نفس الـ transaction بتاع المستند** — والقاعدة بتتأكد تاني
   من التوازن والسنة المفتوحة والحسابات الفرعية النشطة والطرف (0031).

**المحرك عمره ما يسكت** (عكس E3marEG): حساب افتراضي مش متحدد · طرف ناقص · حساب رئيسي أو موقوف · مش متوازن ·
سنة مقفولة ← ``BusinessError`` بالعربي والمستند كله بيرجع (rollback).

* القيد الأوتوماتيك **من غير رقم JV** — بيتعرض برقم المستند (``source_no``)، وقيد واحد بس لكل مستند
  (Partial Unique Index — 0031).
* **إلغاء ترحيل المستند** (``unpost_document``) بيمسح قيده (والأثر كله في ``sec.audit_log`` بالسطور) — مفيش عكس
  (رد المستخدم 2026-11-16).
* الصلاحيات بتاعة المستند نفسه (المحرك بيتنادى من Service المستند بعد ما يتأكد منها).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.journal_repository import PARTY_COLUMNS, JournalRepository
from app.repositories.posting_repository import PostingRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.errors import BusinessError

ENTITY, ENTITY_LABEL = "acc.journal_entries", "قيد يومية"
CENT = Decimal("0.01")
HALF_CENT = Decimal("0.005")
MAX_AMOUNT = Decimal("9999999999999999.99")
DEFAULTS_SCREEN = "الحسابات الافتراضية"
PARTNER_CURRENT_KEY = "partner_cur"   # جاري الشريك ← حسابه لوحده تحت 32 (0042)
PARTY_LABELS = {"customer": "العميل", "supplier": "المورد", "employee": "الموظف", "partner": "الشريك"}
SIDE_LABELS = {"dr": "مدين", "cr": "دائن"}
_MASTER_FIELDS = {"treasury": ("treasury_id", "الخزنة"), "bank": ("bank_account_id", "الحساب البنكي"),
                  "expense": ("expense_type_id", "نوع المصروف")}


@dataclass(frozen=True)
class PostingLine:
    """سطر من المستند: **الدور والمبلغ** + اللي الدور محتاجه عشان الحساب يتعرف."""

    role: str
    amount: Any
    account_id: int | None = None          # «given» بس (الأرصدة الافتتاحية · «حساب تاني»)
    category_id: int | None = None         # «category» (نوع الصنف — الفاضي = الافتراضي)
    tax_code_id: int | None = None         # «tax_sales» / «tax_purchase»
    treasury_id: int | None = None         # «treasury»
    bank_account_id: int | None = None     # «bank»
    expense_type_id: int | None = None     # «expense»
    party: tuple[str, int] | None = None   # ("customer", 12) — إجباري على حسابات الرقابة
    cost_center_id: int | None = None
    project_id: int | None = None
    description: str | None = None
    # المستند بعملة غير الأساسية (0048 — «تعدد العملات الكامل»): العملة والسعر والمبلغ بالعملة على سطر القيد.
    # ``amount`` نفسه دايماً بالجنيه · السطور بنفس الحساب والعملة والسعر بتتجمّع (والمبلغ بالعملة بيتجمّع معاها).
    currency_code: str | None = None
    fx_rate: Any = None
    amount_fc: Any = None


def money(value: Any) -> Decimal:
    """المبلغ زي ما المستند حسبه (ممكن كسور أكتر من قرشين — التقريب بعد التجميع)."""
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).strip().replace(",", "")) if value not in (None, "") else Decimal("0")
        except InvalidOperation:
            raise BusinessError(f"المبلغ «{value}» مش رقم.") from None
    if not amount.is_finite():
        raise BusinessError(f"المبلغ «{value}» مش رقم.")
    if abs(amount) > MAX_AMOUNT:
        raise BusinessError("المبلغ أكبر من المسموح.")
    return amount


def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


class PostingService:
    def __init__(self, db: Database | None = None, repository: PostingRepository | None = None,
                 journal: JournalRepository | None = None, audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or PostingRepository(self.db)
        self.journal = journal or JournalRepository(self.db)
        self.audit = audit or AuditService(self.db)

    # ---- بناء السطور (من غير كتابة) ------------------------------------------------
    def _doc(self, ex: Executor, doc_type: str) -> dict[str, Any]:
        doc = self.repository.doc_type(str(doc_type), ex=ex)
        if doc is None or doc["code"] == "manual":
            raise BusinessError(f"نوع المستند «{doc_type}» مالوش قواعد ترحيل.")
        return doc

    def _default(self, ex: Executor, key: str, role_name: str) -> int:
        row = self.repository.default_key(key, ex=ex)
        if row is None:
            raise BusinessError(f"«{role_name}»: المفتاح «{key}» مش موجود في الحسابات الافتراضية.")
        if not row["account_id"]:
            raise BusinessError(f"«{role_name}»: الحساب الافتراضي «{row['name_ar']}» مش متحدد — "
                                f"حدده من شاشة «{DEFAULTS_SCREEN}».")
        return int(row["account_id"])

    def _account_for(self, ex: Executor, rule: dict[str, Any], line: PostingLine,
                     cache: dict[tuple, int]) -> int:
        source, name = rule["account_source"], rule["name_ar"]
        if source == "given":
            if not line.account_id:
                raise BusinessError(f"«{name}»: الحساب مش متحدد في المستند.")
            return int(line.account_id)
        if source == "default":
            key = ("default", rule["default_key"])
            if key not in cache:
                cache[key] = self._default(ex, rule["default_key"], name)
            return cache[key]
        if source == "category":
            key = ("category", line.category_id, rule["category_kind"])
            if key not in cache:
                account_id = self.repository.category_account(line.category_id, rule["category_kind"], ex=ex)
                if not account_id:
                    where = (f"نوع الصنف «{self.repository.category_name(line.category_id, ex=ex)}»"
                             if line.category_id else "الصنف من غير نوع")
                    raise BusinessError(f"«{name}»: مفيش حساب لـ {where} ولا حساب افتراضي — "
                                        f"حدده من شاشة «أنواع الأصناف» أو «{DEFAULTS_SCREEN}».")
                cache[key] = int(account_id)
            return cache[key]
        if source == "party":
            if line.party is None:
                raise BusinessError(f"«{name}»: لازم يتحدد {PARTY_LABELS[rule['party_kind']]}.")
            kind, party_id = line.party
            # المفتاح الافتراضي جوه الكاش: نفس الشريك ممكن يبقى على 32 (جاري) و31 (رأس المال) في نفس القيد.
            key = ("party", kind, int(party_id), rule["default_key"])
            if key not in cache:
                party = self.repository.party(kind, int(party_id), ex=ex)
                if party is None:
                    raise BusinessError(f"«{name}»: {PARTY_LABELS[kind]} مش موجود (ممكن يكون اتمسح).")
                if party["account_id"]:
                    cache[key] = int(party["account_id"])
                elif party.get("own_account_id") and rule["default_key"] == PARTNER_CURRENT_KEY:
                    cache[key] = int(party["own_account_id"])         # حساب الشريك نفسه تحت 32 (0042)
                elif rule["default_key"]:
                    cache[key] = self._default(ex, rule["default_key"], name)
                else:
                    raise BusinessError(f"«{name}»: {PARTY_LABELS[kind]} «{party['name_ar']}» مالوش حساب رقابة.")
            return cache[key]
        if source in ("tax_sales", "tax_purchase"):
            if not line.tax_code_id:
                return self._account_for(ex, {**rule, "account_source": "default"}, line, cache)
            key = (source, int(line.tax_code_id))
            if key not in cache:
                tax = self.repository.tax_account(int(line.tax_code_id),
                                                  "sales" if source == "tax_sales" else "purchase", ex=ex)
                if tax is None:
                    raise BusinessError(f"«{name}»: كود الضريبة مش موجود (ممكن يكون اتمسح).")
                cache[key] = (int(tax["account_id"]) if tax["account_id"]
                              else self._default(ex, rule["default_key"], name))
            return cache[key]
        field, label = _MASTER_FIELDS[source]
        master_id = getattr(line, field)
        if not master_id:
            raise BusinessError(f"«{name}»: لازم تتحدد {label}.")
        key = (source, int(master_id))
        if key not in cache:
            master = self.repository.master_account(source, int(master_id), ex=ex)
            if master is None:
                raise BusinessError(f"«{name}»: {label} مش موجودة (ممكن تكون اتمسحت).")
            cache[key] = int(master["account_id"])
        return cache[key]

    def build(self, doc_type: str, lines: list[PostingLine], ex: Executor | None = None, *,
              cost_center_id: int | None = None, project_id: int | None = None) -> list[dict[str, Any]]:
        """السطور النهائية للقيد (الحسابات · الجنب · التجميع · التقريب · التوازن) — من غير ما يكتب حاجة.

        ``cost_center_id`` / ``project_id`` = مركز التكلفة وأمر التوريد بتوع **المستند كله**: المركز بيتحط على سطور
        **الإيرادات والمصروفات** اللي مالهاش مركز (المبيعات والتكلفة «لازم مركز تكلفة»)، وأمر التوريد على كل سطر
        مالوش أمر. المستند يقدر يستعمل الدالة دي لـ «معاينة القيد» قبل الحفظ. أي غلط ← ``BusinessError`` بالعربي.
        """
        ex = ex if ex is not None else self.db
        doc = self._doc(ex, doc_type)
        rules = {r["role"]: r for r in self.repository.rules(doc["code"], ex=ex)}
        cache: dict[tuple, int] = {}
        resolved: list[tuple[dict[str, Any], PostingLine, Decimal, int]] = []
        for line in lines:
            rule = rules.get(line.role)
            if rule is None:
                raise BusinessError(f"دور السطر «{line.role}» مش معروف في قواعد ترحيل «{doc['name_ar']}».")
            amount = money(line.amount)
            if amount == 0:
                continue
            if line.party is not None:
                kind = line.party[0]
                if kind not in PARTY_COLUMNS:
                    raise BusinessError(f"«{rule['name_ar']}»: نوع الطرف «{kind}» مش معروف.")
                if rule["party_kind"] and kind != rule["party_kind"]:
                    raise BusinessError(f"«{rule['name_ar']}»: الطرف لازم يبقى {PARTY_LABELS[rule['party_kind']]} "
                                        f"— مش {PARTY_LABELS[kind]}.")
            resolved.append((rule, line, amount, self._account_for(ex, rule, line, cache)))
        if not resolved:
            raise BusinessError("المستند مالوش أثر محاسبي — كل المبالغ صفر.")

        accounts = self.repository.accounts(sorted({r[3] for r in resolved}), ex=ex)
        groups: dict[tuple, dict[str, Any]] = {}
        for rule, line, amount, account_id in resolved:
            account = accounts.get(account_id) or {}
            side = rule["side"] if amount > 0 else ("cr" if rule["side"] == "dr" else "dr")
            text = " ".join(str(line.description or "").split())[:300] or None
            party = (line.party[0], int(line.party[1])) if line.party else None
            center = line.cost_center_id or (
                cost_center_id if account.get("account_type") in ("revenue", "expense") else None) or None
            project = line.project_id or project_id or None
            currency, rate, fc = self._fc(line, rule["name_ar"])
            key = (account_id, side, center, project, party, text, currency, rate)
            group = groups.get(key)
            if group is None:
                groups[key] = {"role": rule["role"], "role_name": rule["name_ar"], "order": rule["sort_order"],
                               "seen": len(groups), "account_id": account_id, "side": side, "raw": abs(amount),
                               "cost_center_id": center, "project_id": project, "party": party, "description": text,
                               "currency_code": currency, "fx_rate": rate, "fc": fc}
                self._check_account(groups[key], accounts.get(account_id))
            else:
                group["raw"] += abs(amount)
                if fc is not None:
                    group["fc"] += fc

        out = sorted(groups.values(), key=lambda g: (g["order"], g["seen"]))
        for g in out:
            g["amount"] = g.pop("raw").quantize(CENT, rounding=ROUND_HALF_UP)
            fc = g.pop("fc")
            g["amount_fc"] = fc.quantize(CENT, rounding=ROUND_HALF_UP) if fc is not None else None
            if g["currency_code"] and not g["amount_fc"]:          # مبلغ بالعملة صفر ← السطر من غير عملة
                g["currency_code"] = g["fx_rate"] = g["amount_fc"] = None
        out = [g for g in out if g["amount"] > 0]
        self._balance(out, len(lines))
        return [self._line(n, g) for n, g in enumerate(out, start=1)]

    @staticmethod
    def _fc(line: PostingLine, name: str) -> tuple[str | None, Decimal | None, Decimal | None]:
        """العملة والسعر والمبلغ بالعملة (موجب) — أو ``(None, None, None)`` لو السطر بالعملة الأساسية."""
        code = str(line.currency_code or "").strip().upper() or None
        if code is None:
            return None, None, None
        rate = money(line.fx_rate) if line.fx_rate not in (None, "") else Decimal("0")
        if rate <= 0:
            raise BusinessError(f"«{name}»: سعر صرف {code} لازم أكبر من صفر.")
        fc = abs(money(line.amount_fc)) if line.amount_fc not in (None, "") else Decimal("0")
        return code, rate.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), fc

    @staticmethod
    def _check_account(g: dict[str, Any], account: dict[str, Any] | None) -> None:
        name = g["role_name"]
        if account is None:
            raise BusinessError(f"«{name}»: الحساب مش موجود (ممكن يكون اتمسح).")
        label = f"{account['code']} {account['name_ar']}"
        if not account["is_leaf"]:
            raise BusinessError(f"«{name}»: الحساب «{label}» رئيسي — القيد على الحسابات الفرعية بس.")
        if not account["is_active"]:
            raise BusinessError(f"«{name}»: الحساب «{label}» موقوف — نشّطه أو غيّر الحساب.")
        kind, party = account["party_kind"], g["party"]
        if kind and party is None:
            raise BusinessError(f"«{name}»: الحساب «{label}» حساب رقابة — لازم يتحدد {PARTY_LABELS[kind]}.")
        if party is not None and party[0] != kind:
            if kind is None:
                raise BusinessError(f"«{name}»: الحساب «{label}» مالوش طرف — {PARTY_LABELS[party[0]]} "
                                    "مايتكتبش عليه.")
            raise BusinessError(f"«{name}»: الحساب «{label}» طرفه {PARTY_LABELS[kind]} — مش {PARTY_LABELS[party[0]]}.")
        if account["requires_cost_center"] and not g["cost_center_id"]:
            raise BusinessError(f"«{name}»: الحساب «{label}» لازم يتكتب عليه مركز تكلفة.")

    @staticmethod
    def _balance(out: list[dict[str, Any]], line_count: int) -> None:
        """فرق التقريب الصغير على أكبر سطر في الجنب الناقص · الكبير ← خطأ."""
        debit = sum((g["amount"] for g in out if g["side"] == "dr"), Decimal("0"))
        credit = sum((g["amount"] for g in out if g["side"] == "cr"), Decimal("0"))
        diff = debit - credit
        if diff == 0:
            return
        tolerance = max(CENT, (HALF_CENT * max(1, line_count)).quantize(CENT, rounding=ROUND_CEILING))
        short = "cr" if diff > 0 else "dr"
        candidates = [g for g in out if g["side"] == short]
        if abs(diff) > tolerance or not candidates:
            raise BusinessError(f"القيد اللي طالع من المستند مش متوازن (المدين {fmt(debit)} ≠ الدائن {fmt(credit)}، "
                                f"الفرق {fmt(abs(diff))}) — فيه غلط في حسبة المستند؛ مايترحّلش.")
        biggest = max(candidates, key=lambda g: (g["amount"], -g["seen"]))
        biggest["amount"] += abs(diff)
        biggest["rounded"] = abs(diff)

    @staticmethod
    def _line(n: int, g: dict[str, Any]) -> dict[str, Any]:
        line = {"line_no": n, "role": g["role"], "role_name": g["role_name"], "account_id": g["account_id"],
                "description": g["description"],
                "debit": g["amount"] if g["side"] == "dr" else Decimal("0"),
                "credit": g["amount"] if g["side"] == "cr" else Decimal("0"),
                "cost_center_id": g["cost_center_id"], "project_id": g["project_id"],
                "rounding": g.get("rounded", Decimal("0")),
                "currency_code": g.get("currency_code"), "fx_rate": g.get("fx_rate"), "amount_fc": g.get("amount_fc"),
                **{col: None for col in PARTY_COLUMNS.values()}}
        if g["party"]:
            line[PARTY_COLUMNS[g["party"][0]]] = g["party"][1]
        return line

    # ---- الترحيل / إلغاء الترحيل (جوه transaction المستند) ------------------------------
    def _snapshot(self, tx: Executor, entry_id: int) -> dict[str, Any]:
        row = self.journal.get_entry(entry_id, ex=tx)
        row["lines"] = self.journal.entry_lines(entry_id, ex=tx)
        return row

    def _record(self, tx: Executor, action: str, entry_id: int, before: dict | None, after: dict | None) -> None:
        row = after or before or {}
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=entry_id, entity_label=ENTITY_LABEL,
                          record_label=f"{row.get('source_no') or ''} — {row.get('description') or ''}",
                          before={k: v for k, v in before.items() if k != "lines"} if before else None,
                          after={k: v for k, v in after.items() if k != "lines"} if after else None,
                          lines={"acc.journal_lines": {"before": (before or {}).get("lines") or [],
                                                       "after": (after or {}).get("lines") or []}},
                          extra={"source_type": row.get("source_type"), "source_id": row.get("source_id")})

    def post_document(self, tx: Executor, *, doc_type: str, source_id: int, source_no: str,
                      entry_date: dt.date, description: str, lines: list[PostingLine],
                      reference: str | None = None, cost_center_id: int | None = None,
                      project_id: int | None = None) -> int:
        """يعمل قيد المستند (أو يعدّل قيده القديم) ويرحّله ← رقم القيد. لازم يتنادى جوه ``db.atomic()`` المستند."""
        if isinstance(entry_date, dt.datetime):
            entry_date = entry_date.date()
        if not isinstance(entry_date, dt.date):
            raise BusinessError("تاريخ المستند مطلوب عشان قيده.")
        source_no = " ".join(str(source_no or "").split())[:40]
        if not source_no:
            raise BusinessError("رقم المستند مطلوب عشان قيده.")
        description = " ".join(str(description or "").split())[:500] or source_no
        reference = (" ".join(str(reference or "").split())[:100]) or None
        try:
            built = self.build(doc_type, lines, ex=tx, cost_center_id=cost_center_id, project_id=project_id)
            header = {"entry_date": entry_date, "description": description, "reference": reference,
                      "source_type": doc_type, "source_id": int(source_id), "source_no": source_no,
                      "user_id": SESSION.user_id}
            existing = self.repository.entry_for_source(doc_type, int(source_id), ex=tx)
            before = None
            if existing is None:
                entry_id = self.repository.insert_entry(header, ex=tx)
            else:
                entry_id = int(existing["id"])
                before = self._snapshot(tx, entry_id)
                if existing["status"] == "posted":
                    self.journal.set_status(entry_id, "draft", None, ex=tx)
                self.repository.update_header(entry_id, header, ex=tx)
            self.journal.replace_lines(entry_id, built, ex=tx)
            self.journal.set_status(entry_id, "posted", SESSION.user_id, ex=tx)
            self._record(tx, "post", entry_id, before, self._snapshot(tx, entry_id))
        except psycopg.errors.UniqueViolation as exc:
            raise BusinessError("المستند ده اترحّل من جهاز تاني في نفس اللحظة — دوس «تحديث» وجرّب تاني.") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return entry_id

    def unpost_document(self, tx: Executor, *, doc_type: str, source_id: int) -> bool:
        """إلغاء ترحيل المستند: قيده بيتمسح (السطور في سجل التعديلات) — ``False`` لو مالوش قيد."""
        self._doc(tx, doc_type)
        try:
            existing = self.repository.entry_for_source(doc_type, int(source_id), ex=tx)
            if existing is None:
                return False
            entry_id = int(existing["id"])
            before = self._snapshot(tx, entry_id)
            if existing["status"] == "posted":
                self.journal.set_status(entry_id, "draft", None, ex=tx)
            self.journal.delete_entry(entry_id, ex=tx)
            self._record(tx, "unpost", entry_id, before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return True

    def entry_for(self, doc_type: str, source_id: int) -> dict[str, Any] | None:
        """قيد المستند بسطوره (لزرار «عرض القيد» في شاشة المستند)."""
        entry_id = self.repository.entry_id_for_source(doc_type, int(source_id))
        return self._snapshot(self.db, entry_id) if entry_id else None
