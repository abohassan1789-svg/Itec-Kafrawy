"""شاشة «مجموعات الصلاحيات» — النموذج 3 «شجرة + خانات الشاشة المختارة» (اختيار المستخدم 2026-11-07).

على الـ Base Screen (شريط الأدوات فوق زي شاشة المستخدمين):
* يمين: قائمة المجموعات (بحث + جدول) وتحتها بيانات المجموعة (الكود، الاسم عربي/إنجليزي، الوصف، نشطة).
* شمال: صلاحيات المجموعة — شجرة الشاشات مترتبة زي القائمة الجانبية + خانات إجراءات الشاشة المختارة
  + تحديد الكل / مسح الكل / مزامنة الصلاحيات.
* الصلاحيات بتتعدّل في «جديد/تعديل» وبتتحفظ مع بيانات المجموعة بـ «حفظ» واحد (عملية واحدة في القاعدة).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.security.session import SESSION
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.permission_tree import PermissionTreeEditor
from app.ui.common.theme import button_style


class RolesScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "roles"
    TITLE = "مجموعات الصلاحيات"
    ICON = "🛡"
    FORM_TITLE = "بيانات المجموعة"
    LIST_TITLE = "المجموعات"
    NEW_TEXT = "مجموعة جديدة"
    SEARCH_PLACEHOLDER = "🔍 بحث بالكود أو الاسم…"
    COLUMNS = [("role_code", "الكود"), ("role_name_ar", "المجموعة"), ("users_count", "المستخدمين"),
               ("is_active", "الحالة")]

    def __init__(self, permissions: PermissionService | None = None, parent: QWidget | None = None) -> None:
        self.permissions = permissions or PermissionService()
        self.editor = PermissionTreeEditor(self._ask_password)
        super().__init__(parent)
        self.setMinimumSize(1150, 640)
        self.reload_tree()

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = QFormLayout(box)
        form.setContentsMargins(12, 18, 12, 10)
        form.setSpacing(8)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.role_code = QLineEdit()
        self.role_code.setMaxLength(50)
        self.role_name_ar = QLineEdit()
        self.role_name_ar.setMaxLength(120)
        self.role_name_en = QLineEdit()
        self.role_name_en.setMaxLength(120)
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(70)
        self.is_active = QCheckBox("نشطة")
        form.addRow("الكود", self.role_code)
        form.addRow("الاسم عربي", self.role_name_ar)
        form.addRow("الاسم إنجليزي", self.role_name_en)
        form.addRow("الوصف", self.description)
        form.addRow("الحالة", self.is_active)

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(list_box, 3)
        right.addWidget(form_box, 2)
        content.addLayout(right, 4)                              # RTL: المجموعات يمين

        perms_box = QGroupBox("صلاحيات المجموعة")
        layout = QVBoxLayout(perms_box)
        layout.setContentsMargins(10, 18, 10, 8)
        self.sync_button = QPushButton("مزامنة الصلاحيات")
        self.sync_button.setStyleSheet(button_style("#B45309", "#92400E"))
        self.sync_button.setToolTip("بتضيف صلاحيات الشاشات الجديدة (وبتحصل لوحدها كمان وقت فتح البرنامج)")
        self.sync_button.clicked.connect(self.sync_permissions)
        self.editor.top_bar.addWidget(self.sync_button)       # جنب تحديد الكل / مسح الكل
        layout.addWidget(self.editor, 1)
        align_form_right(perms_box)
        content.addWidget(perms_box, 7)

    def editable_widgets(self) -> list[QWidget]:
        return [self.role_code, self.role_name_ar, self.role_name_en, self.description, self.is_active]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        self.editor.set_editable(mode in ("new", "edit"))

    def update_extra_buttons(self) -> None:
        self.sync_button.setEnabled(self.mode == "view" and self.can("save"))

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "is_active":
            return "نشطة" if value else "موقوفة"
        return default_format(key, value, row)

    # ---- البيانات ----------------------------------------------------------
    def reload_tree(self) -> None:
        try:
            self.editor.set_entries(self.permissions.grouped_matrix())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الصلاحيات", exc)
            return
        if self.current_id is not None:
            self._load_grants(self.current_id)
        else:
            self.editor.load({}, set())

    def _load_grants(self, role_id: Any) -> None:
        self.editor.load(self.permissions.get_role_permission_map(int(role_id)),
                         self.permissions.get_role_password_ids(int(role_id)))

    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.permissions.list_roles(keyword)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.permissions.get_role(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.role_code.setText(record.get("role_code") or "")
        self.role_name_ar.setText(record.get("role_name_ar") or "")
        self.role_name_en.setText(record.get("role_name_en") or "")
        self.description.setPlainText(record.get("description") or "")
        self.is_active.setChecked(bool(record.get("is_active")))
        self._load_grants(record["id"])

    def clear_form(self) -> None:
        for field in (self.role_code, self.role_name_ar, self.role_name_en):
            field.clear()
        self.description.clear()
        self.is_active.setChecked(True)
        self.editor.load({}, set())

    def collect(self) -> dict:
        # إجراء حساس متعلّم عليه من غير كلمة سر (من «تحديد الكل» مثلاً) ← كلمة سر واحدة لكل نوع.
        for action, pids in self.editor.missing_passwords().items():
            from app.services import permission_registry as reg

            name = reg.ACTION_NAMES_AR[action]
            password = self._ask_password(f"«{name}» متعلّم عليه في {len(pids)} شاشة من غير كلمة سر — "
                                          "كلمة السر دي هتتحط لهم كلهم.", name)
            if password is None:
                raise BusinessError(f"مااتحفظش — «{name}» لازم يكون له كلمة سر.")
            for pid in pids:
                self.editor.pending_passwords[pid] = password
        return {
            "role_code": self.role_code.text(),
            "role_name_ar": self.role_name_ar.text(),
            "role_name_en": self.role_name_en.text(),
            "description": self.description.toPlainText(),
            "is_active": self.is_active.isChecked(),
            "allowed": self.editor.allowed_map(),
            "passwords": dict(self.editor.pending_passwords),
        }

    def save_record(self, payload: dict, record_id: Any) -> Any:
        allowed = payload.pop("allowed")
        passwords = payload.pop("passwords")
        saved = self.permissions.save_role(None if record_id is None else int(record_id), payload, allowed, passwords,
                                           expected_version=self.current_version)
        self._reload_own_permissions(saved)
        return saved

    def delete_record(self, record_id: Any) -> None:
        self.permissions.delete_role(int(record_id), expected_version=self.current_version)

    def _reload_own_permissions(self, role_id: int) -> None:
        """لو المستخدم الداخل في المجموعة دي ← صلاحياته الجديدة تشتغل على طول."""
        user = SESSION.user
        if not user or user.get("role_id") != role_id:
            return
        fresh = self.permissions.repository.get_user(SESSION.user_id)
        if fresh is not None:
            SESSION.reload_permissions(self.permissions.effective_codes(fresh))

    # ---- الأزرار -----------------------------------------------------------
    def _ask_password(self, where: str, action: str) -> str | None:
        from app.ui.dialogs.action_password_dialog import ask_new_action_password

        return ask_new_action_password(self, where, action)

    def sync_permissions(self) -> None:
        try:
            summary = self.permissions.sync_permissions()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّرت المزامنة", exc)
            return
        self.reload_tree()
        messages.info(self, "تمت المزامنة",
                      f"إجمالي الصلاحيات: {summary['total']}\nجديدة: {summary['added']}\n"
                      f"اتعطّلت: {summary['deactivated']}")
