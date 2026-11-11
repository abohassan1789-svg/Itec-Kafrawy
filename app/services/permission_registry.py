"""سجل الشاشات والتقارير والصلاحيات — المصدر الوحيد للقائمة الجانبية ومصفوفة الصلاحيات.

نفس فكرة Hr GAD (docs/02 القسم 3.3):
* كود الصلاحية دايماً ``module.target.action`` (مثلاً ``sys.users.edit``).
* القائمة الجانبية ومصفوفة الصلاحيات **الاتنين** بيتبنوا من ``SECTIONS`` هنا — فأي شاشة جديدة
  بتتضاف هنا مرة واحدة ← تظهر في القائمة، وصلاحياتها تظهر في المصفوفة بعد المزامنة.
* ``module_code`` = id القسم.

إضافة شاشة جديدة: ضيف ``Target`` في القسم بتاعها. لو الشاشة مش محتاجة كل الإجراءات
(زي سجل الدخول: عرض/تصدير بس). الطباعة بتتضاف لما نعمل الطباعة. حدد ``actions``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# (الكود، عربي، إنجليزي) — الترتيب = ترتيب أعمدة المصفوفة.
SCREEN_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("view", "عرض", "View"),
    ("create", "إضافة", "Create"),
    ("edit", "تعديل", "Edit"),
    ("save", "حفظ", "Save"),
    ("delete", "حذف", "Delete"),
    ("delete_all", "حذف الكل", "Delete All"),
    ("approve", "اعتماد", "Approve"),
    ("print", "طباعة", "Print"),
    ("export", "تصدير", "Export"),
    ("import", "استيراد", "Import"),
    ("show_totals", "إظهار الإجماليات", "Show Totals"),
    ("post", "ترحيل", "Post"),
    ("unpost", "إلغاء الترحيل", "Unpost"),
    ("lock", "قفل / فتح", "Lock / Unlock"),
)

REPORT_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("view", "عرض", "View"),
    ("preview", "معاينة", "Preview"),
    ("filter", "تصفية", "Filter"),
    ("print", "طباعة", "Print"),
    ("export", "تصدير", "Export"),
)

# إجراءات حساسة بيتحطلها كلمة سر وقت المنح، والبرنامج بيسأل عليها قبل التنفيذ.
# «قفل / فتح» (السنة المالية) حساس برضه: بيوقف/يفتح الترحيل في سنة كاملة.
PASSWORD_ACTIONS: tuple[str, ...] = ("delete_all", "show_totals", "lock")

ACTION_NAMES_AR = {code: ar for code, ar, _en in (*SCREEN_ACTIONS, *REPORT_ACTIONS)}
ACTION_NAMES_EN = {code: en for code, _ar, en in (*SCREEN_ACTIONS, *REPORT_ACTIONS)}

CATEGORY = {"screen": ("الشاشات", "Screens"), "report": ("التقارير", "Reports")}


@dataclass(frozen=True)
class Target:
    """شاشة أو تقرير في القائمة."""

    code: str                      # target_code (مفتاح الشاشة)
    name_ar: str
    name_en: str
    kind: str = "screen"           # screen / report
    actions: tuple[str, ...] = ()  # فاضي = كل إجراءات النوع
    icon: str = ""

    def action_codes(self) -> tuple[str, ...]:
        all_codes = tuple(c for c, _a, _e in (REPORT_ACTIONS if self.kind == "report" else SCREEN_ACTIONS))
        if not self.actions:
            return all_codes
        unknown = set(self.actions) - set(all_codes)
        if unknown:
            raise ValueError(f"إجراءات مش معروفة للشاشة {self.code}: {sorted(unknown)}")
        return tuple(c for c in all_codes if c in self.actions)   # بنفس ترتيب الأعمدة


@dataclass(frozen=True)
class Group:
    title: str | None              # None = من غير عنوان فرعي
    targets: tuple[Target, ...]
    icon: str = ""


@dataclass(frozen=True)
class Section:
    id: str                        # = module_code
    title: str
    title_en: str
    icon: str
    groups: tuple[Group, ...] = field(default_factory=tuple)
    bottom: bool = False           # «النظام والصلاحيات» تحت لوحده في الشريط

    def targets(self) -> list[Target]:
        return [t for g in self.groups for t in g.targets]


# ---------------------------------------------------------------------------
# الأقسام (نفس ترتيب الشريط الأخضر). الأقسام الفاضية بتتملى في المراحل الجاية.
# ---------------------------------------------------------------------------
SECTIONS: tuple[Section, ...] = (
    Section("acc", "الحسابات", "Accounts", "📒", groups=(
        Group("دليل الحسابات", (
            Target("chart_of_accounts", "شجرة الحسابات", "Chart of Accounts", icon="🌳",
                   actions=("view", "create", "edit", "save", "delete", "export")),
            Target("default_accounts", "الحسابات الافتراضية", "Default Accounts", icon="🔗",
                   actions=("view", "edit", "save", "export")),
            Target("cost_centers", "مراكز التكلفة", "Cost Centers", icon="🎯",
                   actions=("view", "create", "edit", "save", "delete", "export")),
        ), icon="🗂"),
    )),
    Section("sales", "المبيعات", "Sales", "🛒", groups=(
        Group("العملاء", (
            Target("customer_groups", "مجموعات العملاء", "Customer Groups", icon="🗂️",
                   actions=("view", "create", "edit", "save", "delete", "export")),
            Target("customers", "العملاء", "Customers", icon="👥",
                   actions=("view", "create", "edit", "save", "delete", "export")),
        ), icon="👥"),
        Group("أوامر التوريد", (
            Target("projects", "أوامر التوريد", "Supply Orders", icon="📋",
                   actions=("view", "create", "edit", "save", "delete", "export")),
        ), icon="📋"),
    )),
    Section("purch", "المشتريات", "Purchases", "📦", groups=(
        Group("الموردين", (
            Target("supplier_groups", "مجموعات الموردين", "Supplier Groups", icon="🗂️",
                   actions=("view", "create", "edit", "save", "delete", "export")),
            Target("suppliers", "الموردين", "Suppliers", icon="🚚",
                   actions=("view", "create", "edit", "save", "delete", "export")),
        ), icon="🚚"),
    )),
    # قسم لوحده (قرار المستخدم 2026-11-11) عشان يتضاف فيه حاجات تانية بعدين.
    Section("hr", "الموظفين", "Employees", "🪪", groups=(
        Group("الموظفين", (
            Target("employees", "الموظفين", "Employees", icon="🪪",
                   actions=("view", "create", "edit", "save", "delete", "export")),
        ), icon="🪪"),
    )),
    Section("inv", "المخازن", "Inventory", "🏬"),
    Section("cash", "الخزينة", "Cash & Banks", "💵"),
    Section("rep", "التقارير", "Reports", "📈"),
    Section("sys", "النظام والصلاحيات", "System & Security", "🔐", bottom=True, groups=(
        Group("الإعدادات العامة", (
            Target("company", "بيانات الشركة", "Company Info", icon="🏢",
                   actions=("view", "edit", "save")),
            Target("fiscal_years", "السنة المالية والفترات", "Fiscal Years & Periods", icon="📅",
                   actions=("view", "create", "edit", "save", "delete", "export", "lock")),
            Target("currencies", "العملات وأسعار الصرف", "Currencies & Exchange Rates", icon="💱",
                   actions=("view", "create", "edit", "save", "delete", "export")),
            Target("tax_codes", "أكواد الضريبة", "Tax Codes", icon="🧾",
                   actions=("view", "create", "edit", "save", "delete", "export")),
        ), icon="⚙"),
        Group("المستخدمين والصلاحيات", (
            Target("users", "المستخدمين", "Users", icon="👥",
                   actions=("view", "create", "edit", "save", "export")),
            Target("roles", "مجموعات الصلاحيات", "Permission Groups", icon="🛡",
                   actions=("view", "create", "edit", "save", "delete", "export")),
            Target("user_permissions", "صلاحيات المستخدم", "User Permissions", icon="🔑",
                   actions=("view", "edit", "save", "export")),
        ), icon="👥"),
        Group("السجلات", (
            Target("login_logs", "سجل الدخول", "Login Log", icon="🕘",
                   actions=("view", "export")),
        ), icon="🕘"),
    )),
)


def section(section_id: str) -> Section:
    for s in SECTIONS:
        if s.id == section_id:
            return s
    raise KeyError(section_id)


def find_target(module_code: str, target_code: str) -> Target:
    for t in section(module_code).targets():
        if t.code == target_code:
            return t
    raise KeyError(f"{module_code}.{target_code}")


def permission_code(module_code: str, target_code: str, action: str) -> str:
    return f"{module_code}.{target_code}.{action}"


def build_permission_rows() -> list[dict[str, Any]]:
    """كل صفوف جدول ``sec.permissions`` المتوقعة، بترتيب القائمة."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    order = 0
    for s in SECTIONS:
        for t in s.targets():
            cat_ar, cat_en = CATEGORY[t.kind]
            for action in t.action_codes():
                code = permission_code(s.id, t.code, action)
                if code in seen:
                    raise ValueError(f"كود صلاحية متكرر في السجل: {code}")
                seen.add(code)
                order += 1
                rows.append({
                    "permission_code": code,
                    "permission_type": t.kind,
                    "module_code": s.id,
                    "module_name_ar": s.title,
                    "module_name_en": s.title_en,
                    "category_ar": cat_ar,
                    "category_en": cat_en,
                    "target_code": t.code,
                    "target_name_ar": t.name_ar,
                    "target_name_en": t.name_en,
                    "action_code": action,
                    "action_name_ar": ACTION_NAMES_AR[action],
                    "action_name_en": ACTION_NAMES_EN[action],
                    "sort_order": order,
                })
    return rows
