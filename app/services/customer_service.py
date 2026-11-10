"""العملاء (شاشة «العملاء» — المرحلة 3أ، النموذج 1 اختيار المستخدم 2026-11-10).

ردود المستخدم (2026-11-10):
* **الكود** بيبدأ من 1 (ترقيم جديد) ويتولّد لوحده وقت الحفظ جوه قفل (``CustomerRepository.next_code``) وثابت.
* **رقم التسجيل الضريبي** اختياري، مايتكررش، ومن غير عدد أرقام معيّن — أرقام وحروف (التكرار بيتحسب من غير
  مسافات وشُرَط: ``party.normalize_tax_no``).
* **حد الائتمان تنبيه بس** (هيتعمل مع الفاتورة) · 0 = من غير حد.
* **المندوب** على الفاتورة بس — مفيش خانة مندوب على العميل.

* حساب الرقابة من المجموعة (حساب رقابة + بُعد الطرف — القرار 2) · افتراضي المجموعة بيتنسخ للعميل الجديد.
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version`` (جهازين على نفس العميل).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.customer_repository import STATUSES, CustomerRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "party.customers", "عميل"
PERM = "sales.customers"
MAX_TERMS = 365
MAX_LIMIT = Decimal("9999999999999999.99")
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_en", "الاسم EN", 200), ("contact_person", "المسؤول", 120), ("mobile", "الموبايل", 30),
          ("phone", "التليفون", 30), ("email", "الإيميل", 120), ("address", "العنوان", 300),
          ("notes", "الملاحظات", 500))


class CustomerService:
    def __init__(self, db: Database | None = None, repository: CustomerRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or CustomerRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_customers(self, keyword: str = "", status: str = "all", group_id: int | None = None,
                       page: int = 1, page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_customers(keyword.strip(), status, group_id, page_size, offset)

    def count_customers(self, keyword: str = "", status: str = "all", group_id: int | None = None) -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_customers(keyword.strip(), status, group_id)
        return {k: int(row[k]) for k in ("total", "active", "inactive", "no_tax")}

    def lookup_customers(self) -> list[dict[str, Any]]:
        """نافذة البحث F1: كل العملاء (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_customers("", "all", None, LOOKUP_LIMIT, 0)

    def get_customer(self, customer_id: int) -> dict[str, Any] | None:
        return self.repository.get_customer(int(customer_id))

    def group_choices(self) -> list[dict[str, Any]]:
        return self.repository.group_choices()

    def currency_choices(self) -> list[dict[str, Any]]:
        return self.repository.currency_choices()

    def tax_choices(self) -> dict[str, list[dict[str, Any]]]:
        return {"vat": self.repository.tax_choices("add"), "wht": self.repository.tax_choices("deduct")}

    @staticmethod
    def group_defaults(group: dict[str, Any]) -> dict[str, Any]:
        """اللي بيتنسخ من المجموعة للعميل الجديد."""
        return {"group_id": int(group["id"]), "payment_terms_days": int(group["payment_terms_days"]),
                "credit_limit": Decimal(group["credit_limit"] or 0), "currency_code": group["currency_code"],
                "vat_tax_code_id": group["vat_tax_code_id"], "wht_tax_code_id": group["wht_tax_code_id"]}

    def new_defaults(self) -> dict[str, Any]:
        """وضع «جديد»: الكود المقترح + المجموعة النظامية «عملاء — عام» (أو أول مجموعة نشطة) وافتراضيها."""
        groups = [g for g in self.repository.group_choices() if g["is_active"]]
        group = next((g for g in groups if g["is_system"]), groups[0] if groups else None)
        data: dict[str, Any] = {"code": self.repository.suggested_code(), "is_active": True}
        if group is not None:
            data.update(self.group_defaults(group))
        return data

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        return int(value) if value not in (None, "") else None

    @staticmethod
    def clean_tax_no(value: Any) -> str | None:
        """الرقم الضريبي زي ما اتكتب من غير مسافات زيادة (فاضي = None). أرقام وحروف ومن غير عدد معيّن."""
        text = " ".join(str(value or "").split())
        if not text:
            return None
        if len(text) > 30:
            raise BusinessError("«رقم التسجيل الضريبي» أطول من المسموح (30 حرف).")
        if not re.sub(r"[\s./-]", "", text):
            raise BusinessError("«رقم التسجيل الضريبي» لازم يبقى فيه أرقام أو حروف.")
        return text

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم العميل مطلوب.")
        if len(name) > 200:
            raise BusinessError("«الاسم» أطول من المسموح (200 حرف).")
        clean: dict[str, Any] = {"name_ar": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        if clean["email"] and not _EMAIL.match(clean["email"]):
            raise BusinessError("«الإيميل» مش مكتوب صح (مثال: name@company.com).")
        group = self._int_or_none(data.get("group_id"))
        if group is None:
            raise BusinessError("لازم تختار «المجموعة».")
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
        clean.update({"group_id": group, "tax_reg_no": self.clean_tax_no(data.get("tax_reg_no")),
                      "payment_terms_days": terms, "credit_limit": limit, "currency_code": currency,
                      "vat_tax_code_id": self._int_or_none(data.get("vat_tax_code_id")),
                      "wht_tax_code_id": self._int_or_none(data.get("wht_tax_code_id")),
                      "is_active": bool(data.get("is_active", True))})
        return clean

    def save_customer(self, customer_id: int | None, data: dict[str, Any],
                      expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if customer_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if customer_id is not None:
                before = self.repository.get_customer(int(customer_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=customer_id)
                if before is None:
                    raise BusinessError("العميل ده مش موجود (ممكن يكون اتمسح).")
                clean["code"] = before["code"]                       # الكود ثابت بعد الإضافة
            else:
                clean["code"] = self.repository.next_code(tx)        # جوه قفل الترقيم
            exclude = None if customer_id is None else int(customer_id)
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه عميل بنفس الاسم «{taken['name_ar']}» (كود {taken['code']}).")
            if clean["tax_reg_no"]:
                taken = self.repository.tax_taken(clean["tax_reg_no"], exclude, ex=tx)
                if taken:
                    raise BusinessError(f"رقم التسجيل الضريبي «{clean['tax_reg_no']}» متسجل قبل كده للعميل "
                                        f"«{taken['name_ar']}» (كود {taken['code']}).")
            if customer_id is None:
                customer_id = self.repository.insert_customer(clean, ex=tx)
            else:
                updated = self.repository.update_customer(int(customer_id), clean, ex=tx,
                                                          expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=customer_id)
            after = self.repository.get_customer(int(customer_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=customer_id, entity_label=ENTITY_LABEL,
                              record_label=f"{clean['code']} {clean['name_ar']}", before=before, after=after)
        return int(customer_id)

    def set_active(self, customer_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات (الموقوف مابيتختارش في مستند جديد)."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_customer(int(customer_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=customer_id)
            if before is None:
                raise BusinessError("العميل ده مش موجود (ممكن يكون اتمسح).")
            if bool(before["is_active"]) == bool(active):
                return
            updated = self.repository.set_active(int(customer_id), bool(active), ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=customer_id)
            after = self.repository.get_customer(int(customer_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=customer_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_customer(self, customer_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_customer(int(customer_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=customer_id)
            if before is None:
                raise BusinessError("العميل ده مش موجود (ممكن يكون اتمسح).")
            # TODO (الفواتير والسندات وأوامر التوريد والقيود): العميل اللي عليه حركة مايتمسحش — FK RESTRICT
            # من الجداول دي + رسالة عربي «عليه N فاتورة — وقّفه بدل ما تمسحه».
            deleted = self.repository.delete_customer(int(customer_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=customer_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=customer_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
