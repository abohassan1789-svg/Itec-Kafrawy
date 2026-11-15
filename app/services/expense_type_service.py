"""أنواع المصروفات (شاشة «أنواع المصروفات» — المرحلة 3ج، النموذج 4 اختيار المستخدم 2026-11-15 + ردوده).

* **الكود** ``EXT-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``ExpenseTypeRepository.next_seq``) وثابت.
* **حساب النوع** (ثابت بعد الحفظ — إلا لو الحساب اتفرّع من الشجرة ← الأنواع بتنزل على «— عام»):
  - ``pick``: حساب مصروف فرعي نشط موجود من غير دور نظامي ومش تكلفة مبيعات. **كذا نوع على نفس الحساب مسموح.**
  - ``auto``: حساب فرعي جديد باسم النوع في **نفس الـ transaction** تحت حساب رئيسي من المصروفات (52 / 53 / 54…)
    — الكود الجاي تحته والباقي بيتورث من الأب.
  النوع الجديد تحت نوع تاني بياخد حساب أبوه افتراضياً («أختار حساب موجود»).
* **النوع بيتفرّع** (``parent_id``) لحد 5 مستويات: الأب نشط · مش تحت نفسه · الأب مايتوقفش وتحته أنواع نشطة ·
  اللي تحته أنواع مايتمسحش.
* أمر التوريد اختياري لكل الأنواع، ومفيش مركز تكلفة افتراضي ولا ض.ق.م على النوع (ردود المستخدم).
* النوع اللي اتعمل حسابه معاه: المسح بيمسح الحساب كمان (لو مفيش نوع تاني عليه).
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction (والحساب اللي اتعمل معاه) + ``row_version``.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, get_db
from app.repositories.account_repository import AccountRepository
from app.repositories.expense_type_repository import MAX_DEPTH, STATUSES, ExpenseTypeRepository, format_code
from app.services.account_service import AccountService, code_width, next_child_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "exp.expense_types", "نوع مصروف"
ACCOUNT_ENTITY, ACCOUNT_LABEL = "acc.accounts", "حساب"
PERM = "acc.expense_types"
LOOKUP_LIMIT = 5000
NOT_FOUND = "النوع ده مش موجود (ممكن يكون اتمسح)."
ACCOUNT_MODES = ("auto", "pick")
DEFAULT_PARENT_CODE = "52"                      # الحساب الجديد افتراضياً تحت «مصروفات عمومية وإدارية»
USAGE_NOTE = "المصروفات على النوع ده ومبالغها بتظهر مع شاشة المصروفات (المرحلة 9)."
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_en", "الاسم EN", 120), ("notes", "الملاحظات", 500))


def _label(row: dict[str, Any]) -> str:
    return f"«⁦{row['code']}⁩ {row['name_ar']}»"


class ExpenseTypeService:
    def __init__(self, db: Database | None = None, repository: ExpenseTypeRepository | None = None,
                 accounts: AccountRepository | None = None, audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or ExpenseTypeRepository(self.db)
        self.accounts = accounts or AccountRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_types(self, keyword: str = "", status: str = "all") -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        return self.repository.list_types(keyword.strip(), status, LOOKUP_LIMIT, 0)

    def lookup_types(self) -> list[dict[str, Any]]:
        """نافذة البحث F1 + «تصدير إكسل»: كل الأنواع (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_types("", "all", LOOKUP_LIMIT, 0)

    def get_type(self, type_id: int) -> dict[str, Any] | None:
        return self.repository.get_type(int(type_id))

    def chart_rows(self) -> list[dict[str, Any]]:
        """حسابات المصروفات في الدليل (للشجرة)."""
        return self.repository.chart_rows()

    def usable_accounts(self) -> list[dict[str, Any]]:
        return self.repository.usable_accounts()

    def account_parents(self) -> list[dict[str, Any]]:
        return self.repository.account_parents()

    def subtree_ids(self, type_id: int) -> list[int]:
        return self.repository.subtree_ids(int(type_id))

    def parent_choices(self, exclude_id: int | None = None) -> list[dict[str, Any]]:
        """الأنواع النشطة اللي ينفع تبقى أب (من غير النوع نفسه واللي تحته، واللي في المستوى الخامس)."""
        rows = self.repository.list_types("", "active", LOOKUP_LIMIT, 0)
        by_id = {r["id"]: r for r in rows}
        blocked = set(self.repository.subtree_ids(int(exclude_id))) if exclude_id is not None else set()

        def depth(row: dict) -> int:
            level, node = 1, row
            while node.get("parent_id") is not None and level < 50:
                node = by_id.get(node["parent_id"]) or {}
                level += 1
            return level

        return [r for r in rows if r["id"] not in blocked and depth(r) < MAX_DEPTH]

    def suggested_account(self, parent_account_id: Any) -> dict[str, Any] | None:
        """الحساب اللي هيتعمل لوحده (للعرض بس): ``{code, parent_code, parent_name}``."""
        if parent_account_id in (None, "", 0):
            return None
        parent = self.accounts.get_account(int(parent_account_id))
        if parent is None:
            return None
        return {"code": self._next_account_code(parent), "parent_code": parent["code"],
                "parent_name": parent["name_ar"]}

    def new_defaults(self, parent_type_id: Any = None) -> dict[str, Any]:
        """الافتراضي للنوع الجديد: تحت نوع ← حساب أبوه («أختار حساب موجود») · لوحده ← حساب جديد تحت 52."""
        parents = self.account_parents()
        default_parent = next((p["id"] for p in parents if p["code"] == DEFAULT_PARENT_CODE),
                              parents[0]["id"] if parents else None)
        defaults: dict[str, Any] = {"code": self.repository.suggested_code(), "is_active": True,
                                    "parent_id": None, "account_mode": "auto",
                                    "account_parent_id": default_parent, "account_id": None}
        if parent_type_id not in (None, "", 0):
            parent = self.repository.get_type(int(parent_type_id))
            if parent is not None:
                defaults.update(parent_id=parent["id"], account_mode="pick", account_id=parent["account_id"])
        return defaults

    # ---- التحقق --------------------------------------------------------------
    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم النوع مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم» أطول من المسموح (120 حرف).")
        clean: dict[str, Any] = {"name_ar": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        parent = data.get("parent_id")
        clean["parent_id"] = int(parent) if parent not in (None, "", 0) else None
        clean["is_active"] = bool(data.get("is_active", True))
        return clean

    def _check_parent(self, tx, clean: dict[str, Any], before: dict[str, Any] | None) -> None:
        parent_id = clean["parent_id"]
        if parent_id is None:
            return
        changed = before is None or before["parent_id"] != parent_id
        if not changed and not (clean["is_active"] and not before["is_active"]):
            return
        parent = self.repository.get_type(parent_id, ex=tx)
        if parent is None:
            raise BusinessError("النوع الأب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        if clean["is_active"] and not parent["is_active"]:
            raise BusinessError(f"النوع الأب {_label(parent)} موقوف — نشّطه الأول.")
        if before is not None and changed:
            if parent_id in self.repository.subtree_ids(int(before["id"]), ex=tx):
                raise BusinessError(f"مينفعش النوع «{before['name_ar']}» يبقى تحت نفسه أو تحت نوع متفرّع منه.")
        height = self.repository.height(int(before["id"]), ex=tx) if before is not None else 0
        if changed and self.repository.depth(parent_id, ex=tx) + 1 + height > MAX_DEPTH:
            raise BusinessError(f"أنواع المصروفات لحد {MAX_DEPTH} مستويات بس — اختار أب في مستوى أعلى.")

    # ---- حساب النوع -----------------------------------------------------------
    def _next_account_code(self, parent: dict[str, Any], ex=None) -> str:
        width = code_width(int(parent["level"]))
        used = self.accounts.codes_with_prefix(parent["code"], len(parent["code"]) + width, ex=ex)
        return next_child_code(parent["code"], int(parent["level"]), used)

    def _create_account(self, tx, parent_account_id: Any, clean: dict[str, Any]) -> int:
        """حساب فرعي جديد باسم النوع تحت الحساب الرئيسي المختار (جوه قفل الأب — جهازين مياخدوش نفس الكود)."""
        if parent_account_id in (None, "", 0):
            raise BusinessError("اختار الحساب الرئيسي اللي الحساب الجديد هيتعمل تحته (زي 52 أو 53).")
        parent = self.accounts.get_account(int(parent_account_id), ex=tx, for_update=True)
        if parent is None:
            raise BusinessError("الحساب الرئيسي ده مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        label = f"«⁦{parent['code']}⁩ {parent['name_ar']}»"
        if parent["is_leaf"] or parent["account_type"] != "expense":
            raise BusinessError(f"الحساب {label} مش حساب رئيسي من المصروفات.")
        if not parent["is_active"]:
            raise BusinessError(f"الحساب {label} موقوف — اختار حساب نشط.")
        if parent["role_code"] or parent["classification"] == "cogs":
            raise BusinessError(f"الحساب {label} حساب نظامي — اختار 52 أو 53 أو 54.")
        if int(parent["level"]) >= 5:
            raise BusinessError(f"الحساب {label} في آخر مستوى — مينفعش يتعمل تحته حساب.")
        code = self._next_account_code(parent, ex=tx)
        if not code:
            raise BusinessError(f"مفيش أكواد فاضية تحت {label} — اختار «حساب موجود».")
        data = AccountService._child_defaults(parent, code)
        data.update({"name_ar": clean["name_ar"], "name_en": clean["name_en"], "is_leaf": True,
                     "is_active": True, "role_code": None, "party_kind": None})
        account_id = self.accounts.insert_account(data, ex=tx)
        after = self.accounts.get_account(account_id, ex=tx)
        self.audit.record(tx, action="create", entity=ACCOUNT_ENTITY, entity_id=account_id,
                          entity_label=ACCOUNT_LABEL, record_label=f"{code} {clean['name_ar']}", after=after)
        return account_id

    def _pick_account(self, tx, account_id: Any) -> int:
        if account_id in (None, "", 0):
            raise BusinessError("اختار حساب المصروف من الدليل (أو «يتعمل حساب جديد»).")
        account = self.accounts.get_account(int(account_id), ex=tx)
        if account is None:
            raise BusinessError("الحساب ده مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        label = f"«⁦{account['code']}⁩ {account['name_ar']}»"
        if not account["is_leaf"]:
            raise BusinessError(f"الحساب {label} رئيسي — نوع المصروف لازم يبقى حسابه فرعي.")
        if account["account_type"] != "expense":
            raise BusinessError(f"الحساب {label} مش من المصروفات — اختار حساب تحت «5 المصروفات».")
        if account["classification"] == "cogs" or account["role_code"]:
            raise BusinessError(f"الحساب {label} حساب نظامي (بيتقيّد لوحده من المستندات) — اختار حساب مصروف تاني.")
        if not account["is_active"]:
            raise BusinessError(f"الحساب {label} موقوف — اختار حساب نشط.")
        return int(account["id"])

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_type(self, type_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if type_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        mode = data.get("account_mode") or "pick"
        if type_id is None and mode not in ACCOUNT_MODES:
            raise BusinessError("حساب النوع: «حساب موجود» أو «يتعمل حساب جديد».")
        with self.db.atomic() as tx:
            before = None
            if type_id is not None:
                before = self.repository.get_type(int(type_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=type_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
            clean = self._clean(data)
            exclude = None if type_id is None else int(type_id)
            if before is None:
                clean["seq"] = self.repository.next_seq(tx)           # جوه قفل الترقيم
            else:
                clean.update({k: before[k] for k in ("seq", "account_id", "owns_account")})   # ثابتين
                if not clean["is_active"] and before["is_active"]:
                    kids = self.repository.active_children(int(type_id), ex=tx)
                    if kids:
                        names = "، ".join(f"«{k['name_ar']}»" for k in kids)
                        raise BusinessError(f"النوع «{before['name_ar']}» تحته أنواع نشطة ({names}) — وقّفهم الأول.")
            self._check_parent(tx, clean, before)
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه نوع بنفس الاسم «{taken['name_ar']}» ({taken['code']}).")
            code = format_code(clean["seq"])
            if before is None:
                if mode == "auto":
                    clean["account_id"] = self._create_account(tx, data.get("account_parent_id"), clean)
                    clean["owns_account"] = True
                else:
                    clean["account_id"], clean["owns_account"] = self._pick_account(tx, data.get("account_id")), False
                type_id = self.repository.insert_type(clean, ex=tx)
            else:
                account_before = self.accounts.get_account(int(before["account_id"]), ex=tx)
                updated = self.repository.update_type(int(type_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=type_id)
                account_after = self.accounts.get_account(int(before["account_id"]), ex=tx)
                if account_after["row_version"] != account_before["row_version"]:     # الاسم اتغيّر معاه
                    self.audit.record(tx, action="update", entity=ACCOUNT_ENTITY, entity_id=before["account_id"],
                                      entity_label=ACCOUNT_LABEL,
                                      record_label=f"{account_after['code']} {account_after['name_ar']}",
                                      before=account_before, after=account_after)
            after = self.repository.get_type(int(type_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=type_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name_ar']}", before=before, after=after)
        return int(type_id)

    def set_active(self, type_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_type(int(type_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=type_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active:
                kids = self.repository.active_children(int(type_id), ex=tx)
                if kids:
                    names = "، ".join(f"«{k['name_ar']}»" for k in kids)
                    raise BusinessError(f"النوع «{before['name_ar']}» تحته أنواع نشطة ({names}) — وقّفهم الأول.")
            else:
                if before["parent_id"] is not None:
                    parent = self.repository.get_type(int(before["parent_id"]), ex=tx)
                    if parent is not None and not parent["is_active"]:
                        raise BusinessError(f"النوع الأب {_label(parent)} موقوف — نشّطه الأول.")
                if not before["account_active"]:
                    raise BusinessError(f"حساب النوع «⁦{before['account_code']}⁩ {before['account_name']}» موقوف — "
                                        "نشّطه من شجرة الحسابات الأول.")
            updated = self.repository.set_active(int(type_id), bool(active), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=type_id)
            after = self.repository.get_type(int(type_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=type_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_type(self, type_id: int, expected_version: int | None = None) -> None:
        """المسح: النوع + حسابه لو كان اتعمل معاه ومفيش نوع تاني عليه (في نفس الـ transaction)."""
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_type(int(type_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=type_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if before["children_count"]:
                raise BusinessError(f"النوع «{before['name_ar']}» تحته أنواع — امسحهم أو انقلهم لأب تاني الأول.")
            # TODO (المصروفات — المرحلة 9): النوع اللي عليه مصروف مايتمسحش — FK RESTRICT
            # + رسالة عربي «عليه مصروفات — وقّفه بدل ما تمسحه».
            deleted = self.repository.delete_type(int(type_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=type_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=type_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
            if before["owns_account"] and not self.repository.types_on_account(int(before["account_id"]), ex=tx):
                account = self.accounts.get_account(int(before["account_id"]), ex=tx, for_update=True)
                if account is not None and not account["has_children"]:
                    self.accounts.delete_account(int(account["id"]), ex=tx)
                    self.audit.record(tx, action="delete", entity=ACCOUNT_ENTITY, entity_id=account["id"],
                                      entity_label=ACCOUNT_LABEL,
                                      record_label=f"{account['code']} {account['name_ar']}", before=account)
