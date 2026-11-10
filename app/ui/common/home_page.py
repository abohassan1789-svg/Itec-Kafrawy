"""الصفحة الرئيسية — زي Hr GAD: لوجو الشركة (ITEC) في النص، و«A-H CODE / للحلول البرمجية» +
رقم التواصل في كبسولة خضرا صغير تحت على الشمال. الصفحة مابتقراش أي بيانات.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from app.config.paths import resource_root
from app.ui.common.theme import BG_CONTENT, GREEN, TEXT_MUTED

COMPANY_LOGO = resource_root() / "app" / "assets" / "home" / "company_logo.png"
BRAND = "A-H CODE"
TAGLINE = "للحلول البرمجية"
PHONE = "01123746868"


class LogoView(QWidget):
    """اللوجو بيصغر/يكبر مع الشباك ومحافظ على شكله، وأقصى حاجة مقاسه الأصلي × 1.25."""

    MAX_SCALE = 1.25

    def __init__(self, pixmap: QPixmap, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap = pixmap
        self._scaled: QPixmap | None = None
        self.setObjectName("homeLogo")
        self.setMinimumSize(120, 100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def target_rect(self) -> QRect:
        size = self._pixmap.size().scaled(self.size(), Qt.KeepAspectRatio)
        max_w = int(self._pixmap.width() * self.MAX_SCALE)
        if size.width() > max_w:
            size = self._pixmap.size() * self.MAX_SCALE
        x = (self.width() - size.width()) // 2
        y = (self.height() - size.height()) // 2
        return QRect(x, y, size.width(), size.height())

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt API
        rect = self.target_rect()
        if self._scaled is None or self._scaled.size() != rect.size():
            self._scaled = self._pixmap.scaled(rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        QPainter(self).drawPixmap(rect.topLeft(), self._scaled)


class HomePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("homePage")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#homePage {{ background:{BG_CONTENT}; }}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 18)
        outer.setSpacing(8)

        pixmap = QPixmap(str(COMPANY_LOGO))
        self.logo_view: LogoView | None = None
        if not pixmap.isNull():
            self.logo_view = LogoView(pixmap)
            outer.addWidget(self.logo_view, 1)
        else:  # الصورة مش موجودة ← الصفحة تفضل شغالة من غير لوجو
            outer.addStretch(1)

        # «A-H CODE» صغير تحت على الشمال (الشمال الحقيقي حتى تحت RTL ← AlignAbsolute).
        self.brand_block = QWidget()
        self.brand_block.setObjectName("brandBlock")
        block = QVBoxLayout(self.brand_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(4)
        title = QLabel(BRAND)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color:#0F172A; font-size:18px; font-weight:900; letter-spacing:2px; background:transparent;")
        tagline = QLabel(TAGLINE)
        tagline.setAlignment(Qt.AlignCenter)
        tagline.setStyleSheet(f"color:{TEXT_MUTED}; font-size:16px; font-weight:700; background:transparent;")
        block.addWidget(title)
        block.addWidget(tagline)
        block.addWidget(self._phone_pill(), 0, Qt.AlignHCenter)
        outer.addWidget(self.brand_block, 0, Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignBottom)

    @staticmethod
    def _phone_pill() -> QWidget:
        pill = QFrame()
        pill.setObjectName("phonePill")
        pill.setStyleSheet(f"QFrame#phonePill {{ background:{GREEN}; border-radius:14px; }}")
        pill.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        pill.setLayoutDirection(Qt.LeftToRight)       # الأيقونة ثم الرقم من الشمال لليمين
        row = QHBoxLayout(pill)
        row.setContentsMargins(12, 5, 12, 5)
        row.setSpacing(6)
        row.setDirection(QBoxLayout.LeftToRight)
        for text in ("📞", PHONE):
            label = QLabel(text)
            label.setStyleSheet("color:#FFFFFF; font-size:16px; font-weight:800; background:transparent;")
            row.addWidget(label)
        return pill
