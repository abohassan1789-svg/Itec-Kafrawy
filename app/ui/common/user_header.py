"""هيدر المستخدم في الواجهة الرئيسية — منقول من Hr GAD.

صورة المستخدم (دايرة بحد أخضر) + الاسم + المهنة | الساعة بالثواني (ص/م) والتاريخ باليوم.
الاسم = «الاسم الكامل»، ولو فاضي ← «اسم المستخدم». المهنة لو فاضية مابتظهرش. من غير صورة ← خيال شخص.
``refresh()`` بعد ما المستخدم يعدّل بياناته هو.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.ui.common.theme import BORDER_CARD, GREEN, GREEN_DARK, TEXT_MUTED

PHOTO_SIZE = 44
TICK_MS = 1000
WEEKDAYS_AR = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")  # weekday(): الاتنين = 0

_STYLE = f"""
QLabel {{ background:transparent; }}
QLabel#uhPhoto {{ border:2px solid {GREEN}; border-radius:{(PHOTO_SIZE + 4) // 2}px; background:#FFFFFF; }}
QLabel#uhName {{ color:{GREEN_DARK}; font-size:16px; font-weight:900; }}
QLabel#uhJob {{ color:{TEXT_MUTED}; font-size:15px; font-weight:800; }}
QLabel#uhTime {{ color:#0F172A; font-size:18px; font-weight:900; }}
QLabel#uhDate {{ color:{TEXT_MUTED}; font-size:15px; font-weight:800; }}
QFrame#uhSep {{ background:{BORDER_CARD}; }}
"""


def display_name(user: dict[str, Any] | None) -> str:
    user = user or {}
    return (user.get("full_name") or "").strip() or (user.get("username") or "").strip()


def time_text(now: dt.datetime) -> str:
    hour = now.hour % 12 or 12
    return f"{hour:02d}:{now.minute:02d}:{now.second:02d} {'ص' if now.hour < 12 else 'م'}"


def date_text(now: dt.datetime) -> str:
    return f"{WEEKDAYS_AR[now.weekday()]} {now:%d-%m-%Y}"


def round_photo(data: bytes | None, size: int = PHOTO_SIZE) -> QPixmap:
    """الصورة مقصوصة دايرة؛ من غير صورة (أو صورة بايظة) ← خيال شخص على أخضر فاتح."""
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    clip = QPainterPath()
    clip.addEllipse(QRectF(0, 0, size, size))
    painter.setClipPath(clip)
    source = QPixmap()
    if data and source.loadFromData(bytes(data)):
        scaled = source.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        painter.drawPixmap((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    else:
        grad = QLinearGradient(0, 0, 0, size)
        base = QColor(GREEN)
        grad.setColorAt(0, base.lighter(165))
        grad.setColorAt(1, base.lighter(115))
        painter.fillRect(0, 0, size, size, grad)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        head = size * 0.36
        painter.drawEllipse(QPointF(size / 2, size * 0.40), head / 2 + 2, head / 2 + 4)
        painter.drawEllipse(QPointF(size / 2, size * 1.02), size * 0.42, size * 0.36)
    painter.end()
    return out


class UserHeader(QWidget):
    def __init__(self, user_provider: Callable[[], dict[str, Any] | None],
                 photo_provider: Callable[[], bytes | None] | None = None,
                 clock: Callable[[], dt.datetime] = dt.datetime.now, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._user_provider = user_provider
        self._photo_provider = photo_provider or (lambda: None)
        self._clock = clock
        self.setStyleSheet(_STYLE)
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(14)

        self.photo = QLabel()
        self.photo.setObjectName("uhPhoto")
        self.photo.setFixedSize(PHOTO_SIZE + 4, PHOTO_SIZE + 4)
        self.photo.setAlignment(Qt.AlignCenter)
        line.addWidget(self.photo)
        who = QVBoxLayout()
        who.setSpacing(0)
        self.name = QLabel()
        self.name.setObjectName("uhName")
        self.job = QLabel()
        self.job.setObjectName("uhJob")
        who.addWidget(self.name)
        who.addWidget(self.job)
        line.addLayout(who)
        line.addWidget(self._separator())

        clock_box = QVBoxLayout()
        clock_box.setSpacing(0)
        self.time = QLabel()
        self.time.setObjectName("uhTime")
        self.time.setLayoutDirection(Qt.LeftToRight)
        self.date = QLabel()
        self.date.setObjectName("uhDate")
        clock_box.addWidget(self.time, 0, Qt.AlignCenter)
        clock_box.addWidget(self.date, 0, Qt.AlignCenter)
        line.addLayout(clock_box)

        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(TICK_MS)
        self.timer.timeout.connect(self.tick)
        self.refresh()
        self.tick()
        self.timer.start()

    @staticmethod
    def _separator() -> QFrame:
        sep = QFrame()
        sep.setObjectName("uhSep")
        sep.setFixedSize(1, 38)
        return sep

    def refresh(self) -> None:
        user = self._user_provider() or {}
        self.name.setText(display_name(user))
        job = (user.get("job_title") or "").strip()
        self.job.setText(job)
        self.job.setVisible(bool(job))
        try:
            photo = self._photo_provider()
        except Exception:  # noqa: BLE001 — الصورة مش أهم من الهيدر
            photo = None
        self.photo.setPixmap(round_photo(photo))

    def tick(self) -> None:
        now = self._clock()
        self.time.setText(time_text(now))
        self.date.setText(date_text(now))
