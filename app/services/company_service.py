"""بيانات الشركة (شاشة «بيانات الشركة» — المرحلة 1ب).

* سجل واحد بس (id = 1). أول حفظ بيضيفه، وبعد كده تعديل.
* الاسم بالعربي مطلوب · ض.ق.م الافتراضية من 0 لـ 100 · أول شهر في السنة المالية من 1 لـ 12.
* العملة الأساسية **EGP** ثابتة دلوقتي (قرار المستخدم) — هتبقى اختيار لما شاشة العملات تتعمل،
  وهتتقفل بعد أول قيد.
* اللوجو أقصى 2 ميجا، وبيتحفظ مع «حفظ».
* أي حفظ بيتسجل في ``sec.audit_log`` في نفس الـ transaction (من غير بايتات اللوجو).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.settings_repository import COMPANY_ID, SettingsRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY = "core.company_settings"

MAX_LOGO_BYTES = 2 * 1024 * 1024
BASE_CURRENCY = "EGP"
BASE_CURRENCY_NAME = "جنيه مصري"
DEFAULT_VAT_RATE = Decimal("14")
MONTHS_AR = ("يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر")

# (الحقل، أقصى طول، اسمه بالعربي للرسايل)
_TEXT_FIELDS = (
    ("name_ar", 200, "الاسم بالعربي"), ("name_en", 200, "الاسم بالإنجليزي"), ("activity", 200, "النشاط"),
    ("address", 300, "العنوان"), ("city", 100, "المدينة"), ("phone", 40, "التليفون"), ("mobile", 40, "الموبايل"),
    ("email", 120, "البريد"), ("website", 120, "الموقع"), ("commercial_register", 60, "السجل التجاري"),
    ("tax_registration_no", 60, "رقم التسجيل الضريبي"), ("tax_office", 120, "مأمورية الضرايب"),
    ("invoice_footer", 300, "سطر تحت الفواتير"),
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def default_company() -> dict[str, Any]:
    """القيم الافتراضية لو الشركة لسه ماتسجلتش."""
    return {"id": COMPANY_ID, **{f: None for f, _n, _a in _TEXT_FIELDS}, "logo_mime": None, "has_logo": False,
            "base_currency_code": BASE_CURRENCY, "default_vat_rate": DEFAULT_VAT_RATE,
            "fiscal_year_start_month": 1, "print_logo": True, "print_name_en": True,
            "created_at": None, "updated_at": None}


class CompanyService:
    def __init__(self, db: Database | None = None, repository: SettingsRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SettingsRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def get_company(self) -> dict[str, Any] | None:
        """بيانات الشركة (من غير اللوجو) أو None لو لسه ماتسجلتش."""
        return self.repository.get_company()

    def get_company_or_default(self) -> dict[str, Any]:
        return self.get_company() or default_company()

    def get_logo(self) -> tuple[bytes | None, str | None]:
        return self.repository.get_company_logo()

    # ---- تجهيز البيانات ----------------------------------------------------
    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for field, max_len, name in _TEXT_FIELDS:
            value = (data.get(field) or "").strip() or None
            if value and len(value) > max_len:
                raise BusinessError(f"«{name}» أطول من المسموح ({max_len} حرف).")
            clean[field] = value
        if not clean["name_ar"]:
            raise BusinessError("اسم الشركة بالعربي مطلوب.")
        if clean["email"] and not _EMAIL.match(clean["email"]):
            raise BusinessError("البريد مش مكتوب صح (مثال: info@example.com).")
        try:
            vat = Decimal(str(data.get("default_vat_rate", DEFAULT_VAT_RATE))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise BusinessError("نسبة ض.ق.م لازم تبقى رقم.") from exc
        if not Decimal("0") <= vat <= Decimal("100"):
            raise BusinessError("نسبة ض.ق.م لازم تبقى من 0 لـ 100.")
        clean["default_vat_rate"] = vat
        try:
            month = int(data.get("fiscal_year_start_month") or 1)
        except (TypeError, ValueError) as exc:
            raise BusinessError("أول شهر في السنة المالية لازم يبقى من 1 لـ 12.") from exc
        if not 1 <= month <= 12:
            raise BusinessError("أول شهر في السنة المالية لازم يبقى من 1 لـ 12.")
        clean["fiscal_year_start_month"] = month
        clean["print_logo"] = bool(data.get("print_logo", True))
        clean["print_name_en"] = bool(data.get("print_name_en", True))
        clean["base_currency_code"] = BASE_CURRENCY            # ثابتة دلوقتي (قرار المستخدم)
        if "logo" in data:
            logo = data.get("logo")
            if logo and len(logo) > MAX_LOGO_BYTES:
                raise BusinessError("اللوجو أكبر من 2 ميجا.")
            clean["logo"] = bytes(logo) if logo else None
            clean["logo_mime"] = data.get("logo_mime") if logo else None
        return clean

    # ---- حفظ ---------------------------------------------------------------
    def save_company(self, data: dict[str, Any], expected_version: int | None = None) -> None:
        """``expected_version``: الإصدار اللي الشاشة فتحت بيه البيانات (0 = لسه ماتسجلتش) — None = من غير تحقق."""
        PermissionService.require("sys.company.edit")
        PermissionService.require("sys.company.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = self.repository.get_company(ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=COMPANY_ID)
            if before is not None and "logo" in clean:
                logo_before, _mime = self.repository.get_company_logo(ex=tx)
                logo_changed = logo_before != clean["logo"]
            else:
                logo_changed = bool(clean.get("logo"))
            saved = self.repository.save_company(clean, ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, saved, expected_version, entity=ENTITY, entity_id=COMPANY_ID)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=COMPANY_ID, entity_label="بيانات الشركة", record_label=clean["name_ar"],
                              before=before, after=self.repository.get_company(ex=tx),
                              extra={"logo_changed": True} if logo_changed else None)
