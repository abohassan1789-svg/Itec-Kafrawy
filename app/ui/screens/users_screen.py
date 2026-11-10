"""شاشة «المستخدمين» على الـ Base Screen (النموذج 1) — زي Hr GAD.

* الفورم: اسم المستخدم (في «جديد» بس) · كلمة المرور (فاضية = من غير تغيير) · الاسم الكامل · المهنة ·
  البريد · الهاتف · المجموعة · اللغة · نشط/مدير/صلاحية كاملة · ملاحظات · آخر دخول + **مربع الصورة**
  (رفع/إزالة — بتتحفظ مع «حفظ»).
* القائمة: بحث بالاسم/البريد/الهاتف + «إظهار المستخدمين غير المفعّلين».
* أزرار زيادة: إيقاف/تفعيل · سجل الدخول · معاينة الصلاحيات.
* لو المستخدم عدّل نفسه ← هيدر الواجهة الرئيسية بيتحدّث على طول.
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.security.session import SESSION
from app.services.permission_service import PermissionService
from app.services.user_service import MAX_PHOTO_BYTES, UserService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen
from app.ui.common.data_table import DataTable, default_format
from app.ui.common.theme import GREEN, GREEN_DARK, RED, TEXT_MUTED, button_style, toolbar_button_style

PHOTO_W, PHOTO_H = 200, 220
NO_PHOTO_TEXT = "لا توجد صورة"
PHOTO_FILTER = "الصور (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"
_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".bmp": "image/bmp", ".webp": "image/webp"}


class UsersScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "users"
    TITLE = "المستخدمين"
    ICON = "👥"
    FORM_TITLE = "بيانات المستخدم"
    NEW_TEXT = "مستخدم جديد"
    SEARCH_PLACEHOLDER = "🔍 بحث بالاسم / البريد / الهاتف…"
    COLUMNS = [("username", "المستخدم"), ("full_name", "الاسم"), ("role_name_ar", "المجموعة"),
               ("is_active", "الحالة")]

    def __init__(self, users: UserService | None = None, permissions: PermissionService | None = None,
                 parent: QWidget | None = None) -> None:
        self.users = users or UserService()
        self.permissions = permissions or PermissionService(self.users.db)
        self._photo_bytes: bytes | None = None
        self._photo_mime: str | None = None
        super().__init__(parent)

    # ---- الفورم ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        outer = QHBoxLayout(box)
        outer.setContentsMargins(14, 20, 14, 12)
        outer.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(8)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.username = QLineEdit()
        self.username.setMaxLength(60)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.full_name = QLineEdit()
        self.job_title = QLineEdit()
        self.job_title.setPlaceholderText("مثال: محاسب")
        self.email = QLineEdit()
        self.phone = QLineEdit()
        self.role_combo = QComboBox()
        self.language_combo = QComboBox()
        self.language_combo.addItem("العربية", "ar")
        self.language_combo.addItem("English", "en")
        self.is_active = QCheckBox("نشط")
        self.is_admin = QCheckBox("مدير")
        self.full_access = QCheckBox("صلاحية كاملة")
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(70)
        self.last_login = QLabel("-")
        self.last_login.setFixedHeight(32)
        self.last_login.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.last_login.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")

        form.addRow("اسم المستخدم", self.username)
        form.addRow("كلمة المرور", self.password)
        form.addRow("الاسم الكامل", self.full_name)
        form.addRow("المهنة", self.job_title)
        form.addRow("البريد", self.email)
        form.addRow("الهاتف", self.phone)
        form.addRow("المجموعة", self.role_combo)
        form.addRow("اللغة", self.language_combo)
        flags = QHBoxLayout()
        flags.setSpacing(18)
        for check in (self.is_active, self.is_admin, self.full_access):
            flags.addWidget(check)
        flags.addStretch(1)
        form.addRow("الحالة", flags)
        form.addRow("ملاحظات", self.notes)
        form.addRow("آخر دخول", self.last_login)
        outer.addLayout(form, 1)                       # RTL: الحقول يمين
        outer.addLayout(self._build_photo_column())    # والصورة شمالها
        self._load_roles()

    def _build_photo_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(8)
        column.setAlignment(Qt.AlignTop)
        self.photo_preview = QLabel(NO_PHOTO_TEXT)
        self.photo_preview.setFixedSize(PHOTO_W, PHOTO_H)
        self.photo_preview.setAlignment(Qt.AlignCenter)
        self.photo_preview.setStyleSheet(
            f"QLabel {{ background:#F8FAFC; border:1px dashed {GREEN}; border-radius:10px; "
            "color:#94A3B8; font-weight:800; }")
        column.addWidget(self.photo_preview)
        self.upload_photo_button = QPushButton("رفع الصورة")
        self.upload_photo_button.setStyleSheet(button_style(GREEN, GREEN_DARK))
        self.upload_photo_button.setToolTip("بتتحفظ مع «حفظ»")
        self.upload_photo_button.clicked.connect(self._upload_photo)
        self.remove_photo_button = QPushButton("إزالة الصورة")
        self.remove_photo_button.setStyleSheet(
            f"QPushButton {{ background:#FFFFFF; color:{RED}; border:1px solid #FCA5A5; border-radius:6px; "
            "font-weight:800; padding:7px 9px; } QPushButton:hover { background:#FEF2F2; }"
            "QPushButton:disabled { color:#CBD5E1; border-color:#E2E8F0; }")
        self.remove_photo_button.setToolTip("بتتشال مع «حفظ»")
        self.remove_photo_button.clicked.connect(self._remove_photo)
        column.addWidget(self.upload_photo_button)
        column.addWidget(self.remove_photo_button)
        return column

    def _load_roles(self) -> None:
        current = self.role_combo.currentData()
        self.role_combo.clear()
        self.role_combo.addItem("— بدون مجموعة —", None)
        try:
            roles = self.users.list_roles_for_combo()
        except Exception:  # noqa: BLE001 — الشاشة تفتح حتى لو القائمة فشلت
            roles = []
        for role in roles:
            self.role_combo.addItem(role["role_name_ar"], role["id"])
        index = self.role_combo.findData(current)
        self.role_combo.setCurrentIndex(max(0, index))

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.toggle_button = self._button("إيقاف / تفعيل", "toggle", self.toggle_active)
        self.log_button = self._button("سجل الدخول", "login_log", self.show_login_log)
        self.preview_button = self._button("معاينة الصلاحيات", "preview_permissions", self.preview_permissions)
        for button in (self.toggle_button, self.log_button, self.preview_button):
            layout.addWidget(button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        self.show_inactive = QCheckBox("إظهار المستخدمين غير المفعّلين")
        self.show_inactive.toggled.connect(self._on_show_inactive)
        layout.addWidget(self.show_inactive)

    def editable_widgets(self) -> list[QWidget]:
        return [self.password, self.full_name, self.job_title, self.email, self.phone, self.role_combo,
                self.language_combo, self.is_active, self.is_admin, self.full_access, self.notes,
                self.upload_photo_button, self.remove_photo_button]

    def new_only_widgets(self) -> list[QWidget]:
        return [self.username]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        self.password.setPlaceholderText("كلمة المرور مطلوبة" if mode == "new" else "اتركها فارغة لعدم التغيير")
        # «مدير» و«صلاحية كاملة» للمدير بس (الـ Service بيرفض برضه).
        if mode in ("new", "edit") and not SESSION.is_admin:
            self.is_admin.setEnabled(False)
            self.full_access.setEnabled(False)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit"))
        self.log_button.setEnabled(idle)
        self.preview_button.setEnabled(idle)

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "is_active":
            return "نشط" if value else "موقوف"
        if key == "full_name" and not value:
            return ""
        return default_format(key, value, row)

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.users.list_users(keyword, include_inactive=self.show_inactive.isChecked())

    def lookup_rows(self) -> list[dict]:
        return self.users.list_users("", include_inactive=True)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.users.get_user(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._load_roles()
        self.username.setText(record.get("username") or "")
        self.password.clear()
        self.full_name.setText(record.get("full_name") or "")
        self.job_title.setText(record.get("job_title") or "")
        self.email.setText(record.get("email") or "")
        self.phone.setText(record.get("phone") or "")
        self.role_combo.setCurrentIndex(max(0, self.role_combo.findData(record.get("role_id"))))
        self.language_combo.setCurrentIndex(max(0, self.language_combo.findData(record.get("language") or "ar")))
        self.is_active.setChecked(bool(record.get("is_active")))
        self.is_admin.setChecked(bool(record.get("is_admin")))
        self.full_access.setChecked(bool(record.get("full_access")))
        self.notes.setPlainText(record.get("notes") or "")
        self.last_login.setText(default_format("last_login_at", record.get("last_login_at"), record) or "-")
        photo, mime = (None, None)
        if record.get("has_photo"):
            try:
                photo, mime = self.users.get_user_photo(int(record["id"]))
            except Exception:  # noqa: BLE001 — الصورة مش لازم توقف عرض البيانات
                photo, mime = None, None
        self._set_photo(photo, mime)

    def clear_form(self) -> None:
        self._load_roles()
        for field in (self.username, self.password, self.full_name, self.job_title, self.email, self.phone):
            field.clear()
        self.notes.clear()
        self.role_combo.setCurrentIndex(0)
        self.language_combo.setCurrentIndex(0)
        self.is_active.setChecked(True)
        self.is_admin.setChecked(False)
        self.full_access.setChecked(False)
        self.last_login.setText("-")
        self._set_photo(None, None)

    def collect(self) -> dict:
        return {
            "username": self.username.text().strip(),
            "password": self.password.text(),
            "full_name": self.full_name.text(),
            "job_title": self.job_title.text(),
            "email": self.email.text(),
            "phone": self.phone.text(),
            "role_id": self.role_combo.currentData(),
            "language": self.language_combo.currentData(),
            "is_active": self.is_active.isChecked(),
            "is_admin": self.is_admin.isChecked(),
            "full_access": self.full_access.isChecked(),
            "notes": self.notes.toPlainText(),
            "photo": self._photo_bytes,
            "photo_mime": self._photo_mime,
        }

    def save_record(self, payload: dict, record_id: Any) -> Any:
        password = payload.pop("password")
        if record_id is None:
            new_id = self.users.create_user(payload, password)
        else:
            self.users.update_user(int(record_id), payload, password or None, expected_version=self.current_version)
            new_id = int(record_id)
        if SESSION.user_id == new_id:
            refresh_main_header()
        return new_id

    # ---- الصورة ------------------------------------------------------------
    def _set_photo(self, data: bytes | None, mime: str | None) -> None:
        self._photo_bytes = data or None
        self._photo_mime = mime if data else None
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.photo_preview.setPixmap(pixmap.scaled(PHOTO_W - 4, PHOTO_H - 4, Qt.KeepAspectRatio,
                                                       Qt.SmoothTransformation))
        else:
            self.photo_preview.setPixmap(QPixmap())
            self.photo_preview.setText(NO_PHOTO_TEXT)

    def _upload_photo(self) -> None:
        if self.mode not in ("new", "edit"):
            return
        path, _ = QFileDialog.getOpenFileName(self, "اختيار صورة المستخدم", "", PHOTO_FILTER)
        if path:
            self.set_photo_file(path)

    def set_photo_file(self, path: str) -> bool:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            messages.warn(self, "صورة المستخدم", f"تعذّر قراءة الصورة:\n{exc}")
            return False
        if len(data) > MAX_PHOTO_BYTES:
            messages.warn(self, "صورة المستخدم", "الصورة أكبر من 2 ميجا — اختار صورة أصغر.")
            return False
        if not QPixmap().loadFromData(data):
            messages.warn(self, "صورة المستخدم", "الملف ده مش صورة — اختار صورة PNG أو JPG.")
            return False
        self._set_photo(data, _MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "image/png"))
        return True

    def _remove_photo(self) -> None:
        if self.mode in ("new", "edit"):
            self._set_photo(None, None)

    # ---- الأزرار الزيادة ---------------------------------------------------
    def _on_show_inactive(self) -> None:
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()          # المعروض موقوف واتخفى

    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} المستخدم «{self.current_record['username']}»؟"):
            return
        try:
            self.users.set_user_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id) or self.show_inactive.isChecked():
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل المستخدم." if new_state else "تم إيقاف المستخدم.")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم إيقاف المستخدم واختفى من الجدول.\n"
                                      "عشان تفعّله تاني: علّم «إظهار المستخدمين غير المفعّلين».")

    def show_login_log(self) -> None:
        if self.current_id is None:
            return
        try:
            rows = self.users.list_user_logins(int(self.current_id))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "سجل الدخول", exc)
            return
        for row in rows:
            row["status_ar"] = "ناجح" if row["status"] == "success" else "فاشل"
        title = f"سجل الدخول — {self.current_record.get('username', '')}"
        TableDialog(title, [("login_time", "الدخول"), ("logout_time", "الخروج"), ("status_ar", "الحالة"),
                            ("device_name", "الجهاز"), ("notes", "السبب")], rows, self).exec()

    def preview_permissions(self) -> None:
        if self.current_id is None:
            return
        try:
            rows = self.permissions.preview_user(int(self.current_id))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "معاينة الصلاحيات", exc)
            return
        title = f"الصلاحيات الفعلية — {self.current_record.get('username', '')}"
        TableDialog(title, [("section", "القسم"), ("target", "الشاشة"), ("action", "الإجراء"),
                            ("result", "النتيجة"), ("source", "المصدر")], rows, self).exec()


class TableDialog(QDialog):
    """نافذة جدول للعرض بس (سجل الدخول، معاينة الصلاحيات)."""

    def __init__(self, title: str, columns, rows: list[dict], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(820, 520)
        layout = QVBoxLayout(self)
        self.table = DataTable(columns, id_key="id")
        self.table.set_rows(rows)
        layout.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        count = QLabel(f"عدد السجلات: {len(rows)}")
        count.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        bottom.addWidget(count, 1)
        close = QPushButton("إغلاق")
        close.setStyleSheet(toolbar_button_style("exit"))
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        layout.addLayout(bottom)


def refresh_main_header() -> None:
    """المستخدم عدّل نفسه ← هيدر الواجهة الرئيسية يتحدّث."""
    app = QApplication.instance()
    if app is None:
        return
    for window in app.topLevelWidgets():
        refresh = getattr(window, "refresh_user_header", None)
        if callable(refresh):
            refresh()
