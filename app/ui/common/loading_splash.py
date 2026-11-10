"""شاشة التحميل (Splash) — بنفس شكل شاشة الدخول المختار (النموذج 2 «مقسومة نصين»):
يمين لوحة خضرا غامقة فيها البراند، وشمال أبيض فيه سطر الحالة وشريط التقدّم.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from app.ui.common.theme import BORDER_CARD, GREEN, TEXT_MUTED

SPLASH_SIZE = (640, 300)


class LoadingSplash(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from app.ui.screens.login_screen import brand_panel

        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFixedSize(*SPLASH_SIZE)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        frame = QFrame()
        frame.setObjectName("splashFrame")
        frame.setStyleSheet(f"QFrame#splashFrame {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; }}")
        outer.addWidget(frame)

        root = QHBoxLayout(frame)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        panel = brand_panel()
        panel.setFixedWidth(270)
        root.addWidget(panel)

        body = QVBoxLayout()
        body.setContentsMargins(30, 30, 30, 30)
        body.setSpacing(12)
        heading = QLabel("جاري تجهيز البرنامج")
        heading.setStyleSheet(f"color:{GREEN}; font-size:20px; font-weight:900;")
        self.status = QLabel("جاري التحميل…")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{TEXT_MUTED}; font-size:14px; font-weight:600;")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)
        self.bar.setStyleSheet(
            "QProgressBar { background:#E2E8F0; border:none; border-radius:6px; }"
            f"QProgressBar::chunk {{ background:{GREEN}; border-radius:6px; }}"
        )
        body.addStretch(1)
        body.addWidget(heading)
        body.addWidget(self.status)
        body.addWidget(self.bar)
        body.addStretch(1)
        root.addLayout(body, 1)
        self._center()

    def _center(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            geo = self.frameGeometry()
            geo.moveCenter(screen.availableGeometry().center())
            self.move(geo.topLeft())

    def set_step(self, percent: int, text: str) -> None:
        self.bar.setValue(max(0, min(100, int(percent))))
        self.status.setText(text)
        QApplication.processEvents()
