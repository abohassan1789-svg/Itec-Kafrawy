"""البنوك والحسابات البنكية (شاشة «البنوك» — المرحلة 3ج، النموذج 7 «البنوك في دليل الحسابات» اختيار المستخدم
2026-11-15 + كل الاقتراحات).

* **مستويين:** البنك (الاسم، المختصر، الفرع، العنوان، SWIFT، مسؤول الحساب) ← حساب بنكي أو أكتر.
* **الكود:** البنك ``BNK-001`` والحساب ``BNK-001-01`` — بيتولّدوا وقت الحفظ جوه قفل وثابتين.
* **حساب الدليل** (ثابت بعد الحفظ):
  - ``auto`` (الافتراضي): حساب فرعي جديد في **نفس الـ transaction** تحت أعلى حساب رئيسي بدور «بنك» (1102 البنوك)
    باسم «<البنك> — <النوع> [العملة لو مش الأساسية]» (``account_title``)، والباقي بيتورث من الأب.
  - ``pick``: حساب فرعي نشط موجود بدور «بنك» ومش مربوط، وبنفس العملة.
* **النوع:** جاري · توفير · وديعة لأجل (تاريخ استحقاق اختياري) · جاري مدين (حد تسهيلات إجباري). الرصيد السالب
  ممنوع إلا الجاري المدين لحد التسهيلات — المنع نفسه وقت السند (المرحلة 5).
* **العملة** = عملة الحساب، وتغييرها (أو النوع) بيغيّر حساب الدليل معاها (trigger). TODO (المرحلة 5): ماتتغيّرش بعد
  أول حركة.
* **رقم الحساب** مطلوب ومايتكررش في نفس البنك · **IBAN** اختياري ومايتكررش (الشكل + رقم التحقق mod 97، والمصري
  EG + 27 رقم) · **SWIFT** اختياري (8 أو 11 حرف).
* **البنك الافتراضي** = الحساب البنكي اللي حسابه «البنك الافتراضي» في الحسابات الافتراضية — مايتوقفش ولا يتمسح.
* البنك اللي عليه حسابات مايتمسحش، ومايتوقفش وحساب منهم نشط.
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version``.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.account_repository import AccountRepository
from app.repositories.bank_repository import BANK_ROLE, BankRepository, format_account_code, format_bank_code
from app.services.account_service import AccountService, code_width, next_child_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

BANK_ENTITY, BANK_LABEL = "cash.banks", "بنك"
ENTITY, ENTITY_LABEL = "cash.bank_accounts", "حساب بنكي"
ACCOUNT_ENTITY, ACCOUNT_LABEL = "acc.accounts", "حساب"
PERM = "cash.banks"
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
STATUSES = ("all", "active", "inactive")
ACCOUNT_MODES = ("auto", "pick")
ACCOUNT_TYPES = {"current": "جاري", "savings": "توفير", "deposit": "وديعة لأجل", "overdraft": "جاري مدين (تسهيلات)"}
TYPES_EN = {"current": "Current", "savings": "Savings", "deposit": "Deposit", "overdraft": "Overdraft"}
MAX_AMOUNT = Decimal("9999999999999999.99")
MIN_DATE, MAX_DATE = date(2000, 1, 1), date(2100, 12, 31)
BANK_NOT_FOUND = "البنك ده مش موجود (ممكن يكون اتمسح)."
NOT_FOUND = "الحساب البنكي ده مش موجود (ممكن يكون اتمسح)."
DEFAULT_LOCKED = ("الحساب البنكي «{name}» هو البنك الافتراضي (للإيداع والتحويل البنكي) — {what} "
                  "(غيّر «البنك الافتراضي» من شاشة «الحسابات الافتراضية» الأول).")
NO_PARENT = ("مفيش حساب رئيسي نشط بدور «بنك» في الدليل (زي «1102 البنوك») — "
             "اعمله من شجرة الحسابات أو اختار «حساب موجود».")
BALANCE_NOTE = ("الرصيد بيتحسب من القيود على حساب البنك (المرحلة 5) — ورصيد أول المدة من مستند الأرصدة "
                "الافتتاحية. الرصيد السالب ممنوع إلا الجاري المدين لحد التسهيلات.")
_SWIFT = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_IBAN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_BANK_TEXTS = (("name_en", "الاسم EN", 120), ("short_name", "الاسم المختصر", 60), ("branch", "الفرع", 120),
               ("address", "العنوان", 250), ("contact_name", "مسؤول الحساب", 120),
               ("contact_phone", "تليفون مسؤول الحساب", 30), ("notes", "الملاحظات", 500))


def fmt_amount(value: Any) -> str:
    return f"{Decimal(value):,.2f}" if value not in (None, "") else ""


def bank_label(bank: dict[str, Any]) -> str:
    """الاسم اللي بيتكتب في اسم الحساب: المختصر لو موجود، وإلا الاسم."""
    return (bank.get("short_name") or "").strip() or bank["name_ar"]


def iban_checksum_ok(iban: str) -> bool:
    """رقم التحقق بتاع الـ IBAN (ISO 13616 — mod 97 = 1)."""
    moved = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in moved)
    return int(digits) % 97 == 1


def clean_iban(value: Any) -> str | None:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if not text:
        return None
    if not _IBAN.match(text):
        raise BusinessError("الـ IBAN شكله غلط (حرفين للبلد + رقمين تحقق + رقم الحساب).")
    if text.startswith("EG") and len(text) != 29:
        raise BusinessError(f"الـ IBAN المصري = EG + 27 رقم (29 حرف) — المكتوب {len(text)} حرف.")
    if not iban_checksum_ok(text):
        raise BusinessError("الـ IBAN غلط (رقم التحقق مش مظبوط) — راجعه من ورق البنك.")
    return text


def clean_swift(value: Any) -> str | None:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if not text:
        return None
    if not _SWIFT.match(text):
        raise BusinessError("كود SWIFT لازم 8 أو 11 حرف (زي NBEGEGCX).")
    return text


class BankService:
    def __init__(self, db: Database | None = None, repository: BankRepository | None = None,
                 accounts: AccountRepository | None = None, audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or BankRepository(self.db)
        self.accounts = accounts or AccountRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # =========================================================================
    # البنوك
    # =========================================================================
    def list_banks(self, keyword: str = "", status: str = "all") -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        return self.repository.list_banks(keyword.strip(), status)

    def active_banks(self) -> list[dict[str, Any]]:
        return self.repository.list_banks("", "active")

    def get_bank(self, bank_id: int) -> dict[str, Any] | None:
        return self.repository.get_bank(int(bank_id))

    def new_bank_defaults(self) -> dict[str, Any]:
        return {"code": self.repository.suggested_bank_code(), "is_active": True}

    def _clean_bank(self, data: dict[str, Any]) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم البنك مطلوب.")
        if len(name) > 120:
            raise BusinessError("«اسم البنك» أطول من المسموح (120 حرف).")
        clean: dict[str, Any] = {"name_ar": name}
        for key, label, size in _BANK_TEXTS:
            value = " ".join(str(data.get(key) or "").split()) or None
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        clean["swift_code"] = clean_swift(data.get("swift_code"))
        clean["is_active"] = bool(data.get("is_active", True))
        return clean

    def save_bank(self, bank_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if bank_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = None
            if bank_id is not None:
                before = self.repository.get_bank(int(bank_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=BANK_ENTITY, entity_id=bank_id)
                if before is None:
                    raise BusinessError(BANK_NOT_FOUND)
            clean = self._clean_bank(data)
            taken = self.repository.bank_name_taken(clean["name_ar"], None if bank_id is None else int(bank_id),
                                                    ex=tx)
            if taken:
                raise BusinessError(f"فيه بنك بنفس الاسم «{taken['name_ar']}» ({taken['code']}).")
            if before is None:
                clean["seq"] = self.repository.next_bank_seq(tx)               # جوه قفل الترقيم
                bank_id = self.repository.insert_bank(clean, ex=tx)
            else:
                clean["seq"] = before["seq"]
                if not clean["is_active"] and before["is_active"] and before["active_accounts"]:
                    raise BusinessError(f"البنك «{before['name_ar']}» عليه {before['active_accounts']} حساب نشط — "
                                        "وقّفهم الأول.")
                accounts_before = self._lock_bank_accounts(tx, int(bank_id))
                updated = self.repository.update_bank(int(bank_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=BANK_ENTITY, entity_id=bank_id)
                self._audit_renamed_accounts(tx, accounts_before)
            after = self.repository.get_bank(int(bank_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=BANK_ENTITY,
                              entity_id=bank_id, entity_label=BANK_LABEL,
                              record_label=f"{format_bank_code(clean['seq'])} {clean['name_ar']}",
                              before=before, after=after)
        return int(bank_id)

    def _lock_bank_accounts(self, tx, bank_id: int) -> dict[int, dict[str, Any]]:
        """حسابات الدليل بتاعة البنك (مقفولة) قبل التعديل — عشان سطر audit لو اسمها اتغيّر مع اسم البنك."""
        return {aid: self.accounts.get_account(aid, ex=tx, for_update=True)
                for aid in self.repository.linked_account_ids(bank_id, ex=tx)}

    def _audit_renamed_accounts(self, tx, accounts_before: dict[int, dict[str, Any]]) -> None:
        """اسم البنك اتغيّر ← الـ trigger غيّر أسماء حساباته في الدليل (اللي كانت بالاسم المتولّد) — سطر audit لكل واحد."""
        for account_id, before in accounts_before.items():
            after = self.accounts.get_account(account_id, ex=tx)
            if after is not None and after["row_version"] != before["row_version"]:
                self.audit.record(tx, action="update", entity=ACCOUNT_ENTITY, entity_id=account_id,
                                  entity_label=ACCOUNT_LABEL, record_label=f"{after['code']} {after['name_ar']}",
                                  before=before, after=after)

    def set_bank_active(self, bank_id: int, active: bool, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_bank(int(bank_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=BANK_ENTITY, entity_id=bank_id)
            if before is None:
                raise BusinessError(BANK_NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active and before["active_accounts"]:
                raise BusinessError(f"البنك «{before['name_ar']}» عليه {before['active_accounts']} حساب نشط — "
                                    "وقّفهم الأول.")
            updated = self.repository.set_bank_active(int(bank_id), bool(active), ex=tx,
                                                      expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=BANK_ENTITY, entity_id=bank_id)
            after = self.repository.get_bank(int(bank_id), ex=tx)
            self.audit.record(tx, action="update", entity=BANK_ENTITY, entity_id=bank_id, entity_label=BANK_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_bank(self, bank_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_bank(int(bank_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=BANK_ENTITY, entity_id=bank_id)
            if before is None:
                raise BusinessError(BANK_NOT_FOUND)
            if before["accounts_count"]:
                raise BusinessError(f"البنك «{before['name_ar']}» عليه {before['accounts_count']} حساب بنكي — "
                                    "امسحهم الأول أو وقّف البنك.")
            deleted = self.repository.delete_bank(int(bank_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=BANK_ENTITY, entity_id=bank_id)
            self.audit.record(tx, action="delete", entity=BANK_ENTITY, entity_id=bank_id, entity_label=BANK_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)

    # =========================================================================
    # الحسابات البنكية
    # =========================================================================
    def list_accounts(self, keyword: str = "", status: str = "all", page: int = 1,
                      page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_accounts(keyword.strip(), status, page_size, offset)

    def lookup_accounts(self) -> list[dict[str, Any]]:
        """نافذة البحث F1 و«تصدير إكسل»: كل الحسابات البنكية (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_accounts("", "all", LOOKUP_LIMIT, 0)

    def get_account(self, bank_account_id: int) -> dict[str, Any] | None:
        return self.repository.get_account(int(bank_account_id))

    def chart_rows(self) -> list[dict[str, Any]]:
        """شجرة النموذج 7: كل حسابات «بنك» في الدليل + الحساب البنكي المربوط بكل واحد."""
        return self.repository.chart_rows()

    def currencies(self) -> list[dict[str, Any]]:
        return [c for c in self.repository.list_currencies() if c["is_active"]]

    def free_accounts(self) -> list[dict[str, Any]]:
        """حسابات «بنك» فرعية نشطة ومش مربوطة (لـ «أختار حساب موجود»)."""
        return self.repository.free_bank_accounts()

    def account_title(self, bank: dict[str, Any], account_type: str, currency: str) -> str:
        return self.repository.account_title(bank_label(bank), account_type, currency)

    def suggested_account(self) -> dict[str, Any] | None:
        """حساب الدليل اللي هيتعمل لوحده (للعرض بس): ``{code, parent_code, parent_name}`` — None = مفيش أب."""
        parent_row = self.repository.bank_parent(self.db)
        if parent_row is None:
            return None
        parent = self.accounts.get_account(int(parent_row["id"]))
        code = self._next_account_code(parent)
        return {"code": code, "parent_code": parent["code"], "parent_name": parent["name_ar"]}

    def suggested_code(self, bank_id: Any) -> str:
        """كود الحساب البنكي الجاي في البنك ده (للعرض بس — الفعلي وقت الحفظ جوه القفل)."""
        if bank_id in (None, "", 0):
            return ""
        bank = self.repository.get_bank(int(bank_id))
        if bank is None:
            return ""
        return format_account_code(bank["code"], self.repository.suggested_account_seq(int(bank_id)))

    def new_defaults(self, bank_id: Any = None) -> dict[str, Any]:
        base = next((c["code"] for c in self.currencies() if c["is_base"]), "EGP")
        banks = self.active_banks()
        if bank_id in (None, "", 0) and banks:
            bank_id = banks[0]["id"]
        return {"bank_id": bank_id, "code": self.suggested_code(bank_id), "is_active": True,
                "account_type": "current", "currency_code": base, "account_mode": "auto",
                "suggested_account": self.suggested_account()}

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def clean_amount(value: Any, label: str) -> Decimal | None:
        """رقم موجب اختياري (فاضي = مفيش)."""
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
        if number <= 0:
            raise BusinessError(f"«{label}» لازم يبقى أكبر من صفر.")
        if number > MAX_AMOUNT:
            raise BusinessError(f"«{label}» أكبر من المسموح.")
        return number

    @staticmethod
    def clean_date(value: Any, label: str) -> date | None:
        if value in (None, ""):
            return None
        if not isinstance(value, date):
            try:
                value = date.fromisoformat(str(value).strip())
            except ValueError as exc:
                raise BusinessError(f"«{label}» تاريخ غلط.") from exc
        if not MIN_DATE <= value <= MAX_DATE:
            raise BusinessError(f"«{label}» لازم بين {MIN_DATE.year} و {MAX_DATE.year}.")
        return value

    def _clean_account(self, data: dict[str, Any], before: dict[str, Any] | None) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        account_type = str(data.get("account_type") or "").strip()
        if account_type not in ACCOUNT_TYPES:
            raise BusinessError("اختار نوع الحساب (جاري / توفير / وديعة / جاري مدين).")
        clean["account_type"] = account_type
        currency = str(data.get("currency_code") or "").strip().upper()
        if not currency:
            raise BusinessError("لازم تختار عملة الحساب البنكي.")
        known = {c["code"]: c for c in self.repository.list_currencies()}
        if currency not in known:
            raise BusinessError(f"العملة «⁦{currency}⁩» مش موجودة.")
        if not known[currency]["is_active"] and (before is None or before["currency_code"] != currency):
            raise BusinessError(f"العملة «⁦{currency}⁩» موقوفة — نشّطها من شاشة العملات الأول.")
        clean["currency_code"] = currency
        number = " ".join(str(data.get("account_number") or "").split())
        if not number:
            raise BusinessError("رقم الحساب مطلوب.")
        if len(number) > 34:
            raise BusinessError("«رقم الحساب» أطول من المسموح (34 حرف).")
        if not any(ch.isalnum() for ch in number):
            raise BusinessError("«رقم الحساب» لازم فيه أرقام.")
        clean["account_number"] = number
        clean["iban"] = clean_iban(data.get("iban"))
        if account_type == "overdraft":
            limit = self.clean_amount(data.get("overdraft_limit"), "حد التسهيلات")
            if limit is None:
                raise BusinessError("الجاري المدين لازم له «حد التسهيلات».")
            clean["overdraft_limit"] = limit
        else:
            clean["overdraft_limit"] = None
        clean["maturity_date"] = (self.clean_date(data.get("maturity_date"), "تاريخ الاستحقاق")
                                  if account_type == "deposit" else None)
        notes = (data.get("notes") or "").strip() or None
        if notes and len(notes) > 500:
            raise BusinessError("«الملاحظات» أطول من المسموح (500 حرف).")
        clean["notes"] = notes
        clean["is_active"] = bool(data.get("is_active", True))
        return clean

    # ---- حساب الدليل -----------------------------------------------------------
    def _next_account_code(self, parent: dict[str, Any], ex=None) -> str:
        width = code_width(int(parent["level"]))
        used = self.accounts.codes_with_prefix(parent["code"], len(parent["code"]) + width, ex=ex)
        return next_child_code(parent["code"], int(parent["level"]), used)

    def _create_account(self, tx, bank: dict[str, Any], clean: dict[str, Any]) -> int:
        """حساب فرعي جديد تحت 1102 باسم «البنك — النوع» وعملته (جوه قفل الأب — جهازين مياخدوش نفس الكود)."""
        parent_row = self.repository.bank_parent(tx, for_update=True)
        if parent_row is None:
            raise BusinessError(NO_PARENT)
        parent = self.accounts.get_account(int(parent_row["id"]), ex=tx)
        if int(parent["level"]) >= 5:
            raise BusinessError(f"الحساب «⁦{parent['code']}⁩ {parent['name_ar']}» في آخر مستوى — "
                                "مينفعش يتعمل تحته حساب بنكي.")
        code = self._next_account_code(parent, ex=tx)
        if not code:
            raise BusinessError(f"مفيش أكواد فاضية تحت «⁦{parent['code']}⁩ {parent['name_ar']}» — "
                                "اختار «حساب موجود».")
        name = self.repository.account_title(bank_label(bank), clean["account_type"], clean["currency_code"], ex=tx)
        name_en = None
        if bank.get("name_en"):
            name_en = f"{bank['name_en']} — {TYPES_EN[clean['account_type']]}"[:120]
        data = AccountService._child_defaults(parent, code)
        data.update({"name_ar": name, "name_en": name_en, "currency_code": clean["currency_code"],
                     "role_code": BANK_ROLE, "is_leaf": True, "is_active": True})
        account_id = self.accounts.insert_account(data, ex=tx)
        after = self.accounts.get_account(account_id, ex=tx)
        self.audit.record(tx, action="create", entity=ACCOUNT_ENTITY, entity_id=account_id,
                          entity_label=ACCOUNT_LABEL, record_label=f"{code} {name}", after=after)
        return account_id

    def _pick_account(self, tx, account_id: Any, clean: dict[str, Any]) -> int:
        if account_id in (None, "", 0):
            raise BusinessError("اختار الحساب من الدليل (أو خلّيه «يتعمل لوحده»).")
        account = self.accounts.get_account(int(account_id), ex=tx, for_update=True)   # جهازين على نفس الحساب
        if account is None:
            raise BusinessError("الحساب ده مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        label = f"«⁦{account['code']}⁩ {account['name_ar']}»"
        if not account["is_leaf"]:
            raise BusinessError(f"الحساب {label} رئيسي — الحساب البنكي لازم يبقى حسابه فرعي.")
        if account["role_code"] != BANK_ROLE:
            raise BusinessError(f"الحساب {label} مش بدور «بنك» — اختار حساب تحت «البنوك».")
        if not account["is_active"]:
            raise BusinessError(f"الحساب {label} موقوف — اختار حساب نشط.")
        taken = self.repository.bank_account_of(int(account["id"]), ex=tx)
        if taken:
            raise BusinessError(f"الحساب {label} مربوط بالفعل بالحساب البنكي «{taken['code']}».")
        if account["currency_code"] != clean["currency_code"]:
            raise BusinessError(f"عملة الحساب {label} (⁦{account['currency_code']}⁩) غير عملة الحساب البنكي "
                                f"(⁦{clean['currency_code']}⁩).")
        return int(account["id"])

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_account(self, bank_account_id: int | None, data: dict[str, Any],
                     expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if bank_account_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        mode = data.get("account_mode") or "auto"
        if bank_account_id is None and mode not in ACCOUNT_MODES:
            raise BusinessError("حساب الدليل: «يتعمل لوحده» أو «حساب موجود».")
        with self.db.atomic() as tx:
            before = None
            if bank_account_id is not None:
                before = self.repository.get_account(int(bank_account_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=bank_account_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
            clean = self._clean_account(data, before)
            if before is None:
                bank_id = data.get("bank_id")
                if bank_id in (None, "", 0):
                    raise BusinessError("اختار البنك.")
                bank = self.repository.get_bank(int(bank_id), ex=tx)
                if bank is None:
                    raise BusinessError(BANK_NOT_FOUND + " — دوس «تحديث».")
                if not bank["is_active"] and clean["is_active"]:
                    raise BusinessError(f"البنك «{bank['name_ar']}» موقوف — نشّطه الأول.")
                clean["bank_id"] = int(bank["id"])
            else:
                clean.update({k: before[k] for k in ("bank_id", "seq", "account_id", "owns_account")})   # ثابتين
                bank = self.repository.get_bank(int(before["bank_id"]), ex=tx)
                if not clean["is_active"] and before["is_active"] and before["is_default"]:
                    raise BusinessError(DEFAULT_LOCKED.format(name=before["code"], what="مايتوقفش"))
                if clean["is_active"] and not before["is_active"] and not bank["is_active"]:
                    raise BusinessError(f"البنك «{bank['name_ar']}» موقوف — نشّطه الأول.")
            exclude = None if bank_account_id is None else int(bank_account_id)
            taken = self.repository.number_taken(clean["bank_id"], clean["account_number"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"رقم الحساب ده متسجل في نفس البنك ({taken['code']}).")
            if clean["iban"]:
                taken = self.repository.iban_taken(clean["iban"], exclude, ex=tx)
                if taken:
                    raise BusinessError(f"الـ IBAN ده متسجل على الحساب البنكي {taken['code']}.")
            if before is None:
                clean["seq"] = self.repository.next_account_seq(clean["bank_id"], tx)    # جوه قفل الترقيم
                if mode == "auto":
                    clean["account_id"], clean["owns_account"] = self._create_account(tx, bank, clean), True
                else:
                    clean["account_id"] = self._pick_account(tx, data.get("account_id"), clean)
                    clean["owns_account"] = False
                bank_account_id = self.repository.insert_account(clean, ex=tx)
            else:
                account_before = self.accounts.get_account(int(before["account_id"]), ex=tx, for_update=True)
                updated = self.repository.update_account(int(bank_account_id), clean, ex=tx,
                                                         expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=bank_account_id)
                account_after = self.accounts.get_account(int(before["account_id"]), ex=tx)
                if account_after["row_version"] != account_before["row_version"]:   # العملة / الاسم اتغيّروا معاه
                    self.audit.record(tx, action="update", entity=ACCOUNT_ENTITY, entity_id=before["account_id"],
                                      entity_label=ACCOUNT_LABEL,
                                      record_label=f"{account_after['code']} {account_after['name_ar']}",
                                      before=account_before, after=account_after)
            after = self.repository.get_account(int(bank_account_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=bank_account_id, entity_label=ENTITY_LABEL,
                              record_label=self._label(after), before=before, after=after)
        return int(bank_account_id)

    @staticmethod
    def _label(row: dict[str, Any]) -> str:
        return f"{row['code']} {row['bank_short'] or row['bank_name']} — {ACCOUNT_TYPES[row['account_type']]}"

    def set_active(self, bank_account_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» — البنك الافتراضي مايتوقفش، والتفعيل محتاج البنك نشط."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_account(int(bank_account_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=bank_account_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active and before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["code"], what="مايتوقفش"))
            if active and not before["bank_active"]:
                raise BusinessError(f"البنك «{before['bank_name']}» موقوف — نشّطه الأول.")
            updated = self.repository.set_account_active(int(bank_account_id), bool(active), ex=tx,
                                                         expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=bank_account_id)
            after = self.repository.get_account(int(bank_account_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=bank_account_id,
                              entity_label=ENTITY_LABEL, record_label=self._label(before), before=before, after=after)

    def delete_account(self, bank_account_id: int, expected_version: int | None = None) -> None:
        """المسح: الحساب البنكي + حساب الدليل لو كان اتعمل معاه (في نفس الـ transaction)."""
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_account(int(bank_account_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=bank_account_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["code"], what="مايتمسحش"))
            # TODO (السندات — المرحلة 5): الحساب البنكي اللي عليه حركة مايتمسحش — FK RESTRICT
            # + رسالة عربي «عليه حركة — وقّفه بدل ما تمسحه».
            deleted = self.repository.delete_account(int(bank_account_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=bank_account_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=bank_account_id,
                              entity_label=ENTITY_LABEL, record_label=self._label(before), before=before)
            if before["owns_account"]:
                account = self.accounts.get_account(int(before["account_id"]), ex=tx, for_update=True)
                if account is not None and not account["has_children"]:
                    self.accounts.delete_account(int(account["id"]), ex=tx)
                    self.audit.record(tx, action="delete", entity=ACCOUNT_ENTITY, entity_id=account["id"],
                                      entity_label=ACCOUNT_LABEL,
                                      record_label=f"{account['code']} {account['name_ar']}", before=account)
