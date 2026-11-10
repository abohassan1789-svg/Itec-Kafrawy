"""شجرة الحسابات (شاشة «شجرة الحسابات» — المرحلة 2، النموذج 6 «تبويب لكل نوع»).

القرارات (docs/03 القسم 3.1 و6 و8 + اختيار المستخدم 2026-11-08):
* الكود أرقام بس، **بيبدأ بكود الأب وأطول منه**، ولحد **5 مستويات**. الكود الجاي بيتقترح لوحده
  (``suggest_code``): تحت الجذر رقم واحد زيادة (``1`` ← ``11``، ``12``…) وبعد كده رقمين
  (``11`` ← ``1101`` ← ``110101``) — والمستخدم يقدر يغيّره بنفس الشرط.
* الحساب الجديد بيورث من أبوه: النوع والقائمة (ثابتين) + الطبيعة والتصنيف والتدفقات والدور والطرف ومركز
  التكلفة والعملة (اقتراح — يتغيّروا).
* «رئيسي» = تجميعي (مابيقبلش قيود) · «فرعي» = بيقبل قيود.
* العملاء والموردين = **حساب رقابة واحد + بُعد الطرف** (``party_kind`` = لازم طرف مع القيد).
* «نظامي» = مايتمسحش ولا يتوقف، وكوده ومكانه ودوره ونوعه ثابتين.
* القاعدة نفسها بتحمي نفس القواعد (trigger ``acc.accounts_guard``) — هنا الرسايل بتطلع أوضح وقبل الحفظ.
* كل حفظ/مسح بيتسجل في ``sec.audit_log`` في نفس الـ transaction، وبحماية التعديل في نفس الوقت (row_version).
* **تفريع حساب فرعي** (طلب المستخدم 2026-11-08 — «+ فرعي تحت المحدد»، سكريبت 0011): الحساب بيتحوّل رئيسي
  والحساب الجديد بيتضاف تحته في **نفس الـ transaction** (``branch_account``). **مع كل تفريع** (قرار المستخدم:
  «عام» لكل تفريع) البرنامج بيعمل أول حساب تحته لوحده «<الاسم> — عام» بياخد الدور والنظامي وربط الضريبة
  (والقيود القديمة — المرحلة 4) — عشان المستندات تفضل عارفة تقيّد فين.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.database.db import Database, get_db
from app.repositories.account_repository import AccountRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

MAX_LEVEL = 5
ENTITY, ENTITY_LABEL = "acc.accounts", "حساب"
PERM = "acc.chart_of_accounts"
_CODE = re.compile(r"^[0-9]{1,20}$")

# النوع: (اسم التبويب، اسم مختصر، كود الجذر، اللون)
TYPES: dict[str, tuple[str, str, str, str]] = {
    "asset": ("الأصول", "أصول", "1", "#1D4ED8"),
    "liability": ("الخصوم", "خصوم", "2", "#B45309"),
    "equity": ("حقوق الملكية", "حقوق ملكية", "3", "#6D28D9"),
    "revenue": ("الإيرادات", "إيرادات", "4", "#087443"),
    "expense": ("المصروفات", "مصروفات", "5", "#B91C1C"),
}
STATEMENTS = {"bs": "الميزانية", "is": "قائمة الدخل"}
NATURES = {"dr": "مدين", "cr": "دائن"}
CLASSIFICATIONS: dict[str, dict[str, str]] = {
    "asset": {"current": "أصول متداولة", "noncurrent": "أصول غير متداولة"},
    "liability": {"current": "خصوم متداولة", "noncurrent": "خصوم غير متداولة"},
    "equity": {"equity": "حقوق الملكية"},
    "revenue": {"operating_rev": "إيرادات النشاط", "other_rev": "إيرادات أخرى", "returns": "مردودات ومسموحات"},
    "expense": {"cogs": "تكلفة المبيعات", "operating_exp": "مصروفات تشغيل", "finance_exp": "مصروفات تمويلية",
                "other_exp": "مصروفات أخرى"},
}
CASH_FLOWS = {"op": "أنشطة تشغيلية", "inv": "أنشطة استثمارية", "fin": "أنشطة تمويلية",
              "cash": "نقدية (مش بند)", "none": "— مش في التدفقات"}
PARTY_KINDS = {"customer": "عميل", "supplier": "مورد", "employee": "موظف", "partner": "شريك"}
GENERAL_SUFFIX = " — عام"
BRANCH_BUTTON = "+ فرعي تحت المحدد"


def general_name(name_ar: str) -> str:
    """اسم أول حساب بيعمله التفريع: «مبيعات بضاعة — عام» (نفس اللي بتعمله acc.split_account)."""
    return f"{name_ar[:120 - len(GENERAL_SUFFIX)]}{GENERAL_SUFFIX}"


def type_of(account: dict[str, Any]) -> str:
    return account.get("account_type") or "asset"


def code_width(parent_level: int) -> int:
    """عدد أرقام الكود اللي بتتزوّد تحت الأب: تحت الجذر رقم، وبعد كده رقمين."""
    return 1 if parent_level <= 1 else 2


def next_child_code(parent_code: str, parent_level: int, child_codes: Iterable[str]) -> str:
    """الكود الجاي تحت الأب (أكبر كود بنفس الطول + 1، ولو خلصوا أول رقم فاضي). "" = مفيش مكان."""
    width = code_width(parent_level)
    size = len(parent_code) + width
    used = {int(c[len(parent_code):]) for c in child_codes
            if len(c) == size and c.startswith(parent_code) and c.isdigit()}
    limit = 10 ** width - 1
    candidate = max(used) + 1 if used else 1
    if candidate > limit:
        free = [n for n in range(1, limit + 1) if n not in used]
        if not free:
            return ""
        candidate = free[0]
    return f"{parent_code}{candidate:0{width}d}"


def matching_ids(rows: list[dict[str, Any]], keyword: str) -> set[Any]:
    """الحسابات اللي بتطابق البحث (الكود / الاسم ع / E)."""
    needle = (keyword or "").strip().casefold()
    return {r["id"] for r in rows
            if needle and needle in f"{r.get('code') or ''} {r.get('name_ar') or ''} {r.get('name_en') or ''}".casefold()}


def visible_ids(rows: list[dict[str, Any]], keyword: str) -> set[Any] | None:
    """البحث في الشجرة: الحسابات اللي بتطابق + آباءها (عشان مكانها يبان). None = من غير بحث (الكل)."""
    if not (keyword or "").strip():
        return None
    by_id = {r["id"]: r for r in rows}
    matches = matching_ids(rows, keyword)
    shown: set[Any] = set()
    for row in rows:
        if row["id"] in matches:
            node: dict | None = row
            while node is not None and node["id"] not in shown:
                shown.add(node["id"])
                node = by_id.get(node.get("parent_id"))
    return shown


class AccountService:
    def __init__(self, db: Database | None = None, repository: AccountRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or AccountRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_accounts(self) -> list[dict[str, Any]]:
        return self.repository.list_accounts()

    def get_account(self, account_id: int) -> dict[str, Any] | None:
        return self.repository.get_account(int(account_id))

    def list_roles(self) -> list[dict[str, Any]]:
        return self.repository.list_roles()

    def roles_for_type(self, account_type: str) -> list[dict[str, Any]]:
        return [r for r in self.list_roles() if r["account_type"] in (None, account_type)]

    def list_currencies(self) -> list[dict[str, Any]]:
        return self.repository.list_currencies()

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
        """لكل نوع: عدد الحسابات والفرعي والرئيسي والموقوف."""
        out = {t: {"total": 0, "leaf": 0, "main": 0, "inactive": 0} for t in TYPES}
        for r in rows:
            s = out[type_of(r)]
            s["total"] += 1
            s["leaf" if r["is_leaf"] else "main"] += 1
            s["inactive"] += 0 if r["is_active"] else 1
        return out

    # ---- الحساب الجديد (الكود المقترح + الوراثة) ------------------------------
    def suggest_code(self, parent_id: int) -> str:
        parent = self.repository.get_account(int(parent_id))
        if parent is None:
            return ""
        return next_child_code(parent["code"], int(parent["level"]), self.repository.child_codes(int(parent_id)))

    def new_defaults(self, parent_id: int) -> dict[str, Any]:
        """حساب جديد تحت الأب: الكود المقترح + المستوى والنوع والقائمة + اللي بيتورث (الطبيعة والتصنيف…)."""
        parent = self.repository.get_account(int(parent_id))
        if parent is None:
            raise BusinessError("الحساب الأب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        return self._child_defaults(
            parent, next_child_code(parent["code"], int(parent["level"]), self.repository.child_codes(parent["id"])))

    @staticmethod
    def _child_defaults(parent: dict[str, Any], code: str) -> dict[str, Any]:
        return {
            "parent_id": parent["id"], "parent_code": parent["code"], "parent_name": parent["name_ar"],
            "code": code,
            "level": int(parent["level"]) + 1, "account_type": parent["account_type"],
            "statement": parent["statement"], "nature": parent["nature"],
            "classification": parent["classification"], "cash_flow": parent["cash_flow"],
            "role_code": parent["role_code"], "party_kind": parent["party_kind"],
            "requires_cost_center": parent["requires_cost_center"], "currency_code": parent["currency_code"],
            "is_leaf": True, "is_active": True, "is_system": False, "has_children": False,
            "name_ar": "", "name_en": "",
        }

    # ---- التفريع («+ فرعي تحت المحدد» على حساب فرعي) -------------------------
    @staticmethod
    def _needs_general(account: dict[str, Any], links: list[str]) -> bool:
        """«— عام» **مع كل تفريع** (قرار المستخدم 2026-11-08) — نفس acc.split_account."""
        return True

    def _free_code(self, account: dict[str, Any], taken: list[str], ex=None) -> str:
        """أول كود فاضي تحت الحساب (بيشوف الدليل كله — ممكن حساب جنبه واخد نفس الكود)."""
        width = code_width(int(account["level"]))
        used = self.repository.codes_with_prefix(account["code"], len(account["code"]) + width, ex=ex)
        return next_child_code(account["code"], int(account["level"]), [*used, *taken])

    @staticmethod
    def _branch_checks(account: dict[str, Any] | None) -> None:
        if account is None:
            raise BusinessError("الحساب ده مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        label = f"«⁦{account['code']}⁩ {account['name_ar']}»"
        if not account["is_leaf"]:
            raise BusinessError(f"الحساب {label} رئيسي أصلاً — ضيف تحته على طول («حساب جديد»).")
        if int(account["level"]) >= MAX_LEVEL:
            raise BusinessError(f"الحساب {label} في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه.")
        if not account["is_active"]:
            raise BusinessError(f"الحساب {label} موقوف — نشّطه الأول وبعدين فرّع منه.")

    def branch_plan(self, account_id: int) -> dict[str, Any]:
        """اللي هيحصل لو فرّعنا الحساب الفرعي ده (من غير ما يتحفظ حاجة): «العام» + الحساب الجديد."""
        account = self.repository.get_account(int(account_id))
        self._branch_checks(account)
        links = self.repository.tax_links(account["id"])
        taken: list[str] = []
        general = None
        if self._needs_general(account, links):
            code = self._free_code(account, taken)
            if not code:
                raise BusinessError("مفيش كود فاضي تحت الحساب ده.")
            taken.append(code)
            general = {"code": code, "name_ar": general_name(account["name_ar"]),
                       "role_code": account["role_code"], "role_name": account["role_name"],
                       "is_system": bool(account["is_system"]), "tax_links": links,
                       "default_keys": self.repository.default_keys(account["id"])}
        code = self._free_code(account, taken)
        if not code:
            raise BusinessError("مفيش كود فاضي تحت الحساب ده.")
        return {"account": account, "general": general, "defaults": self._child_defaults(account, code),
                "expected_version": int(account["row_version"])}

    def branch_account(self, account_id: int, data: dict[str, Any], expected_version: int | None = None) -> int:
        """التفريع كله في transaction واحدة: قفل الحساب ← التأكد من الإصدار ← يتحوّل رئيسي (+ «— عام»)
        ← الحساب الجديد تحته ← سجل التعديلات. أي غلط = ولا حاجة اتحفظت. بيرجّع رقم الحساب الجديد."""
        PermissionService.require(f"{PERM}.create")
        PermissionService.require(f"{PERM}.edit")                 # الحساب نفسه بيتعدّل (فرعي ← رئيسي)
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_account(int(account_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=account_id)
            self._branch_checks(before)
            links = self.repository.tax_codes_of(before["id"], ex=tx)
            defaults = self.repository.default_rows_of(before["id"], ex=tx)
            general_code = None
            if self._needs_general(before, [t["code"] for t in links]):
                general_code = self._free_code(before, [], ex=tx)
            general_id = self.repository.split_account(before["id"], general_code, ex=tx)
            after = self.repository.get_account(before["id"], ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=before["id"], entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after,
                              extra={"split": {"general_code": general_code if general_id else None,
                                               "tax_codes": [t["code"] for t in links],
                                               "default_accounts": [d["key"] for d in defaults]}})
            if general_id is not None:
                general = self.repository.get_account(general_id, ex=tx)
                self.audit.record(tx, action="create", entity=ENTITY, entity_id=general_id,
                                  entity_label=ENTITY_LABEL, record_label=f"{general['code']} {general['name_ar']}",
                                  after=general, extra={"split_from": before["code"]})
                for old, new in zip(links, self.repository.tax_codes_by_ids([t["id"] for t in links], ex=tx)):
                    self.audit.record(tx, action="update", entity="acc.tax_codes", entity_id=old["id"],
                                      entity_label="كود ضريبة", record_label=old["code"], before=old, after=new,
                                      extra={"split_from": before["code"]})
                for old, new in zip(defaults, self.repository.default_rows_by_keys([d["key"] for d in defaults],
                                                                                   ex=tx)):
                    self.audit.record(tx, action="update", entity="acc.default_accounts", entity_id=old["key"],
                                      entity_label="حساب افتراضي", record_label=old["name_ar"], before=old,
                                      after=new, extra={"split_from": before["code"]})
            clean = self._clean({**data, "parent_id": before["id"]}, None, tx)
            new_id = self.repository.insert_account(clean, ex=tx)
            self.audit.record(tx, action="create", entity=ENTITY, entity_id=new_id, entity_label=ENTITY_LABEL,
                              record_label=f"{clean['code']} {clean['name_ar']}",
                              after=self.repository.get_account(new_id, ex=tx))
        return int(new_id)

    # ---- التحقق ------------------------------------------------------------
    def _clean(self, data: dict[str, Any], before: dict[str, Any] | None, ex) -> dict[str, Any]:
        code = str(data.get("code") or "").strip()
        if not _CODE.match(code):
            raise BusinessError("كود الحساب أرقام بس (من 1 لـ 20 رقم).")
        name = (data.get("name_ar") or "").strip()
        if not name:
            raise BusinessError("اسم الحساب (عربي) مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم عربي» أطول من المسموح (120 حرف).")
        name_en = (data.get("name_en") or "").strip() or None
        if name_en and len(name_en) > 120:
            raise BusinessError("«الاسم إنجليزي» أطول من المسموح (120 حرف).")
        is_leaf = bool(data.get("is_leaf", True))
        parent_id = data.get("parent_id")
        parent_id = int(parent_id) if parent_id not in (None, "") else None
        is_root = before is not None and before["parent_id"] is None
        if is_root:
            parent_id, level, account_type = None, 1, before["account_type"]
        else:
            if parent_id is None:
                raise BusinessError("لازم تختار الحساب اللي هيبقى تحته («تحت حساب»).")
            if before is not None and parent_id == before["id"]:
                raise BusinessError("الحساب مينفعش يبقى تحت نفسه.")
            parent = self.repository.get_account(parent_id, ex=ex)
            if parent is None:
                raise BusinessError("الحساب الأب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
            label = f"«⁦{parent['code']}⁩ {parent['name_ar']}»"
            if parent["is_leaf"]:
                raise BusinessError(f"الحساب {label} فرعي — مينفعش يبقى تحته حسابات "
                                    f"(فرّع منه بـ «{BRANCH_BUTTON}»).")
            moved = before is None or before["parent_id"] != parent_id
            reactivated = before is not None and not before["is_active"] and bool(data.get("is_active", True))
            if not parent["is_active"] and (moved or reactivated):
                raise BusinessError(f"الحساب {label} موقوف — مينفعش يبقى تحته حسابات نشطة أو جديدة.")
            if not code.startswith(parent["code"]) or len(code) <= len(parent["code"]):
                raise BusinessError(f"كود الحساب لازم يبدأ بكود الأب «⁦{parent['code']}⁩» ويبقى أطول منه.")
            level, account_type = int(parent["level"]) + 1, parent["account_type"]
            if level > MAX_LEVEL:
                raise BusinessError(f"أقصى عدد مستويات في الدليل {MAX_LEVEL} — مينفعش تضيف تحت {label}.")
        if level == MAX_LEVEL and not is_leaf:
            raise BusinessError("المستوى الخامس آخر مستوى — الحساب لازم يبقى «فرعي».")
        nature = data.get("nature") or ""
        if nature not in NATURES:
            raise BusinessError("الطبيعة لازم تبقى «مدين» أو «دائن».")
        classification = data.get("classification") or ""
        if classification not in CLASSIFICATIONS[account_type]:
            raise BusinessError(f"التصنيف ده مايناسبش حسابات «{TYPES[account_type][1]}» — اختار من القائمة.")
        cash_flow = data.get("cash_flow") or "none"
        if cash_flow not in CASH_FLOWS:
            raise BusinessError("بند التدفقات النقدية مش معروف.")
        role_code = data.get("role_code") or None
        if role_code is not None:
            role = next((r for r in self.repository.list_roles(ex=ex) if r["code"] == role_code), None)
            if role is None:
                raise BusinessError("الدور ده مش معروف.")
            if role["account_type"] not in (None, account_type):
                raise BusinessError(f"الدور «{role['name_ar']}» بيتحط على حسابات «{TYPES[role['account_type']][1]}» بس.")
        party_kind = data.get("party_kind") or None
        if party_kind is not None and party_kind not in PARTY_KINDS:
            raise BusinessError("نوع الطرف لازم يبقى عميل أو مورد أو موظف أو شريك.")
        currency = (data.get("currency_code") or "EGP").strip().upper()
        if currency not in {c["code"] for c in self.repository.list_currencies(ex=ex)}:
            raise BusinessError(f"العملة «⁦{currency}⁩» مش موجودة.")
        is_active = bool(data.get("is_active", True))
        if self.repository.code_exists(code, before["id"] if before else None, ex=ex):
            raise BusinessError(f"كود الحساب «⁦{code}⁩» متسجل قبل كده.")

        if before is not None:
            label = f"«⁦{before['code']}⁩ {before['name_ar']}»"
            if before["is_system"]:
                if before["is_leaf"] and not is_leaf:
                    raise BusinessError(f"الحساب {label} نظامي — مايتحوّلش رئيسي من «تعديل»؛ "
                                        f"عشان تفرّع منه دوس «{BRANCH_BUTTON}» (الدور هينزل لأول حساب تحته).")
                if (code != before["code"] or parent_id != before["parent_id"] or role_code != before["role_code"]
                        or is_leaf != before["is_leaf"]):
                    raise BusinessError(f"الحساب {label} نظامي — كوده ومكانه ودوره ونوعه (رئيسي/فرعي) ثابتين.")
                if not is_active:
                    raise BusinessError(f"الحساب {label} نظامي — مايتوقفش (البرنامج محتاجه).")
            if before["has_children"]:
                if code != before["code"] or parent_id != before["parent_id"]:
                    raise BusinessError(f"الحساب {label} تحته حسابات — مينفعش تغيّر كوده ولا مكانه.")
                if is_leaf:
                    raise BusinessError(f"الحساب {label} تحته حسابات — مينفعش يتحوّل «فرعي».")
                if not is_active and before["is_active"] and any(
                        r["parent_id"] == before["id"] and r["is_active"] for r in self.repository.list_accounts(ex=ex)):
                    raise BusinessError(f"الحساب {label} تحته حسابات نشطة — وقّفهم الأول.")
            if (before["is_active"] and not is_active) or (before["is_leaf"] and not is_leaf):
                links = self.repository.tax_links(before["id"], ex=ex)
                if links:
                    raise BusinessError(f"الحساب {label} مربوط بأكواد الضريبة ({'، '.join(links)}) — "
                                        "مينفعش يتوقف ولا يتحوّل رئيسي (غيّر الربط من شاشة أكواد الضريبة الأول، "
                                        f"أو فرّع منه بـ «{BRANCH_BUTTON}» والربط هينزل لأول حساب تحته).")
                keys = self.repository.default_keys(before["id"], ex=ex)
                if keys:
                    raise BusinessError(f"الحساب {label} افتراضي لـ ({'، '.join(keys)}) — مينفعش يتوقف ولا يتحوّل "
                                        "رئيسي (غيّر الحساب من شاشة «الحسابات الافتراضية» الأول، "
                                        f"أو فرّع منه بـ «{BRANCH_BUTTON}» والمفتاح هينزل لأول حساب تحته).")
        return {"code": code, "name_ar": name, "name_en": name_en, "parent_id": parent_id, "is_leaf": is_leaf,
                "account_type": account_type, "nature": nature, "classification": classification,
                "cash_flow": cash_flow, "role_code": role_code, "party_kind": party_kind,
                "requires_cost_center": bool(data.get("requires_cost_center")), "currency_code": currency,
                "is_active": is_active}

    # ---- حفظ / مسح ---------------------------------------------------------
    def save_account(self, account_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if account_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = None
            if account_id is not None:
                before = self.repository.get_account(int(account_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=account_id)
                if before is None:
                    raise BusinessError("الحساب ده مش موجود (ممكن يكون اتمسح).")
            clean = self._clean(data, before, tx)
            if before is None:
                account_id = self.repository.insert_account(clean, ex=tx)
            else:
                updated = self.repository.update_account(int(account_id), clean, ex=tx,
                                                         expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=account_id)
            after = self.repository.get_account(int(account_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=account_id, entity_label=ENTITY_LABEL,
                              record_label=f"{clean['code']} {clean['name_ar']}", before=before, after=after)
        return int(account_id)

    def delete_account(self, account_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_account(int(account_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=account_id)
            if before is None:
                raise BusinessError("الحساب ده مش موجود (ممكن يكون اتمسح).")
            label = f"«⁦{before['code']}⁩ {before['name_ar']}»"
            if before["is_system"]:
                raise BusinessError(f"الحساب {label} نظامي — مايتمسحش.")
            if before["has_children"]:
                raise BusinessError(f"الحساب {label} تحته حسابات — امسحهم الأول.")
            links = self.repository.tax_links(before["id"], ex=tx)
            if links:
                raise BusinessError(f"الحساب {label} مربوط بأكواد الضريبة ({'، '.join(links)}) — "
                                    "غيّر الربط من شاشة أكواد الضريبة الأول.")
            keys = self.repository.default_keys(before["id"], ex=tx)
            if keys:
                raise BusinessError(f"الحساب {label} افتراضي لـ ({'، '.join(keys)}) — "
                                    "غيّر الحساب من شاشة «الحسابات الافتراضية» الأول.")
            deleted = self.repository.delete_account(int(account_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=account_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=account_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
