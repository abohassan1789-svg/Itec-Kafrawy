"""الحسابات الافتراضية (شاشة «الحسابات الافتراضية» — المرحلة 2، النموذج 9 «شريط الأقسام + تفاصيل الحساب»).

الفكرة (docs/01 10.1 + docs/03 9.10 — اختيار المستخدم 2026-11-09):
* جدول **مفتاح ← حساب** (``acc.default_accounts``): كل مستند بياخد حساباته من هنا بالمفتاح — **من غير أي
  بحث بالاسم**. المفاتيح ثابتة (29 في 7 أقسام) — المستخدم بيغيّر الحساب بس.
* الحساب لازم **فرعي نشط من النوع المسموح** للمفتاح (المبيعات = إيرادات…) — والقاعدة نفسها بتحمي ده
  (trigger ``acc.default_accounts_guard``)، وبتمنع مسح / إيقاف / تحويل الحساب الافتراضي لرئيسي.
* «اقترح من الدور» = أول حساب فرعي نشط بدور المفتاح (``suggested_account``).
* ``account_for(key)`` = اللي المستندات هتستخدمه (المرحلة 4 وبعدها): المفتاح الناقص ← رسالة عربي والمستند يترفض.
* كل حفظ بيتسجل في ``sec.audit_log`` في نفس الـ transaction، وبحماية التعديل في نفس الوقت (row_version لكل مفتاح).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.default_account_repository import DefaultAccountRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

PERM = "acc.default_accounts"
ENTITY, ENTITY_LABEL = "acc.default_accounts", "حساب افتراضي"
SCREEN_NAME = "الحسابات الافتراضية"

# القسم: (الاسم، الأيقونة) — بنفس ترتيب الشريط الجانبي.
GROUPS: dict[str, tuple[str, str]] = {
    "sales": ("المبيعات", "🛒"),
    "purch": ("المشتريات", "📦"),
    "inv": ("المخازن", "🏬"),
    "cash": ("الخزينة والبنوك", "💰"),
    "tax": ("الضرايب", "🧾"),
    "fx": ("العملات", "💱"),
    "close": ("الافتتاح والإقفال", "📘"),
}
TYPE_NAMES = {"asset": "الأصول", "liability": "الخصوم", "equity": "حقوق الملكية", "revenue": "الإيرادات",
              "expense": "المصروفات"}


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def account_label(row: dict[str, Any]) -> str:
    """«4101 مبيعات بضاعة» أو "" لو المفتاح ناقص."""
    if not row.get("account_id"):
        return ""
    return f"{ltr(row['account_code'])} {row['account_name']}"


class DefaultAccountService:
    def __init__(self, db: Database | None = None, repository: DefaultAccountRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or DefaultAccountRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_keys(self, group_code: str | None = None, keyword: str = "") -> list[dict[str, Any]]:
        """مفاتيح القسم — ولو فيه كلمة بحث: في كل الأقسام."""
        keyword = (keyword or "").strip()
        return self.repository.list_keys(None if keyword else group_code, keyword)

    def get_key(self, key: str) -> dict[str, Any] | None:
        return self.repository.get_key(str(key))

    def group_summary(self) -> dict[str, dict[str, int]]:
        """لكل قسم: عدد المفاتيح والناقص (للشريط الجانبي)."""
        out = {g: {"total": 0, "missing": 0} for g in GROUPS}
        for r in self.repository.group_counts():
            out[r["group_code"]] = {"total": int(r["total"]), "missing": int(r["missing"])}
        return out

    def account_choices(self, account_type: str) -> list[dict[str, Any]]:
        return self.repository.account_choices(account_type)

    def suggested_account(self, key: str) -> dict[str, Any] | None:
        """اقتراح الدور: أول حساب فرعي نشط بدور المفتاح (None لو المفتاح مالوش دور أو مفيش حساب)."""
        row = self.repository.get_key(str(key))
        if row is None or not row["role_code"]:
            return None
        return self.repository.role_account(row["role_code"], row["account_type"])

    def account_for(self, key: str, ex: Executor | None = None) -> int:
        """رقم الحساب الافتراضي للمفتاح ده (اللي المستندات بتقيّد عليه). الناقص ← رسالة والمستند يترفض."""
        row = self.repository.get_key(str(key), ex=ex)
        if row is None:
            raise BusinessError(f"المفتاح «{key}» مش معروف في الحسابات الافتراضية.")
        if not row["account_id"]:
            raise BusinessError(f"الحساب الافتراضي «{row['name_ar']}» مش متحدد — حدده من شاشة «{SCREEN_NAME}».")
        if not row["account_active"] or not row["account_leaf"]:
            raise BusinessError(f"الحساب الافتراضي «{row['name_ar']}» ({account_label(row)}) لازم يبقى فرعي نشط "
                                f"— غيّره من شاشة «{SCREEN_NAME}».")
        return int(row["account_id"])

    # ---- حفظ ---------------------------------------------------------------
    def save_key(self, key: str, account_id: Any, expected_version: int | None = None) -> str:
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if account_id in (None, ""):
            raise BusinessError("اختار الحساب الافتراضي.")
        account_id = int(account_id)
        with self.db.atomic() as tx:
            before = self.repository.get_key(str(key), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=key)
            if before is None:
                raise BusinessError("المفتاح ده مش موجود.")
            if account_id != before["account_id"]:
                allowed = {int(r["id"]): r for r in self.repository.account_choices(before["account_type"], ex=tx)}
                if account_id not in allowed:
                    raise BusinessError(f"«{before['name_ar']}» محتاج حساب فرعي نشط من "
                                        f"«{TYPE_NAMES[before['account_type']]}».")
            updated = self.repository.update_account(before["key"], account_id, ex=tx,
                                                     expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=key)
            after = self.repository.get_key(before["key"], ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=before["key"], entity_label=ENTITY_LABEL,
                              record_label=before["name_ar"], before=before, after=after)
        return str(key)
