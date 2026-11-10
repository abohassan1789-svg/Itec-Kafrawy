"""شاشة الدخول — النموذج 2 «مقسومة نصين» (اختيار المستخدم 2026-11-07).

يمين: لوحة خضرا غامقة فيها البراند. شمال: الفورم الأبيض (اسم المستخدم، كلمة المرور، رسالة الخطأ،
دخول / خروج). نفس ألوان وخطوط Hr GAD.

* Enter من اسم المستخدم يروح لكلمة السر، وEnter من كلمة السر يدخل.
* الشاشة دي واجهة بس: التحقق والتسجيل والجلسة كلهم في ``AuthService.login``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, APP_VERSION
from app.config.db_errors import describe_db_error
from app.services.auth_service import AuthService
from app.ui.common.theme import BORDER_INPUT, GREEN, GREEN_DARK, RED, TEXT, TEXT_MUTED

LOGIN_SIZE = (760, 440)
BRAND_PANEL_WIDTH = 330


def brand_panel(subtitle: str = "برنامج الحسابات") -> QFrame:
    """اللوحة الخضرا اللي على اليمين (مشتركة مع شاشة التحميل)."""
    panel = QFrame()
    panel.setObjectName("brandPanel")
    panel.setFixedWidth(BRAND_PANEL_WIDTH)
    panel.setStyleSheet(f"QFrame#brandPanel {{ background:{GREEN_DARK}; }}")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(24, 30, 24, 24)
    layout.setSpacing(8)

    title = QLabel(APP_NAME)
    title.setAlignment(Qt.AlignCenter)
    title.setStyleSheet("color:#FFFFFF; font-size:34px; font-weight:900; background:transparent;")
    sub = QLabel(subtitle)
    sub.setAlignment(Qt.AlignCenter)
    sub.setStyleSheet("color:#BBF7D0; font-size:18px; font-weight:700; background:transparent;")
    company = QLabel("ITEC")
    company.setAlignment(Qt.AlignCenter)
    company.setStyleSheet("color:rgba(255,255,255,0.75); font-size:15px; font-weight:800; "
                          "background:transparent; letter-spacing:3px;")
    version = QLabel(f"الإصدار {APP_VERSION}")
    version.setAlignment(Qt.AlignCenter)
    version.setStyleSheet("color:rgba(255,255,255,0.55); font-size:12px; background:transparent;")

    layout.addStretch(1)
    layout.addWidget(title)
    layout.addWidget(sub)
    layout.addSpacing(14)
    layout.addWidget(company)
    layout.addStretch(1)
    layout.addWidget(version)
    return panel


class LoginWindow(QDialog):
    def __init__(self, auth_service: AuthService | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.auth_service = auth_service or AuthService()
        self.user: dict[str, Any] | None = None
        self.setWindowTitle(f"تسجيل الدخول - {APP_NAME}")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFixedSize(*LOGIN_SIZE)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._build_ui()

    # ---- الواجهة -----------------------------------------------------------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(brand_panel())          # RTL: أول عنصر = يمين

        form = QFrame()
        form.setObjectName("loginForm")
        form.setStyleSheet("QFrame#loginForm { background:#FFFFFF; }")
        root.addWidget(form, 1)

        layout = QVBoxLayout(form)
        layout.setContentsMargins(42, 36, 42, 30)
        layout.setSpacing(8)

        heading = QLabel("تسجيل الدخول")
        heading.setStyleSheet(f"color:{GREEN}; font-size:24px; font-weight:900; background:transparent;")
        hint = QLabel("اكتب اسم المستخدم وكلمة المرور")
        hint.setStyleSheet(f"color:{TEXT_MUTED}; font-size:13px; font-weight:600; background:transparent;")
        layout.addWidget(heading)
        layout.addWidget(hint)
        layout.addSpacing(14)

        layout.addWidget(self._label("اسم المستخدم"))
        self.username = QLineEdit()
        self.username.setObjectName("username")
        self.username.setPlaceholderText("اسم المستخدم")
        self.username.setMaxLength(60)
        self._style_input(self.username)
        layout.addWidget(self.username)
        layout.addSpacing(4)

        layout.addWidget(self._label("كلمة المرور"))
        self.password = QLineEdit()
        self.password.setObjectName("password")
        self.password.setPlaceholderText("كلمة المرور")
        self.password.setEchoMode(QLineEdit.Password)
        self._style_input(self.password)
        layout.addWidget(self.password)

        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setMinimumHeight(24)
        self.error_label.setStyleSheet(f"color:{RED}; font-weight:800; background:transparent;")
        layout.addWidget(self.error_label)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.login_button = QPushButton("دخول")
        self.login_button.setFixedHeight(44)
        self.login_button.setCursor(Qt.PointingHandCursor)
        self.login_button.setDefault(True)
        self.login_button.setStyleSheet(
            f"QPushButton {{ background:{GREEN}; color:#FFFFFF; border:none; border-radius:8px; "
            "font-size:16px; font-weight:900; }"
            f"QPushButton:hover {{ background:{GREEN_DARK}; }}"
            "QPushButton:disabled { background:#CBD5E1; }"
        )
        self.exit_button = QPushButton("خروج")
        self.exit_button.setFixedHeight(44)
        self.exit_button.setCursor(Qt.PointingHandCursor)
        self.exit_button.setAutoDefault(False)
        self.exit_button.setStyleSheet(
            f"QPushButton {{ background:#FFFFFF; color:#374151; border:1px solid {BORDER_INPUT}; "
            "border-radius:8px; font-size:15px; font-weight:800; }"
            "QPushButton:hover { background:#F3F4F6; }"
        )
        buttons.addWidget(self.login_button, 1)
        buttons.addWidget(self.exit_button, 1)
        layout.addLayout(buttons)

        self.username.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self.attempt_login)
        self.login_button.clicked.connect(self.attempt_login)
        self.exit_button.clicked.connect(self.reject)
        self.username.textEdited.connect(self._clear_error)
        self.password.textEdited.connect(self._clear_error)
        self.username.setFocus()

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"color:{TEXT}; font-size:15px; font-weight:800; background:transparent;")
        return label

    @staticmethod
    def _style_input(field: QLineEdit) -> None:
        field.setFixedHeight(42)
        field.setStyleSheet(
            f"QLineEdit {{ background:#FFFFFF; border:1px solid {GREEN}; border-radius:8px; "
            f"padding:6px 12px; font-size:16px; font-weight:700; color:{TEXT}; }}"
            f"QLineEdit:focus {{ border:2px solid {GREEN_DARK}; }}"
        )

    def _clear_error(self) -> None:
        self.error_label.setText("")

    # ---- الدخول ------------------------------------------------------------
    def attempt_login(self) -> None:
        if not self.username.text().strip():
            self.error_label.setText("اكتب اسم المستخدم")
            self.username.setFocus()
            return
        if not self.password.text():
            self.error_label.setText("اكتب كلمة المرور")
            self.password.setFocus()
            return
        self.login_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.auth_service.login(self.username.text(), self.password.text())
        except Exception as exc:  # noqa: BLE001 — مشكلة اتصال: رسالة عربي بدل ما البرنامج يقع
            settings = self.auth_service.db.settings
            self.error_label.setText(describe_db_error(exc, password=settings.db_password))
            return
        finally:
            QApplication.restoreOverrideCursor()
            self.login_button.setEnabled(True)

        if not result.ok:
            self.error_label.setText(result.message)
            self.password.clear()
            (self.username if "المستخدم" in result.message else self.password).setFocus()
            return
        self.user = result.user
        self.accept()
