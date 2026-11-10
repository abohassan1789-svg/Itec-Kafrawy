"""شاشة «صلاحيات المستخدم» — النموذج 1 «زي Hr GAD: مصفوفة الصلاحية الفعلية بالألوان» (اختيار المستخدم 2026-11-07).

على الـ Base Screen (شريط الأدوات فوق زي باقي الشاشات):
* يمين: المستخدمين (بحث + جدول وجنب كل واحد عدد تخصيصاته) وتحتها بيانات المستخدم ومجموعته.
* شمال: المصفوفة — كل خانة = الصلاحية الفعلية بلونها، والضغط بيلف وراثة ← سماح ← منع.
* «تصفير التخصيصات» و«🔑 كلمة سر «حذف الكل» لكل الشاشات» في «تعديل»، وكله بيتحفظ مع «حفظ» واحد
  (عملية واحدة في القاعدة + سجل التعديلات).
* المدير وصاحب «الصلاحية الكاملة»: كل حاجة مسموحة ومفيش تعديل.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from app.security.session import SESSION
from app.services import permission_registry as reg
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.override_matrix import ALLOW, OverrideMatrixEditor


class UserPermissionsScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "user_permissions"
    TITLE = "صلاحيات المستخدم"
    ICON = "🔑"
    FORM_TITLE = "المستخدم"
    LIST_TITLE = "المستخدمين"
    SEARCH_PLACEHOLDER = "🔍 بحث بالاسم / البريد / الهاتف…"
    COLUMNS = [("username", "المستخدم"), ("full_name", "الاسم"), ("role_name_ar", "المجموعة"),
               ("overrides_count", "التخصيصات")]
    VERSION_FIELD = "permissions_version"      # إصدار صلاحيات المستخدم (مش بياناته)

    def __init__(self, permissions: PermissionService | None = None, parent: QWidget | None = None) -> None:
        self.permissions = permissions or PermissionService()
        self.editor = OverrideMatrixEditor(self._ask_password)
        super().__init__(parent)
        self.setMinimumSize(1150, 640)
        self.reload_matrix()

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = QFormLayout(box)
        form.setContentsMargins(12, 18, 12, 10)
        form.setSpacing(8)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.username = self._readonly()
        self.full_name = self._readonly()
        self.role_name = self._readonly()
        self.status = self._readonly()
        self.overrides = self._readonly()
        form.addRow("المستخدم", self.username)
        form.addRow("الاسم", self.full_name)
        form.addRow("المجموعة", self.role_name)
        form.addRow("الحالة", self.status)
        form.addRow("التخصيصات", self.overrides)

    @staticmethod
    def _readonly() -> QLineEdit:
        field = QLineEdit()
        field.setReadOnly(True)
        return field

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.reset_button = self._button("تصفير التخصيصات", "toggle", self.reset_overrides)
        self.reset_button.setToolTip("كل التخصيصات ترجع «وراثة» = زي مجموعته بالظبط (بيتحفظ مع «حفظ»)")
        self.password_all_button = self._button("🔑 كلمة سر «حذف الكل» لكل الشاشات", "preview_permissions",
                                                self.set_delete_all_password)
        self.password_all_button.setToolTip("كلمة سر واحدة لـ «حذف الكل» في كل الشاشات اللي المستخدم واخدها فيها")
        layout.addWidget(self.reset_button)
        layout.addWidget(self.password_all_button)

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(list_box, 3)
        right.addWidget(form_box, 2)
        content.addLayout(right, 4)                              # RTL: المستخدمين يمين
        matrix_box = QGroupBox("صلاحيات المستخدم")
        layout = QVBoxLayout(matrix_box)
        layout.setContentsMargins(10, 18, 10, 8)
        layout.addWidget(self.editor, 1)
        align_form_right(matrix_box)
        content.addWidget(matrix_box, 8)

    def _is_full(self) -> bool:
        record = self.current_record or {}
        return bool(record.get("is_admin") or record.get("full_access"))

    def set_mode(self, mode: str) -> None:
        self.editor.set_editable(mode == "edit")                 # قبل الـ Base عشان أزرار الشاشة
        super().set_mode(mode)

    def update_extra_buttons(self) -> None:
        editing = self.mode == "edit" and self.editor.editable
        self.reset_button.setEnabled(editing)
        self.password_all_button.setEnabled(editing)
        if self.mode == "view" and self._is_full():
            self.edit_button.setEnabled(False)                   # كل حاجة مسموحة ← مفيش تخصيص
            self.edit_button.setToolTip("المستخدم ده مدير / صلاحية كاملة")
        else:
            self.edit_button.setToolTip("")

    def edit_record(self) -> None:
        if self._is_full():
            return
        super().edit_record()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "overrides_count":
            if row.get("is_admin") or row.get("full_access"):
                return "صلاحية كاملة"
            return "—" if not value else f"{value} ({row.get('overrides_allow', 0)} سماح · {row.get('overrides_deny', 0)} منع)"
        return default_format(key, value, row)

    # ---- البيانات ----------------------------------------------------------
    def reload_matrix(self) -> None:
        try:
            self.editor.set_entries(self.permissions.grouped_matrix())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الصلاحيات", exc)
            return
        if self.current_record is not None:
            self._load_matrix(self.current_record)
        self.set_mode(self.mode)

    def _load_matrix(self, record: dict) -> None:
        full = bool(record.get("is_admin") or record.get("full_access"))
        self.editor.load(self.permissions.user_matrix(int(record["id"])), full)

    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.permissions.list_users_for_permissions(keyword)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.permissions.get_user(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.username.setText(record.get("username") or "")
        self.full_name.setText(record.get("full_name") or "")
        self.role_name.setText(record.get("role_name_ar") or "— من غير مجموعة —")
        flags = ["نشط" if record.get("is_active") else "موقوف"]
        if record.get("is_admin"):
            flags.append("مدير")
        if record.get("full_access"):
            flags.append("صلاحية كاملة")
        self.status.setText(" · ".join(flags))
        self._load_matrix(record)
        allow, deny = self.editor.counts()
        self.overrides.setText("كل حاجة مسموحة" if self.editor.is_full
                               else f"{allow + deny} ({allow} سماح · {deny} منع)")

    def clear_form(self) -> None:
        for field in (self.username, self.full_name, self.role_name, self.status, self.overrides):
            field.clear()
        self.editor.load({}, False)

    def collect(self) -> dict:
        for action, pids in self.editor.missing_passwords().items():
            name = reg.ACTION_NAMES_AR[action]
            password = self._ask_password(f"«{name}» مسموح في {len(pids)} شاشة من غير كلمة سر — "
                                          "كلمة السر دي هتتحط لهم كلهم.", name)
            if password is None:
                raise BusinessError(f"مااتحفظش — «{name}» لازم يكون له كلمة سر.")
            for pid in pids:
                self.editor.states[pid] = ALLOW
                self.editor.pending_passwords[pid] = password
        return {"states": self.editor.changed_states(), "passwords": dict(self.editor.pending_passwords)}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        self.permissions.save_user_permissions(int(record_id), payload["states"], payload["passwords"],
                                              expected_version=self.current_version)
        if SESSION.user_id == int(record_id):                   # عدّلت نفسك ← صلاحياتك تتحدّث على طول
            fresh = self.permissions.get_user(int(record_id))
            if fresh is not None:
                SESSION.reload_permissions(self.permissions.effective_codes(fresh))
        return record_id

    # ---- الأزرار -----------------------------------------------------------
    def _ask_password(self, where: str, action: str) -> str | None:
        from app.ui.dialogs.action_password_dialog import ask_new_action_password

        return ask_new_action_password(self, where, action)

    def reset_overrides(self) -> None:
        if self.mode != "edit" or not self.editor.editable:
            return
        if not messages.ask_yes_no(self, "تصفير التخصيصات",
                                   "كل التخصيصات هترجع «وراثة» (زي مجموعته بالظبط) — هتتحفظ مع «حفظ». متأكد؟"):
            return
        self.editor.reset_all()

    def set_delete_all_password(self) -> None:
        """كلمة سر واحدة لـ «حذف الكل» في كل الشاشات اللي المستخدم واخده فيها (بتتحفظ مع «حفظ»)."""
        if self.mode != "edit" or not self.editor.editable:
            return
        pids = [pid for pid in self.editor._cells
                if self.editor.action_of(pid) == "delete_all" and self.editor.effective(pid)]
        if not pids:
            messages.info(self, "حذف الكل", "المستخدم ده مالوش «حذف الكل» في أي شاشة — اديهوله الأول "
                                            "(من المجموعة أو «سماح» هنا).")
            return
        password = self._ask_password(f"المستخدم {self.username.text()} — كلمة السر دي هتتحط لـ «حذف الكل» "
                                      f"في {len(pids)} شاشة.", "حذف الكل")
        if password is None:
            return
        for pid in pids:
            self.editor.states[pid] = ALLOW
            self.editor.pending_passwords[pid] = password
        self.editor.refresh()
