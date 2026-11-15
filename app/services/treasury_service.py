"""الخزائن (شاشة «الخزائن» — المرحلة 3ج، النموذج 1 اختيار المستخدم 2026-11-15 + الاقتراحات).

* **الكود** ``TRS-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``TreasuryRepository.next_seq``) وثابت.
* **حساب الخزنة** (ثابت بعد الحفظ):
  - ``auto`` (الافتراضي): حساب فرعي جديد بيتعمل في **نفس الـ transaction** تحت أعلى حساب رئيسي بدور «خزينة»
    (1101 النقدية بالخزائن) — الكود الجاي تحته (110103…) والاسم والعملة من الخزنة، والباقي بيتورث من الأب.
  - ``pick``: حساب فرعي نشط موجود بدور «خزينة» ومش مربوط بخزنة تانية، وبنفس العملة.
* **العملة** = عملة الحساب. تغييرها في «تعديل» بيغيّر عملة الحساب معاها (trigger في القاعدة).
  TODO (المرحلة 5): العملة ماتتغيّرش بعد أول حركة.
* **الأمين** اختياري: موظف نشط عليه دور «أمين خزينة» (والموظف يقدر يمسك أكتر من خزنة).
* **حد أقصى للرصيد** اختياري — تنبيه بس (مع السندات في المرحلة 5).
* **الخزينة الافتراضية** = اللي حسابها «الخزينة الافتراضية» في الحسابات الافتراضية — ماتتوقفش ولا تتمسح.
* **الرصيد ورصيد أول المدة** من القيود (المرحلة 4 و5) — الشاشة بتعرضهم توضيحي بس.
* الخزنة اللي اتعمل حسابها معاها: المسح بيمسح الحساب كمان (لو مالوش ربط تاني).
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction (والحساب اللي اتعمل أو اتغيّر معاها) +
  ``row_version``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.account_repository import AccountRepository
from app.repositories.treasury_repository import CASH_ROLE, STATUSES, TreasuryRepository, format_code
from app.services.account_service import AccountService, code_width, next_child_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "cash.treasuries", "خزنة"
ACCOUNT_ENTITY, ACCOUNT_LABEL = "acc.accounts", "حساب"
PERM = "cash.treasuries"
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
NOT_FOUND = "الخزنة دي مش موجودة (ممكن تكون اتمسحت)."
ACCOUNT_MODES = ("auto", "pick")
MAX_BALANCE = Decimal("9999999999999999.99")
DEFAULT_LOCKED = ("الخزنة «{name}» هي الخزينة الافتراضية (لسندات القبض والصرف) — {what} "
                  "(غيّر «الخزينة الافتراضية» من شاشة «الحسابات الافتراضية» الأول).")
NO_PARENT = ("مفيش حساب رئيسي نشط بدور «خزينة» في الدليل (زي «1101 النقدية بالخزائن») — "
             "اعمله من شجرة الحسابات أو اختار «حساب موجود».")
BALANCE_NOTE = "الرصيد بيتحسب من القيود على حساب الخزنة (المرحلة 5) — ورصيد أول المدة من مستند الأرصدة الافتتاحية."
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_en", "الاسم EN", 120), ("location", "المكان", 200), ("notes", "الملاحظات", 500))


def fmt_amount(value: Any) -> str:
    return f"{Decimal(value):,.2f}" if value not in (None, "") else ""


class TreasuryService:
    def __init__(self, db: Database | None = None, repository: TreasuryRepository | None = None,
                 accounts: AccountRepository | None = None, audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or TreasuryRepository(self.db)
        self.accounts = accounts or AccountRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_treasuries(self, keyword: str = "", status: str = "all", page: int = 1,
                        page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_treasuries(keyword.strip(), status, page_size, offset)

    def count_treasuries(self, keyword: str = "", status: str = "all") -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_treasuries(keyword.strip(), status)
        return {k: int(row[k]) for k in ("total", "active", "inactive")}

    def lookup_treasuries(self) -> list[dict[str, Any]]:
        """نافذة البحث F1: كل الخزائن (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_treasuries("", "all", LOOKUP_LIMIT, 0)

    def get_treasury(self, treasury_id: int) -> dict[str, Any] | None:
        return self.repository.get_treasury(int(treasury_id))

    def currencies(self) -> list[dict[str, Any]]:
        """العملات النشطة (الأساسية الأول)."""
        return [c for c in self.repository.list_currencies() if c["is_active"]]

    def treasurers(self) -> list[dict[str, Any]]:
        """الموظفين النشطين اللي عليهم دور «أمين خزينة»."""
        return self.repository.list_treasurers()

    def free_accounts(self) -> list[dict[str, Any]]:
        """حسابات «خزينة» فرعية نشطة ومش مربوطة (لـ «أختار حساب موجود»)."""
        return self.repository.free_cash_accounts()

    def suggested_account(self) -> dict[str, Any] | None:
        """الحساب اللي هيتعمل لوحده (للعرض بس): ``{code, parent_code, parent_name}`` — None = مفيش أب."""
        parent_row = self.repository.cash_parent(self.db)
        if parent_row is None:
            return None
        parent = self.accounts.get_account(int(parent_row["id"]))
        code = self._next_account_code(parent)
        return {"code": code, "parent_code": parent["code"], "parent_name": parent["name_ar"]}

    def new_defaults(self) -> dict[str, Any]:
        base = next((c["code"] for c in self.currencies() if c["is_base"]), "EGP")
        return {"code": self.repository.suggested_code(), "is_active": True, "currency_code": base,
                "account_mode": "auto", "suggested_account": self.suggested_account()}

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def clean_amount(value: Any, label: str) -> Decimal | None:
        """رقم موجب اختياري (فاضي أو صفر = من غير حد)."""
        text = str(value if value is not None else "").replace(",", "").strip()
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise BusinessError(f"«{label}» لازم يبقى رقم.") from exc
        if not number.is_finite():
            raise BusinessError(f"«{label}» لازم يبقى رقم.")
        number = number.quantize(Decimal("0.01"))
        if number == 0:
            return None
        if number < 0:
            raise BusinessError(f"«{label}» مايبقاش سالب.")
        if number > MAX_BALANCE:
            raise BusinessError(f"«{label}» أكبر من المسموح.")
        return number

    def _clean(self, data: dict[str, Any], before: dict[str, Any] | None) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم الخزنة مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم» أطول من المسموح (120 حرف).")
        clean: dict[str, Any] = {"name_ar": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        currency = str(data.get("currency_code") or "").strip().upper()
        if not currency:
            raise BusinessError("لازم تختار عملة الخزنة.")
        known = {c["code"]: c for c in self.repository.list_currencies()}
        if currency not in known:
            raise BusinessError(f"العملة «⁦{currency}⁩» مش موجودة.")
        if not known[currency]["is_active"] and (before is None or before["currency_code"] != currency):
            raise BusinessError(f"العملة «⁦{currency}⁩» موقوفة — نشّطها من شاشة العملات الأول.")
        clean["currency_code"] = currency
        treasurer = data.get("treasurer_id")
        clean["treasurer_id"] = int(treasurer) if treasurer not in (None, "", 0) else None
        clean["max_balance"] = self.clean_amount(data.get("max_balance"), "الحد الأقصى للرصيد")
        clean["is_active"] = bool(data.get("is_active", True))
        if clean["treasurer_id"] is not None and clean["is_active"]:
            changed = (before is None or before["treasurer_id"] != clean["treasurer_id"]
                       or not before["is_active"])
            if changed:
                emp = self.repository.get_employee(clean["treasurer_id"])
                if emp is None:
                    raise BusinessError("أمين الخزينة مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
                if not emp["is_treasurer"]:
                    raise BusinessError(f"الموظف «{emp['name_ar']}» ({emp['code']}) مش عليه دور «أمين خزينة» — "
                                        "فعّله من شاشة الموظفين.")
                if not emp["is_active"]:
                    raise BusinessError(f"الموظف «{emp['name_ar']}» ({emp['code']}) موقوف — "
                                        "مينفعش يبقى أمين خزينة.")
        return clean

    # ---- حساب الخزنة -----------------------------------------------------------
    def _next_account_code(self, parent: dict[str, Any], ex=None) -> str:
        width = code_width(int(parent["level"]))
        used = self.accounts.codes_with_prefix(parent["code"], len(parent["code"]) + width, ex=ex)
        return next_child_code(parent["code"], int(parent["level"]), used)

    def _create_account(self, tx, clean: dict[str, Any]) -> int:
        """حساب فرعي جديد تحت 1101 باسم الخزنة وعملتها (جوه قفل الأب — جهازين مياخدوش نفس الكود)."""
        parent_row = self.repository.cash_parent(tx, for_update=True)
        if parent_row is None:
            raise BusinessError(NO_PARENT)
        parent = self.accounts.get_account(int(parent_row["id"]), ex=tx)
        if int(parent["level"]) >= 5:
            raise BusinessError(f"الحساب «⁦{parent['code']}⁩ {parent['name_ar']}» في آخر مستوى — "
                                "مينفعش يتعمل تحته حساب للخزنة.")
        code = self._next_account_code(parent, ex=tx)
        if not code:
            raise BusinessError(f"مفيش أكواد فاضية تحت «⁦{parent['code']}⁩ {parent['name_ar']}» — "
                                "اختار «حساب موجود».")
        data = AccountService._child_defaults(parent, code)
        data.update({"name_ar": clean["name_ar"], "name_en": clean["name_en"],
                     "currency_code": clean["currency_code"], "role_code": CASH_ROLE, "is_leaf": True,
                     "is_active": True})
        account_id = self.accounts.insert_account(data, ex=tx)
        after = self.accounts.get_account(account_id, ex=tx)
        self.audit.record(tx, action="create", entity=ACCOUNT_ENTITY, entity_id=account_id,
                          entity_label=ACCOUNT_LABEL, record_label=f"{code} {clean['name_ar']}", after=after)
        return account_id

    def _pick_account(self, tx, account_id: Any, clean: dict[str, Any]) -> int:
        if account_id in (None, "", 0):
            raise BusinessError("اختار الحساب من الدليل (أو خلّيه «يتعمل لوحده»).")
        account = self.accounts.get_account(int(account_id), ex=tx, for_update=True)   # جهازين على نفس الحساب
        if account is None:
            raise BusinessError("الحساب ده مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        label = f"«⁦{account['code']}⁩ {account['name_ar']}»"
        if not account["is_leaf"]:
            raise BusinessError(f"الحساب {label} رئيسي — الخزنة لازم يبقى حسابها فرعي.")
        if account["role_code"] != CASH_ROLE:
            raise BusinessError(f"الحساب {label} مش بدور «خزينة» — اختار حساب تحت «النقدية بالخزائن».")
        if not account["is_active"]:
            raise BusinessError(f"الحساب {label} موقوف — اختار حساب نشط.")
        taken = self.repository.treasury_of_account(int(account["id"]), ex=tx)
        if taken:
            raise BusinessError(f"الحساب {label} مربوط بالفعل بالخزنة «{taken['name_ar']}» ({taken['code']}).")
        if account["currency_code"] != clean["currency_code"]:
            raise BusinessError(f"عملة الحساب {label} (⁦{account['currency_code']}⁩) غير عملة الخزنة "
                                f"(⁦{clean['currency_code']}⁩).")
        return int(account["id"])

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_treasury(self, treasury_id: int | None, data: dict[str, Any],
                      expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if treasury_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        mode = data.get("account_mode") or "auto"
        if treasury_id is None and mode not in ACCOUNT_MODES:
            raise BusinessError("حساب الخزنة: «يتعمل لوحده» أو «حساب موجود».")
        with self.db.atomic() as tx:
            before = None
            if treasury_id is not None:
                before = self.repository.get_treasury(int(treasury_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=treasury_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
            clean = self._clean(data, before)
            exclude = None if treasury_id is None else int(treasury_id)
            if before is None:
                clean["seq"] = self.repository.next_seq(tx)          # جوه قفل الترقيم
            else:
                clean.update({k: before[k] for k in ("seq", "account_id", "owns_account")})   # ثابتين
                if not clean["is_active"] and before["is_active"] and before["is_default"]:
                    raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="ماتتوقفش"))
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه خزنة بنفس الاسم «{taken['name_ar']}» ({taken['code']}).")
            code = format_code(clean["seq"])
            if before is None:
                if mode == "auto":
                    clean["account_id"], clean["owns_account"] = self._create_account(tx, clean), True
                else:
                    clean["account_id"] = self._pick_account(tx, data.get("account_id"), clean)
                    clean["owns_account"] = False
                treasury_id = self.repository.insert_treasury(clean, ex=tx)
            else:
                account_before = self.accounts.get_account(int(before["account_id"]), ex=tx, for_update=True)
                updated = self.repository.update_treasury(int(treasury_id), clean, ex=tx,
                                                          expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=treasury_id)
                account_after = self.accounts.get_account(int(before["account_id"]), ex=tx)
                if account_after["row_version"] != account_before["row_version"]:   # العملة / الاسم اتغيّروا معاها
                    self.audit.record(tx, action="update", entity=ACCOUNT_ENTITY, entity_id=before["account_id"],
                                      entity_label=ACCOUNT_LABEL,
                                      record_label=f"{account_after['code']} {account_after['name_ar']}",
                                      before=account_before, after=account_after)
            after = self.repository.get_treasury(int(treasury_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=treasury_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name_ar']}", before=before, after=after)
        return int(treasury_id)

    def set_active(self, treasury_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات — الخزينة الافتراضية ماتتوقفش."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_treasury(int(treasury_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=treasury_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active and before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="ماتتوقفش"))
            if active and before["treasurer_id"] is not None:
                emp = self.repository.get_employee(int(before["treasurer_id"]), ex=tx)
                if emp is None or not emp["is_treasurer"] or not emp["is_active"]:
                    raise BusinessError(f"أمين الخزنة «{before['treasurer_name']}» مابقاش أمين خزينة نشط — "
                                        "غيّر الأمين («تعديل») وبعدين فعّل الخزنة.")
            updated = self.repository.set_active(int(treasury_id), bool(active), ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=treasury_id)
            after = self.repository.get_treasury(int(treasury_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=treasury_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_treasury(self, treasury_id: int, expected_version: int | None = None) -> None:
        """المسح: الخزنة + حسابها لو كان اتعمل معاها (في نفس الـ transaction)."""
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_treasury(int(treasury_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=treasury_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="ماتتمسحش"))
            # TODO (السندات — المرحلة 5): الخزنة اللي عليها حركة ماتتمسحش — FK RESTRICT
            # + رسالة عربي «عليها حركة — وقّفها بدل ما تمسحها».
            deleted = self.repository.delete_treasury(int(treasury_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=treasury_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=treasury_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
            if before["owns_account"]:
                account = self.accounts.get_account(int(before["account_id"]), ex=tx, for_update=True)
                if account is not None and not account["has_children"]:
                    self.accounts.delete_account(int(account["id"]), ex=tx)
                    self.audit.record(tx, action="delete", entity=ACCOUNT_ENTITY, entity_id=account["id"],
                                      entity_label=ACCOUNT_LABEL,
                                      record_label=f"{account['code']} {account['name_ar']}", before=account)
