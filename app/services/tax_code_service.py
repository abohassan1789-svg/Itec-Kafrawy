"""أكواد الضريبة (شاشة «أكواد الضريبة» — المرحلة 1ب، النموذج 1).

قرارات المستخدم (2026-11-08):
* الفاتورة الواحدة ممكن يبقى فيها **أكتر من ضريبة** في جدول ضرايب لوحده.
* الضرايب **نوعين**: **إضافة** (بتزوّد الإجمالي — قيمة مضافة، جدول) و**خصم** (بتقلّله — الخصم من المنبع).
* كل كود: الكود (إنجليزي كابيتال، ثابت بعد الإضافة)، الاسم، النوع، النسبة (0–100)، بتتحسب على
  (الصافي / الصافي + ضرايب الإضافة اللي ترتيبها قبلها)، الترتيب (1–9)، بتتطبق على (مبيعات / مشتريات /
  الاتنين)، نشطة، معفي (من غير نسبة — إضافة على الصافي).
* ``calculate`` = نفس الحساب اللي الفاتورة هتستخدمه: صافي 10,000 + جدول 8% (800) + ق.م 14% على
  10,800 (1,512) − خصم 1% (100) = 12,212.
* كل حفظ/مسح بيتسجل في ``sec.audit_log`` في نفس الـ transaction.
* **الحسابات (سكريبت 0010):** كل كود ليه حساب مبيعات وحساب مشتريات من الدليل (فرعي نشط في الميزانية).
  لو الكود بيتطبق على ناحية ونسبته أكبر من صفر ومااخترتش حساب ← بياخد حساب **الدور** لوحده
  (إضافة: ض.ق.م مخرجات / مدخلات · خصم: خصم المنبع لينا / علينا). الناحية اللي الكود مابيتطبقش عليها حسابها فاضي.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.tax_code_repository import TaxCodeRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

KIND_ADD, KIND_DEDUCT = "add", "deduct"
KINDS = {KIND_ADD: "إضافة", KIND_DEDUCT: "خصم"}
BASES = {"net": "الصافي بس", "cum": "الصافي + ضرايب الإضافة اللي قبلها"}
APPLIES = {"both": "المبيعات والمشتريات", "sales": "المبيعات", "purch": "المشتريات"}
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,9}$")
CENT = Decimal("0.01")
ENTITY, ENTITY_LABEL = "acc.tax_codes", "كود ضريبة"
# الناحية ← (عمود الحساب، اسمه)
SIDES = {"sales": ("sales_account_id", "حساب المبيعات"), "purch": ("purchase_account_id", "حساب المشتريات")}
# (النوع، الناحية) ← دور الحساب الافتراضي في الدليل
ACCOUNT_ROLES = {("add", "sales"): "vat_out", ("add", "purch"): "vat_in",
                 ("deduct", "sales"): "wht_rec", ("deduct", "purch"): "wht_pay"}


def fmt_rate(value: Any) -> str:
    """النسبة من غير أصفار زيادة: 14 ← 14% · 2.5 ← 2.5%."""
    text = f"{Decimal(value or 0).normalize():f}"
    return f"{text}%"


def fmt_money(value: Any) -> str:
    return f"{Decimal(value):,.2f}"


class TaxCodeService:
    def __init__(self, db: Database | None = None, repository: TaxCodeRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or TaxCodeRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_codes(self, keyword: str = "") -> list[dict[str, Any]]:
        return self.repository.list_codes(keyword.strip())

    def get_code(self, tax_id: int) -> dict[str, Any] | None:
        return self.repository.get_code(int(tax_id))

    def account_choices(self) -> list[dict[str, Any]]:
        """الحسابات اللي ينفع تتربط بكود ضريبة (فرعي نشط في الميزانية)."""
        return self.repository.account_choices()

    def suggested_accounts(self, kind: str) -> dict[str, int | None]:
        """حساب المبيعات والمشتريات الافتراضي للنوع ده (بالدور)."""
        out: dict[str, int | None] = {}
        for side, (field, _label) in SIDES.items():
            row = self.repository.role_account(ACCOUNT_ROLES[(kind if kind in KINDS else KIND_ADD, side)])
            out[field] = int(row["id"]) if row else None
        return out

    def _resolve_accounts(self, clean: dict[str, Any], data: dict[str, Any], before: dict[str, Any] | None,
                          ex) -> None:
        """حساب المبيعات / المشتريات: الناحية اللي مش بتتطبق = فاضي · المطلوب ومش متحدد = حساب الدور."""
        allowed = {int(r["id"]) for r in self.repository.account_choices(ex=ex)}
        for side, (field, label) in SIDES.items():
            if clean["applies_to"] not in ("both", side):
                clean[field] = None
                continue
            value = data.get(field)
            value = int(value) if value not in (None, "") else None
            if value is None and clean["rate"] > 0 and not clean["is_exempt"]:
                role = self.repository.role_account(ACCOUNT_ROLES[(clean["kind"], side)], ex=ex)
                if role is None:
                    raise BusinessError(f"لازم تختار «{label}» للضريبة دي (مفيش حساب في الدليل بالدور بتاعها).")
                value = int(role["id"])
            if value is not None and value not in allowed and value != (before or {}).get(field):
                raise BusinessError(f"«{label}» لازم يبقى حساب فرعي نشط من حسابات الميزانية (أصول أو خصوم).")
            clean[field] = value

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {"total": len(rows),
                "add": sum(1 for r in rows if r["kind"] == KIND_ADD),
                "deduct": sum(1 for r in rows if r["kind"] == KIND_DEDUCT),
                "inactive": sum(1 for r in rows if not r["is_active"])}

    # ---- الحساب ------------------------------------------------------------
    @staticmethod
    def calculate(net: Any, taxes: list[dict[str, Any]]) -> dict[str, Any]:
        """جدول ضرايب الفاتورة: بالترتيب، و«الصافي + اللي قبلها» = الصافي + ضرايب الإضافة اللي ترتيبها أقل."""
        net = Decimal(str(net or 0)).quantize(CENT)
        rows: list[dict[str, Any]] = []
        for tax in sorted(taxes, key=lambda t: (int(t["calc_order"]), t["code"])):
            if tax.get("is_exempt"):
                rows.append({"tax": tax, "base": net, "amount": Decimal("0.00")})
                continue
            base = net
            if tax["calc_base"] == "cum":
                base += sum((r["amount"] for r in rows
                             if r["tax"]["kind"] == KIND_ADD and int(r["tax"]["calc_order"]) < int(tax["calc_order"])),
                            Decimal("0"))
            amount = (base * Decimal(tax["rate"]) / 100).quantize(CENT, ROUND_HALF_UP)
            rows.append({"tax": tax, "base": base, "amount": amount})
        added = sum((r["amount"] for r in rows if r["tax"]["kind"] == KIND_ADD), Decimal("0.00"))
        deducted = sum((r["amount"] for r in rows if r["tax"]["kind"] == KIND_DEDUCT), Decimal("0.00"))
        return {"net": net, "rows": rows, "added": added, "deducted": deducted, "total": net + added - deducted}

    # ---- حفظ / مسح ---------------------------------------------------------
    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        code = (data.get("code") or "").strip().upper()
        if not _CODE.match(code):
            raise BusinessError("كود الضريبة لازم يبدأ بحرف إنجليزي، وحروف إنجليزي وأرقام بس لحد 10 (مثال: VAT14).")
        name = (data.get("name_ar") or "").strip()
        if not name:
            raise BusinessError("اسم الضريبة مطلوب.")
        if len(name) > 60:
            raise BusinessError("«الاسم» أطول من المسموح (60 حرف).")
        kind = data.get("kind") or KIND_ADD
        if kind not in KINDS:
            raise BusinessError("نوع الضريبة لازم يبقى «إضافة» أو «خصم».")
        try:
            rate = Decimal(str(data.get("rate") if data.get("rate") is not None else 0)).quantize(Decimal("0.0001"))
        except (InvalidOperation, ValueError) as exc:
            raise BusinessError("النسبة لازم تبقى رقم.") from exc
        if not Decimal(0) <= rate <= Decimal(100):
            raise BusinessError("النسبة لازم تبقى من 0 لـ 100.")
        base = data.get("calc_base") or "net"
        if base not in BASES:
            raise BusinessError("«بتتحسب على» لازم تبقى «الصافي» أو «الصافي + اللي قبلها».")
        try:
            order = int(data["calc_order"]) if data.get("calc_order") is not None else 1
        except (TypeError, ValueError) as exc:
            raise BusinessError("الترتيب لازم يبقى رقم من 1 لـ 9.") from exc
        if not 1 <= order <= 9:
            raise BusinessError("الترتيب لازم يبقى رقم من 1 لـ 9.")
        applies = data.get("applies_to") or "both"
        if applies not in APPLIES:
            raise BusinessError("«بتتطبق على» لازم تبقى المبيعات أو المشتريات أو الاتنين.")
        exempt = bool(data.get("is_exempt"))
        if exempt:                                   # المعفي: من غير نسبة، إضافة، على الصافي
            if kind != KIND_ADD:
                raise BusinessError("المعفي بيبقى من ضرايب الإضافة (زي القيمة المضافة) — مش خصم.")
            rate, base = Decimal("0.0000"), "net"
        return {"code": code, "name_ar": name, "kind": kind, "rate": rate, "calc_base": base,
                "calc_order": order, "applies_to": applies, "is_exempt": exempt,
                "is_active": bool(data.get("is_active", True))}

    def save_code(self, tax_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require("sys.tax_codes.create" if tax_id is None else "sys.tax_codes.edit")
        PermissionService.require("sys.tax_codes.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if tax_id is not None:
                before = self.repository.get_code(int(tax_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=tax_id)
                if before is None:
                    raise BusinessError("كود الضريبة ده مش موجود (ممكن يكون اتمسح).")
                clean["code"] = before["code"]                       # الكود ثابت بعد الإضافة
            elif self.repository.code_exists(clean["code"], ex=tx):
                raise BusinessError(f"كود الضريبة «{clean['code']}» متسجل قبل كده.")
            self._resolve_accounts(clean, data, before, tx)
            if tax_id is None:
                tax_id = self.repository.insert_code(clean, ex=tx)
            else:
                updated = self.repository.update_code(int(tax_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=tax_id)
            after = self.repository.get_code(int(tax_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=tax_id, entity_label=ENTITY_LABEL, record_label=clean["code"],
                              before=before, after=after)
        return int(tax_id)

    def delete_code(self, tax_id: int, expected_version: int | None = None) -> None:
        PermissionService.require("sys.tax_codes.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_code(int(tax_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=tax_id)
            if before is None:
                raise BusinessError("كود الضريبة ده مش موجود (ممكن يكون اتمسح).")
            deleted = self.repository.delete_code(int(tax_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=tax_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=tax_id, entity_label=ENTITY_LABEL,
                              record_label=before["code"], before=before)
