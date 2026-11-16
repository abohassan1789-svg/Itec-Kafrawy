"""أنواع الأصناف «الأقسام» (شاشة «أنواع الأصناف» — المرحلة 3د، النموذج 2 «شجرة» اختيار المستخدم 2026-11-16).

* **الكود** ``CAT-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``ItemCategoryRepository.next_seq``) وثابت.
* **شجرة** (``parent_id``) لحد 5 مستويات — رد المستخدم: «ممكن نوع تحته أنواع، وممكن نوع فرعي أفرّع منه برضه»:
  الأب نشط · مش تحت نفسه · مايتوقفش وتحته أنواع نشطة · اللي تحته أنواع مايتمسحش.
* **الاسم** مطلوب ومتنضف (زي الماركات) ومايتكررش **تحت نفس الأب** (bms = BMS) · الاسم العربي اختياري.
* **4 حسابات اختيارية** (المخزون · المبيعات · تكلفة المبيعات · مردودات المبيعات): **الفاضي بيتورث من الأب**
  (أقرب أب عنده الحساب) ولو مفيش ← **الحساب الافتراضي** (``resolve_accounts`` — ونفس الحسبة في القاعدة
  ``inv.item_category_account`` للفواتير).
* **«الأصناف الجديدة خدمية»**: الصنف الجديد تحت النوع ده بيطلع خدمي افتراضياً (النوع الفرعي الجديد بياخدها من أبوه).
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version``.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, get_db
from app.repositories.account_repository import AccountRepository
from app.repositories.item_category_repository import (
    ACCOUNT_KINDS,
    KIND_COLUMN,
    KIND_LABEL,
    MAX_DEPTH,
    STATUSES,
    ItemCategoryRepository,
    format_code,
)
from app.services.audit_service import AuditService
from app.services.brand_service import clean_name
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "inv.item_categories", "نوع أصناف"
PERM = "inv.item_categories"
LOOKUP_LIMIT = 5000
NOT_FOUND = "النوع ده مش موجود (ممكن يكون اتمسح)."
USAGE_NOTE = "عدد الأصناف على كل نوع بيظهر مع شاشة الأصناف."
KINDS = tuple(k for k, _col, _l, _c in ACCOUNT_KINDS)
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_ar", "الاسم العربي", 120), ("notes", "الملاحظات", 500))
# سبب رفض الحساب لكل نوع (نفس شروط inv.item_category_account_error في القاعدة)
_KIND_HINT = {
    "inventory": "مش حساب مخزون — اختار حساب تحت «1105 المخزون»",
    "sales": "مش حساب مبيعات — اختار حساب تحت «41 إيرادات النشاط»",
    "cogs": "مش حساب تكلفة مبيعات — اختار حساب تحت «51 تكلفة المبيعات»",
    "sales_ret": "مش حساب مردودات مبيعات — اختار حساب تحت «42 مردودات المبيعات»",
}


def _label(row: dict[str, Any]) -> str:
    return f"«⁦{row['code']}⁩ {row['name']}»"


def account_fits(account: dict[str, Any], kind: str) -> bool:
    """الحساب من النوع الصح؟ (من غير فرعي / نشط)."""
    t, cls, role = account.get("account_type"), account.get("classification"), account.get("role_code")
    return {"inventory": t == "asset" and role == "inventory",
            "sales": t == "revenue" and cls == "operating_rev",
            "cogs": t == "expense" and cls == "cogs",
            "sales_ret": t == "revenue" and cls == "returns"}[kind]


def full_path(row: dict[str, Any], by_id: dict[Any, dict]) -> str:
    """«Panel / ABB» — الاسم بكل الآباء."""
    names, node, guard = [row["name"]], row, 0
    while node.get("parent_id") is not None and guard < 50:
        node = by_id.get(node["parent_id"]) or {}
        if not node:
            break
        names.append(node["name"])
        guard += 1
    return " / ".join(reversed(names))


class ItemCategoryService:
    def __init__(self, db: Database | None = None, repository: ItemCategoryRepository | None = None,
                 accounts: AccountRepository | None = None, audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or ItemCategoryRepository(self.db)
        self.accounts = accounts or AccountRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_categories(self, keyword: str = "", status: str = "all") -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        return self.repository.list_categories(keyword.strip(), status, LOOKUP_LIMIT, 0)

    def lookup_categories(self) -> list[dict[str, Any]]:
        """الشجرة + نافذة البحث F1 + «تصدير إكسل»: كل الأنواع (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_categories("", "all", LOOKUP_LIMIT, 0)

    def get_category(self, category_id: int) -> dict[str, Any] | None:
        return self.repository.get_category(int(category_id))

    def subtree_ids(self, category_id: int) -> list[int]:
        return self.repository.subtree_ids(int(category_id))

    def account_choices(self) -> dict[str, list[dict[str, Any]]]:
        """الحسابات اللي تنفع لكل نوع من الأربعة."""
        return {kind: self.repository.account_choices(kind) for kind in KINDS}

    def default_accounts(self) -> dict[str, dict]:
        return self.repository.default_accounts()

    def parent_choices(self, exclude_id: int | None = None) -> list[dict[str, Any]]:
        """الأنواع النشطة اللي ينفع تبقى أب (من غير النوع نفسه واللي تحته، واللي في المستوى الخامس)."""
        rows = self.repository.list_categories("", "active", LOOKUP_LIMIT, 0)
        by_id = {r["id"]: r for r in rows}
        blocked = set(self.repository.subtree_ids(int(exclude_id))) if exclude_id is not None else set()

        def depth(row: dict) -> int:
            level, node = 1, row
            while node.get("parent_id") is not None and level < 50:
                node = by_id.get(node["parent_id"]) or {}
                level += 1
            return level

        out = []
        for r in rows:
            if r["id"] in blocked or depth(r) >= MAX_DEPTH:
                continue
            out.append({**r, "path": full_path(r, by_id)})
        return sorted(out, key=lambda r: r["path"].lower())

    def new_defaults(self, parent_id: Any = None) -> dict[str, Any]:
        """النوع الجديد: تحت نوع ← «الأصناف الجديدة خدمية» زي أبوه، والحسابات فاضية (بتتورث منه)."""
        defaults: dict[str, Any] = {"code": self.repository.suggested_code(), "is_active": True, "parent_id": None,
                                    "new_items_service": False}
        if parent_id not in (None, "", 0):
            parent = self.repository.get_category(int(parent_id))
            if parent is not None:
                defaults.update(parent_id=parent["id"], new_items_service=bool(parent["new_items_service"]))
        return defaults

    @staticmethod
    def resolve_accounts(own: dict[str, Any], parent_id: Any, rows: list[dict[str, Any]],
                         defaults: dict[str, dict]) -> dict[str, dict[str, Any]]:
        """الحساب الفعلي لكل نوع من الأربعة: ``{kind: {account_id, code, name, source, source_name}}``.

        ``own``: حسابات النوع نفسه (``{kind: account_id|None}`` أو صف النوع) — ``source``: ``own`` · ``parent``
        (``source_name`` = اسم الأب اللي جه منه) · ``default`` · ``none`` (حتى الافتراضي فاضي).
        """
        by_id = {r["id"]: r for r in rows}
        out: dict[str, dict[str, Any]] = {}
        for kind in KINDS:
            col = KIND_COLUMN[kind]
            value = own.get(kind, own.get(col))
            if value not in (None, "", 0):
                out[kind] = {"account_id": int(value), "code": own.get(f"{kind}_code"),
                             "name": own.get(f"{kind}_name"), "source": "own", "source_name": None}
            else:
                node, found, guard = by_id.get(parent_id), None, 0
                while node is not None and guard < 50:
                    if node.get(col) is not None:
                        found = node
                        break
                    node = by_id.get(node.get("parent_id"))
                    guard += 1
                if found is not None:
                    out[kind] = {"account_id": int(found[col]), "source": "parent", "source_name": found["name"],
                                 "code": found.get(f"{kind}_code"), "name": found.get(f"{kind}_name")}
                else:
                    d = defaults.get(kind) or {}
                    out[kind] = {"account_id": d.get("account_id"), "code": d.get("code"),
                                 "name": d.get("name_ar"),
                                 "source": "default" if d.get("account_id") else "none", "source_name": None}
        return out

    # ---- التحقق --------------------------------------------------------------
    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = clean_name(data.get("name"))
        if not name:
            raise BusinessError("اسم النوع مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم» أطول من المسموح (120 حرف).")
        clean: dict[str, Any] = {"name": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if key != "notes" and value:
                value = clean_name(value)
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        parent = data.get("parent_id")
        clean["parent_id"] = int(parent) if parent not in (None, "", 0) else None
        for kind in KINDS:
            col = KIND_COLUMN[kind]
            value = data.get(col)
            clean[col] = int(value) if value not in (None, "", 0) else None
        clean["new_items_service"] = bool(data.get("new_items_service", False))
        clean["is_active"] = bool(data.get("is_active", True))
        return clean

    def _check_parent(self, tx, clean: dict[str, Any], before: dict[str, Any] | None) -> None:
        parent_id = clean["parent_id"]
        if parent_id is None:
            return
        changed = before is None or before["parent_id"] != parent_id
        if not changed and not (clean["is_active"] and not before["is_active"]):
            return
        parent = self.repository.get_category(parent_id, ex=tx)
        if parent is None:
            raise BusinessError("النوع الأب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        if clean["is_active"] and not parent["is_active"]:
            raise BusinessError(f"النوع الأب {_label(parent)} موقوف — نشّطه الأول.")
        if before is not None and changed:
            if parent_id in self.repository.subtree_ids(int(before["id"]), ex=tx):
                raise BusinessError(f"مينفعش النوع «{before['name']}» يبقى تحت نفسه أو تحت نوع متفرّع منه.")
        height = self.repository.height(int(before["id"]), ex=tx) if before is not None else 0
        if changed and self.repository.depth(parent_id, ex=tx) + 1 + height > MAX_DEPTH:
            raise BusinessError(f"أنواع الأصناف لحد {MAX_DEPTH} مستويات بس — اختار أب في مستوى أعلى.")

    def _check_accounts(self, tx, clean: dict[str, Any], before: dict[str, Any] | None) -> None:
        activating = before is not None and clean["is_active"] and not before["is_active"]
        for kind in KINDS:
            col = KIND_COLUMN[kind]
            account_id = clean[col]
            if account_id is None:
                continue
            changed = before is None or before[col] != account_id
            if not changed and not activating:
                continue
            account = self.accounts.get_account(account_id, ex=tx)
            label = KIND_LABEL[kind]
            if account is None:
                raise BusinessError(f"حساب {label} مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
            text = f"«⁦{account['code']}⁩ {account['name_ar']}»"
            if changed:
                if not account["is_leaf"]:
                    raise BusinessError(f"حساب {label} {text} رئيسي — اختار حساب فرعي.")
                if not account_fits(account, kind):
                    raise BusinessError(f"حساب {label} {text} {_KIND_HINT[kind]}.")
            if clean["is_active"] and not account["is_active"]:
                raise BusinessError(f"حساب {label} {text} موقوف — نشّطه من شجرة الحسابات الأول.")

    def _active_kids_error(self, tx, category: dict[str, Any]) -> None:
        kids = self.repository.active_children(int(category["id"]), ex=tx)
        if kids:
            names = "، ".join(f"«{k['name']}»" for k in kids)
            raise BusinessError(f"النوع «{category['name']}» تحته أنواع نشطة ({names}) — وقّفهم الأول.")

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_category(self, category_id: int | None, data: dict[str, Any],
                      expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if category_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = None
            if category_id is not None:
                before = self.repository.get_category(int(category_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=category_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
            clean = self._clean(data)
            exclude = None if category_id is None else int(category_id)
            # جوه قفل الترقيم: جهازين بيضيفوا نفس الاسم في نفس اللحظة ← التاني بيشوف الأول ويترفض.
            clean["seq"] = self.repository.next_seq(tx) if before is None else before["seq"]
            if before is not None and not clean["is_active"] and before["is_active"]:
                self._active_kids_error(tx, before)
            self._check_parent(tx, clean, before)
            taken = self.repository.name_taken(clean["name"], clean["parent_id"], exclude, ex=tx)
            if taken:
                where = "تحت نفس الأب" if clean["parent_id"] is not None else "في الأنواع الرئيسية"
                raise BusinessError(f"فيه نوع بنفس الاسم «{taken['name']}» ({taken['code']}) {where} — الاسم "
                                    "مايتكررش حتى لو بحروف كبيرة/صغيرة.")
            self._check_accounts(tx, clean, before)
            code = format_code(clean["seq"])
            if before is None:
                category_id = self.repository.insert_category(clean, ex=tx)
            else:
                updated = self.repository.update_category(int(category_id), clean, ex=tx,
                                                          expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=category_id)
            after = self.repository.get_category(int(category_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=category_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name']}", before=before, after=after)
        return int(category_id)

    def set_active(self, category_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_category(int(category_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=category_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active:
                self._active_kids_error(tx, before)
            else:
                if before["parent_id"] is not None:
                    parent = self.repository.get_category(int(before["parent_id"]), ex=tx)
                    if parent is not None and not parent["is_active"]:
                        raise BusinessError(f"النوع الأب {_label(parent)} موقوف — نشّطه الأول.")
                for kind in KINDS:
                    if before[KIND_COLUMN[kind]] is not None and not before[f"{kind}_active"]:
                        raise BusinessError(f"حساب {KIND_LABEL[kind]} «⁦{before[f'{kind}_code']}⁩ "
                                            f"{before[f'{kind}_name']}» موقوف — نشّطه من شجرة الحسابات الأول.")
            updated = self.repository.set_active(int(category_id), bool(active), ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=category_id)
            after = self.repository.get_category(int(category_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=category_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name']}", before=before, after=after)

    def delete_category(self, category_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_category(int(category_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=category_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if before["children_count"]:
                raise BusinessError(f"النوع «{before['name']}» تحته أنواع — امسحهم أو انقلهم لأب تاني الأول.")
            # TODO (شاشة الأصناف): النوع اللي عليه أصناف مايتمسحش — FK RESTRICT + رسالة «وقّفه بدل ما تمسحه».
            deleted = self.repository.delete_category(int(category_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=category_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=category_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name']}", before=before)
