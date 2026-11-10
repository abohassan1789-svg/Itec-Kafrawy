"""مجموعات العملاء (شاشة «مجموعات العملاء» — المرحلة 3أ، النموذج 1 اختيار المستخدم 2026-11-10).

* كل عميل (الشاشة الجاية) بيبقى في مجموعة، والمجموعة بتحدد **حساب الرقابة** + **الافتراضي** اللي بيتنسخ للعميل
  الجديد (مدة السداد، حد الائتمان، العملة، ض.ق.م، خصم المنبع). تغيير الافتراضي بيأثر على العملاء الجداد بس.
* الكود أرقام بيتولّد لوحده وقت الحفظ جوه قفل (``CustomerGroupRepository.next_code``) وثابت بعد الإضافة.
* «عملاء — عام» نظامية: مابتتمسحش ولا بتتوقف (القاعدة بتمنع — سكريبت 0014).
* كل حفظ / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version`` (جهازين على نفس المجموعة).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.customer_group_repository import CustomerGroupRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "party.customer_groups", "مجموعة عملاء"
PERM = "sales.customer_groups"
MAX_TERMS = 365
MAX_LIMIT = Decimal("9999999999999999.99")


def fmt_money(value: Any) -> str:
    return f"{Decimal(value or 0):,.2f}"


def limit_text(value: Any, currency: str = "") -> str:
    """حد الائتمان للعرض: 0 = من غير حد."""
    if not Decimal(value or 0):
        return "من غير حد"
    return f"{fmt_money(value)} {currency}".strip()


class CustomerGroupService:
    def __init__(self, db: Database | None = None, repository: CustomerGroupRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or CustomerGroupRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_groups(self, keyword: str = "") -> list[dict[str, Any]]:
        return self.repository.list_groups(keyword.strip())

    def get_group(self, group_id: int) -> dict[str, Any] | None:
        return self.repository.get_group(int(group_id))

    def account_choices(self) -> list[dict[str, Any]]:
        return self.repository.account_choices()

    def currency_choices(self) -> list[dict[str, Any]]:
        return self.repository.currency_choices()

    def tax_choices(self) -> dict[str, list[dict[str, Any]]]:
        return {"vat": self.repository.tax_choices("add"), "wht": self.repository.tax_choices("deduct")}

    def new_defaults(self) -> dict[str, Any]:
        """الخانات في وضع «جديد»: الكود المقترح + حساب «العملاء» الافتراضي + العملة الأساسية + ض.ق.م 14% + 30 يوم."""
        vat = next((t for t in self.repository.tax_choices("add") if t["code"] == "VAT14"), None)
        return {"code": self.repository.suggested_code(), "control_account_id": self.repository.default_account_id(),
                "payment_terms_days": 30, "credit_limit": Decimal("0"),
                "currency_code": self.repository.base_currency(),
                "vat_tax_code_id": int(vat["id"]) if vat else None, "wht_tax_code_id": None, "is_active": True}

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {"total": len(rows), "inactive": sum(1 for r in rows if not r["is_active"]),
                "accounts": len({r["control_account_id"] for r in rows})}

    # ---- حفظ / مسح ---------------------------------------------------------
    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        return int(value) if value not in (None, "") else None

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = (data.get("name_ar") or "").strip()
        if not name:
            raise BusinessError("اسم المجموعة مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم» أطول من المسموح (120 حرف).")
        name_en = (data.get("name_en") or "").strip() or None
        if name_en and len(name_en) > 120:
            raise BusinessError("«الاسم EN» أطول من المسموح (120 حرف).")
        account = self._int_or_none(data.get("control_account_id"))
        if account is None:
            raise BusinessError("لازم تختار «حساب الرقابة» (1103 العملاء أو حساب فرعي منه).")
        try:
            terms = int(data.get("payment_terms_days") if data.get("payment_terms_days") not in (None, "") else 0)
        except (TypeError, ValueError) as exc:
            raise BusinessError("«مدة السداد» لازم تبقى رقم أيام.") from exc
        if not 0 <= terms <= MAX_TERMS:
            raise BusinessError(f"«مدة السداد» لازم تبقى من 0 لـ {MAX_TERMS} يوم (0 = نقدي).")
        try:
            limit = Decimal(str(data.get("credit_limit") if data.get("credit_limit") not in (None, "") else 0))
            limit = limit.quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise BusinessError("«حد الائتمان» لازم يبقى رقم.") from exc
        if limit < 0 or limit > MAX_LIMIT:
            raise BusinessError("«حد الائتمان» لازم يبقى صفر أو أكبر (صفر = من غير حد).")
        currency = (data.get("currency_code") or "").strip().upper()
        if len(currency) != 3:
            raise BusinessError("لازم تختار العملة.")
        notes = (data.get("notes") or "").strip() or None
        if notes and len(notes) > 500:
            raise BusinessError("«الملاحظات» أطول من المسموح (500 حرف).")
        return {"name_ar": name, "name_en": name_en, "control_account_id": account, "payment_terms_days": terms,
                "credit_limit": limit, "currency_code": currency,
                "vat_tax_code_id": self._int_or_none(data.get("vat_tax_code_id")),
                "wht_tax_code_id": self._int_or_none(data.get("wht_tax_code_id")),
                "notes": notes, "is_active": bool(data.get("is_active", True))}

    def save_group(self, group_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if group_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if group_id is not None:
                before = self.repository.get_group(int(group_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=group_id)
                if before is None:
                    raise BusinessError("المجموعة دي مش موجودة (ممكن تكون اتمسحت).")
                clean["code"] = before["code"]                       # الكود ثابت بعد الإضافة
                if before["is_system"] and not clean["is_active"]:
                    raise BusinessError(f"المجموعة «{before['name_ar']}» نظامية (الافتراضية لأي عميل جديد) — "
                                        "مابتتوقفش.")
            else:
                clean["code"] = self.repository.next_code(tx)        # جوه قفل الترقيم
            taken = self.repository.name_taken(clean["name_ar"], None if group_id is None else int(group_id), ex=tx)
            if taken:
                raise BusinessError(f"فيه مجموعة بنفس الاسم «{taken['name_ar']}» (كود {taken['code']}).")
            if group_id is None:
                group_id = self.repository.insert_group(clean, ex=tx)
            else:
                updated = self.repository.update_group(int(group_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=group_id)
            after = self.repository.get_group(int(group_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=group_id, entity_label=ENTITY_LABEL,
                              record_label=f"{clean['code']} {clean['name_ar']}", before=before, after=after)
        return int(group_id)

    def delete_group(self, group_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_group(int(group_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=group_id)
            if before is None:
                raise BusinessError("المجموعة دي مش موجودة (ممكن تكون اتمسحت).")
            if before["is_system"]:
                raise BusinessError(f"المجموعة «{before['name_ar']}» نظامية (الافتراضية لأي عميل جديد) — "
                                    "مابتتمسحش.")
            # TODO (شاشة العملاء): المجموعة اللي فيها عملاء مابتتمسحش — FK RESTRICT من party.customers
            # (رسالة عربي «فيها N عميل — انقلهم أو وقّف المجموعة»).
            deleted = self.repository.delete_group(int(group_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=group_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=group_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
