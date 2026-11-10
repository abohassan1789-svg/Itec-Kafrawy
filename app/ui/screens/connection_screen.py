"""شاشة إعداد الاتصال بالخادم (أول تشغيل، أو لو القاعدة مش متاحة) — نفس فكرة Hr GAD.

* :class:`ConnectionSettingsWidget` — الحقول + «اختبار الاتصال» + «حفظ».
* :class:`FirstRunConnectionDialog` — نافذة بتمنع البرنامج يكمّل لحد ما يتحفظ اتصال شغال.
* :func:`ensure_connection` — بيرجّع True لو فيه اتصال شغال (بعد ما يسأل لو لزم).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.config import connection_config as cc
from app.services.connection_service import test_connection
from app.ui.common.theme import BORDER_CARD, GREEN_DARK, RED_DARK, WARNING_BG, WARNING_TEXT, toolbar_button_style


class ConnectionSettingsWidget(QWidget):
    def __init__(self, profile: cc.ConnectionProfile | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self._profile = profile or cc.load() or cc.ConnectionProfile()
        self._build_ui()
        self._load_into_fields()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("إعدادات الاتصال بالخادم")
        title.setStyleSheet(f"font-size:20px; font-weight:900; color:{GREEN_DARK};")
        root.addWidget(title)

        frame = QFrame()
        frame.setObjectName("connectionCard")
        frame.setStyleSheet(
            f"QFrame#connectionCard {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; border-radius:8px; }}"
        )
        form = QFormLayout(frame)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)

        self.txt_address = QLineEdit()
        self.txt_address.setPlaceholderText("localhost  أو  192.168.1.10")
        self.spn_port = QSpinBox()
        self.spn_port.setRange(1, 65535)
        self.spn_port.setButtonSymbols(QSpinBox.NoButtons)
        self.txt_db = QLineEdit()
        self.txt_user = QLineEdit()
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        self.cmb_ssl = QComboBox()
        self.cmb_ssl.addItems(list(cc.SSL_MODES))

        form.addRow("عنوان الخادم:", self.txt_address)
        form.addRow("المنفذ (Port):", self.spn_port)
        form.addRow("اسم قاعدة البيانات:", self.txt_db)
        form.addRow("المستخدم:", self.txt_user)
        form.addRow("كلمة المرور:", self.txt_password)
        form.addRow("وضع SSL:", self.cmb_ssl)
        root.addWidget(frame)

        actions = QHBoxLayout()
        self.btn_test = QPushButton("اختبار الاتصال")
        self.btn_test.setStyleSheet(toolbar_button_style("save"))
        self.btn_test.clicked.connect(self.run_test)
        self.btn_save = QPushButton("حفظ")
        self.btn_save.setStyleSheet(toolbar_button_style("primary"))
        self.btn_save.clicked.connect(self._on_save_clicked)
        actions.addWidget(self.btn_test)
        actions.addStretch(1)
        actions.addWidget(self.btn_save)
        root.addLayout(actions)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        root.addStretch(1)

    def _load_into_fields(self) -> None:
        p = self._profile
        self.txt_address.setText(p.server_address)
        self.spn_port.setValue(int(p.server_port or cc.DEFAULT_PORT))
        self.txt_db.setText(p.db_name)
        self.txt_user.setText(p.db_user)
        self.txt_password.setText(p.db_password)
        self.cmb_ssl.setCurrentText(p.sslmode)

    def collect(self) -> cc.ConnectionProfile:
        return cc.ConnectionProfile(
            server_address=self.txt_address.text().strip(),
            server_port=self.spn_port.value(),
            db_name=self.txt_db.text().strip(),
            db_user=self.txt_user.text().strip(),
            db_password=self.txt_password.text(),
            sslmode=self.cmb_ssl.currentText(),
        )

    def _set_status(self, ok: bool, message: str) -> None:
        self.status.setStyleSheet(f"color:{GREEN_DARK if ok else RED_DARK}; font-weight:700;")
        self.status.setText(("✓ " if ok else "✗ ") + message)

    def run_test(self) -> bool:
        result = test_connection(self.collect())
        self._set_status(result.ok, result.message)
        return result.ok

    def save(self, require_reachable: bool = False) -> bool:
        profile = self.collect()
        errors = profile.validate()
        if errors:
            self._set_status(False, " | ".join(errors))
            return False
        if require_reachable and not self.run_test():
            return False
        try:
            path = cc.save(profile)
        except OSError as exc:
            self._set_status(False, str(exc))
            return False
        self._profile = profile
        self._set_status(True, f"تم حفظ الإعدادات في: {path}")
        return True

    def _on_save_clicked(self) -> None:
        if self.save():
            QMessageBox.information(self, "تم الحفظ", "تم حفظ إعدادات الاتصال بنجاح.")


class FirstRunConnectionDialog(QDialog):
    def __init__(self, reason: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("إعداد الاتصال بالخادم")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        intro = QLabel(reason or (
            "لم يتم العثور على إعدادات اتصال صالحة. اكتب بيانات الخادم وقاعدة البيانات "
            "واضغط «اختبار الاتصال» ثم «حفظ»."
        ))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"padding:14px 20px; background:{WARNING_BG}; color:{WARNING_TEXT}; font-weight:700;")
        layout.addWidget(intro)

        self.widget = ConnectionSettingsWidget(parent=self)
        self.widget.btn_save.clicked.disconnect()
        self.widget.btn_save.clicked.connect(self._save_and_close)
        layout.addWidget(self.widget)

    def _save_and_close(self) -> None:
        if self.widget.save(require_reachable=True):
            self.accept()


def ensure_connection(parent: QWidget | None = None) -> bool:
    """True لو فيه اتصال شغال، وإلا بيفتح شاشة الإعداد. False = المستخدم قفل من غير حفظ."""
    profile = cc.load()
    if profile is not None:
        result = test_connection(profile)
        if result.ok:
            return True
        reason = "تعذّر الاتصال بالإعدادات المحفوظة:\n" + result.message
    else:
        reason = ""
    return FirstRunConnectionDialog(reason, parent).exec() == QDialog.Accepted
