"""العملات وأسعار الصرف (شاشة «العملات وأسعار الصرف» — المرحلة 1ب).

قرارات المستخدم: تعدد عملات كامل · الجنيه المصري **EGP عملة أساسية واحدة بس** (ثابتة).
* العملة: الكود (3 حروف إنجليزي كابيتال، ثابت بعد الإضافة)، الاسم ع/E، الرمز، الكسور 0–6،
  اسم الكسر (للتفقيط)، نشطة/موقوفة. الأساسية: مابتتوقفش ولا بتتمسح.
* **أسعار الصرف بالتاريخ**: سعر واحد لكل عملة في اليوم (بالجنيه)، أكبر من صفر. التسجيل في نفس اليوم
  تاني = استبدال (محتاج صلاحية «تعديل»). أي مستند بياخد **آخر سعر في تاريخه أو قبله** (``rate_on``).
* «💲 تسجيل سعر» بصلاحية «إضافة» على الشاشة · مسح سعر بصلاحية «حذف».
* مسح عملة بيمسح أسعارها معاها (المستندات اللي هتتعمل بعد كده هتمنع مسح عملة عليها حركة).
* كل حفظ/مسح بيتسجل في ``sec.audit_log`` في نفس الـ transaction.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.currency_repository import CurrencyRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

CURRENCY_ENTITY, RATE_ENTITY = "core.currencies", "core.exchange_rates"

BIG_CHANGE_PERCENT = Decimal("5")        # تغيير أكبر من كده ← الشاشة بتسأل تتأكد (غلط كتابة؟)
RATE_PLACES = Decimal("0.000001")
_CODE = re.compile(r"^[A-Z]{3}$")


def fmt_rate(value: Any) -> str:
    """السعر بـ 4 أرقام عشرية على الأقل (ولحد 6 لو فيه)."""
    if value is None:
        return ""
    text = f"{Decimal(value).quantize(RATE_PLACES):f}".rstrip("0")
    whole, _dot, frac = text.partition(".")
    return f"{int(whole):,}.{frac.ljust(4, '0')}"


def fmt_amount(value: Any, decimals: int = 2) -> str:
    return f"{Decimal(value):,.{decimals}f}"


def change_percent(rate: Decimal, previous: Decimal | None) -> Decimal | None:
    if previous in (None, 0):
        return None
    return ((Decimal(rate) - Decimal(previous)) / Decimal(previous) * 100).quantize(Decimal("0.01"))


class CurrencyService:
    def __init__(self, db: Database | None = None, repository: CurrencyRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or CurrencyRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_currencies(self, keyword: str = "") -> list[dict[str, Any]]:
        return self.repository.list_currencies(keyword.strip())

    def get_currency(self, currency_id: int) -> dict[str, Any] | None:
        return self.repository.get_currency(int(currency_id))

    def base_currency(self) -> dict[str, Any] | None:
        return self.repository.base_currency()

    def list_rates(self, currency_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return self.repository.list_rates(int(currency_id), limit)

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        base = next((r["code"] for r in rows if r["is_base"]), "EGP")
        return {"total": len(rows), "active": sum(1 for r in rows if r["is_active"]), "base": base}

    def rate_on(self, code: str, on_date: date) -> Decimal | None:
        """سعر العملة بالجنيه في تاريخ = آخر سعر في اليوم ده أو قبله (الأساسية = 1)."""
        return self.repository.rate_on(code, on_date)

    def convert(self, currency_id: int, amount: Any, on_date: date) -> dict[str, Any]:
        """المحوّل: المبلغ بالجنيه + السعر اللي اتاخد وتاريخه (نفس طريقة المستندات)."""
        currency = self.repository.get_currency(int(currency_id))
        if currency is None:
            raise BusinessError("العملة دي مش موجودة.")
        amount = Decimal(str(amount or 0))
        if currency["is_base"]:
            return {"rate": Decimal(1), "rate_date": None, "amount_base": amount, "is_base": True}
        found = self.repository.rate_before(int(currency_id), on_date)
        if found is None:
            return {"rate": None, "rate_date": None, "amount_base": None, "is_base": False}
        return {"rate": found["rate"], "rate_date": found["rate_date"], "is_base": False,
                "amount_base": (amount * found["rate"]).quantize(Decimal("0.01"))}

    def rate_context(self, currency_id: int, rate_date: date) -> dict[str, Any]:
        """قبل تسجيل سعر: السعر اللي قبل التاريخ ده، والسعر المتسجل في نفس اليوم (لو فيه)."""
        return {"previous": self.repository.rate_before(int(currency_id), rate_date, inclusive=False),
                "same_day": self.repository.rate_on_day(int(currency_id), rate_date)}

    # ---- العملات -----------------------------------------------------------
    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        code = (data.get("code") or "").strip().upper()
        if not _CODE.match(code):
            raise BusinessError("كود العملة لازم يبقى 3 حروف إنجليزي (مثال: USD).")
        name = (data.get("name_ar") or "").strip()
        if not name:
            raise BusinessError("اسم العملة مطلوب.")
        clean: dict[str, Any] = {"code": code, "name_ar": name}
        for field, max_len, label in (("name_ar", 60, "الاسم"), ("name_en", 60, "الاسم بالإنجليزي"),
                                      ("symbol", 10, "الرمز"), ("fraction_name", 30, "اسم الكسر")):
            value = (data.get(field) or "").strip() or None
            if value and len(value) > max_len:
                raise BusinessError(f"«{label}» أطول من المسموح ({max_len} حرف).")
            clean[field] = value
        try:
            decimals = int(data.get("decimals", 2))
        except (TypeError, ValueError) as exc:
            raise BusinessError("الكسور لازم تبقى رقم من 0 لـ 6.") from exc
        if not 0 <= decimals <= 6:
            raise BusinessError("الكسور لازم تبقى رقم من 0 لـ 6.")
        clean["decimals"] = decimals
        clean["is_active"] = bool(data.get("is_active", True))
        return clean

    def save_currency(self, currency_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require("sys.currencies.create" if currency_id is None else "sys.currencies.edit")
        PermissionService.require("sys.currencies.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if currency_id is not None:
                before = self.repository.get_currency(int(currency_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=CURRENCY_ENTITY, entity_id=currency_id)
                if before is None:
                    raise BusinessError("العملة دي مش موجودة (ممكن تكون اتمسحت).")
                clean["code"] = before["code"]                     # الكود ثابت بعد الإضافة
                if before["is_base"] and not clean["is_active"]:
                    raise BusinessError("العملة الأساسية مينفعش تتوقف.")
            elif self.repository.code_exists(clean["code"], ex=tx):
                raise BusinessError(f"العملة «{clean['code']}» متسجلة قبل كده.")
            if currency_id is None:
                currency_id = self.repository.insert_currency(clean, ex=tx)
            else:
                updated = self.repository.update_currency(int(currency_id), clean, ex=tx,
                                                          expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=CURRENCY_ENTITY, entity_id=currency_id)
            after = self.repository.get_currency(int(currency_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity="core.currencies",
                              entity_id=currency_id, entity_label="عملة", record_label=clean["code"],
                              before=_audit_view(before), after=_audit_view(after))
        return int(currency_id)

    def delete_currency(self, currency_id: int, expected_version: int | None = None) -> None:
        PermissionService.require("sys.currencies.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_currency(int(currency_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=CURRENCY_ENTITY, entity_id=currency_id)
            if before is None:
                raise BusinessError("العملة دي مش موجودة (ممكن تكون اتمسحت).")
            if before["is_base"]:
                raise BusinessError(f"العملة الأساسية {before['code']} مينفعش تتمسح.")
            deleted = self.repository.delete_currency(int(currency_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=CURRENCY_ENTITY, entity_id=currency_id)
            self.audit.record(tx, action="delete", entity="core.currencies", entity_id=currency_id,
                              entity_label="عملة", record_label=before["code"], before=_audit_view(before),
                              extra={"rates_deleted": int(before["rates_count"])} if before["rates_count"] else None)

    # ---- أسعار الصرف -------------------------------------------------------
    @staticmethod
    def _clean_rate(rate: Any) -> Decimal:
        try:
            value = Decimal(str(rate)).quantize(RATE_PLACES)
        except (InvalidOperation, ValueError) as exc:
            raise BusinessError("السعر لازم يبقى رقم.") from exc
        if value <= 0:
            raise BusinessError("السعر لازم يبقى أكبر من صفر.")
        if value >= Decimal("1000000000000"):
            raise BusinessError("السعر أكبر من المسموح.")
        return value

    def save_rate(self, currency_id: int, rate_date: date, rate: Any, expected_version: int | None = None) -> int:
        """تسجيل سعر (أو استبدال سعر نفس اليوم — محتاج «تعديل»). بيرجع id السعر.

        ``expected_version`` = إصدار سعر اليوم ده اللي النافذة شافته (0 = مكانش فيه سعر) — لو جهاز تاني
        سجّل أو غيّر سعر نفس اليوم في الوقت ده ← تعارض بدل ما يستبدله من غير ما يعرف.
        """
        PermissionService.require("sys.currencies.create")
        if not isinstance(rate_date, date):
            raise BusinessError("تاريخ السعر مطلوب.")
        value = self._clean_rate(rate)
        with self.db.atomic() as tx:
            currency = self.repository.get_currency(int(currency_id), ex=tx, for_update=True)
            if currency is None:
                raise BusinessError("العملة دي مش موجودة (ممكن تكون اتمسحت).")
            if currency["is_base"]:
                raise BusinessError(f"العملة الأساسية {currency['code']} سعرها دايماً 1 — مالهاش أسعار صرف.")
            if not currency["is_active"]:
                raise BusinessError(f"العملة {currency['code']} موقوفة — فعّلها الأول.")
            before = self.repository.rate_on_day(int(currency_id), rate_date, ex=tx)   # العملة مقفولة FOR UPDATE
            self.guard.check(tx, before, expected_version, entity=RATE_ENTITY,
                             entity_id=before["id"] if before else "")
            if before is not None:
                PermissionService.require("sys.currencies.edit")
            rate_id = self.repository.upsert_rate(int(currency_id), rate_date, value, SESSION.user_id, ex=tx)
            label = f"{currency['code']} {rate_date:%d/%m/%Y}"
            self.audit.record(tx, action="create" if before is None else "update", entity="core.exchange_rates",
                              entity_id=rate_id, entity_label="سعر صرف", record_label=label,
                              before=before, after=self.repository.get_rate(rate_id, ex=tx))
        return rate_id

    def delete_rate(self, rate_id: int, expected_version: int | None = None) -> None:
        PermissionService.require("sys.currencies.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_rate(int(rate_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=RATE_ENTITY, entity_id=rate_id)
            if before is None:
                raise BusinessError("السعر ده مش موجود (ممكن يكون اتمسح).")
            currency = self.repository.get_currency(int(before["currency_id"]), ex=tx)
            deleted = self.repository.delete_rate(int(rate_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=RATE_ENTITY, entity_id=rate_id)
            self.audit.record(tx, action="delete", entity="core.exchange_rates", entity_id=rate_id,
                              entity_label="سعر صرف",
                              record_label=f"{currency['code'] if currency else ''} {before['rate_date']:%d/%m/%Y}",
                              before=before)


def _audit_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """بيانات العملة نفسها بس في سجل التعديلات (من غير آخر سعر والعدد)."""
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in ("last_rate", "last_rate_date", "prev_rate", "rates_count")}
