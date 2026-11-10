"""نوافذ كلمة سر الإجراء (حذف الكل / إظهار الإجماليات) — منقولة من Hr GAD.

* ``ask_new_action_password``: وقت منح الصلاحية (للمجموعة أو للمستخدم) — كلمة سر + تأكيدها.
* ``ask_action_password``: وقت التنفيذ — بتتقارن باللي اتحط في الصلاحيات.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from app.ui.common import messages


class PasswordDialog(QDialog):
    def __init__(self, title: str, message: str, confirm: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(440)
        self._confirm = confirm
        layout = QVBoxLayout(self)
        label = QLabel(message)
        label.setWordWrap(True)
        label.setStyleSheet("font-size:16px; font-weight:800; color:#0F172A;")
        layout.addWidget(label)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter)   # يمين حتى مع RTL
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("كلمة السر:", self.password)
        self.again: QLineEdit | None = None
        if confirm:
            self.again = QLineEdit()
            self.again.setEchoMode(QLineEdit.Password)
            form.addRow("تأكيد كلمة السر:", self.again)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("موافق")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.password.setFocus()

    def _accept(self) -> None:
        if not self.password.text().strip():
            messages.warn(self, "كلمة السر", "اكتب كلمة السر.")
            return
        if self._confirm and self.again is not None and self.again.text() != self.password.text():
            messages.warn(self, "كلمة السر", "كلمة السر والتأكيد مش زي بعض.")
            return
        self.accept()


def ask_new_action_password(parent: QWidget | None, target: str, action: str = "حذف الكل") -> str | None:
    dialog = PasswordDialog(f"كلمة سر «{action}»",
                            f"{target}\nاكتب كلمة السر اللي هتتسأل قبل «{action}».", True, parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.password.text()


def ask_action_password(parent: QWidget | None, action: str = "حذف الكل") -> str | None:
    dialog = PasswordDialog(f"تأكيد «{action}»", f"اكتب كلمة سر «{action}» اللي اتحطت لك في الصلاحيات.",
                            False, parent)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.password.text()
