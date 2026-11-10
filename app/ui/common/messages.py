"""الرسائل الموحّدة (زي Hr GAD): قبل الحفظ «هل تريد الحفظ؟» (الافتراضي «لا») وبعده «تم الحفظ بنجاح»،
وأخطاء قواعد العمل والقاعدة بالعربي."""

from __future__ import annotations

import logging

import psycopg
from PySide6.QtWidgets import QMessageBox, QWidget

from app.config.db_errors import describe_db_error
from app.services.errors import BusinessError

logger = logging.getLogger(__name__)

SAVE_TITLE = "تأكيد الحفظ"
SAVE_QUESTION = "هل تريد الحفظ؟"
SAVED_TITLE = "تم الحفظ"
SAVED_TEXT = "تم الحفظ بنجاح"
_WIDE = ("QLabel#qt_msgbox_label, QLabel#qt_msgbox_informativelabel { min-width: 430px; }")  # رسالة طويلة ← أعرض


def ask_yes_no(parent: QWidget | None, title: str, text: str) -> bool:
    """سؤال بـ «نعم / لا» — الافتراضي «لا»."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    if len(text) > 80:                          # سؤال طويل (زي التفريع) ← أعرض عشان مايتكسرش
        box.setStyleSheet(_WIDE)
    box.setWindowTitle(title)
    box.setText(text)
    yes =box.addButton("نعم", QMessageBox.YesRole)
    no = box.addButton("لا", QMessageBox.NoRole)
    box.setDefaultButton(no)
    box.setEscapeButton(no)
    box.exec()
    return box.clickedButton() is yes


def ask_save(parent: QWidget | None) -> bool:
    return ask_yes_no(parent, SAVE_TITLE, SAVE_QUESTION)


def info(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Information)
    box.setWindowTitle(title)
    box.setText(text)
    box.addButton("تمام", QMessageBox.AcceptRole)
    box.exec()


def warn(parent: QWidget | None, title: str, text: str) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    if len(text) > 80:                          # رسالة طويلة (زي التعارض) ← أعرض عشان ماتتكسرش
        box.setStyleSheet(_WIDE)
    box.setWindowTitle(title)
    box.setText(text)
    box.addButton("تمام", QMessageBox.AcceptRole)
    box.exec()


CONFLICT_TITLE = "السجل اتعدّل من جهاز تاني"
CONFLICT_REFRESH = "تحديث"
CONFLICT_STAY = "رجوع للتعديل"
CONFLICT_HELP = ("«تحديث» = تشوف التعديل الجديد (اللي انت كتبته هيتلغي) وتعدّل تاني.\n"
                 "«رجوع للتعديل» = تفضل في الشاشة باللي كتبته (مااتحفظش) — وتقدر «إلغاء» بعدين.")
CONFLICT_HELP_DELETED = ("«تحديث» = الشاشة تتحدّث (السجل ده مابقاش موجود).\n"
                         "«رجوع للتعديل» = تفضل في الشاشة باللي كتبته (مااتحفظش) — وتقدر «إلغاء» بعدين.")
REFRESHED_NOTE = "الشاشة اتحدّثت بآخر نسخة."


def ask_conflict(parent: QWidget | None, exc: BaseException) -> bool:
    """جهاز تاني حفظ قبلك: True = «تحديث» · False = «رجوع للتعديل» (الافتراضي — عشان مايضيعش اللي اتكتب)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(CONFLICT_TITLE)
    box.setStyleSheet(_WIDE)
    box.setText(error_text(exc))
    box.setInformativeText(CONFLICT_HELP_DELETED if getattr(exc, "deleted", False) else CONFLICT_HELP)
    refresh = box.addButton(CONFLICT_REFRESH, QMessageBox.AcceptRole)
    stay = box.addButton(CONFLICT_STAY, QMessageBox.RejectRole)
    box.setDefaultButton(stay)
    box.setEscapeButton(stay)
    box.exec()
    return box.clickedButton() is refresh


def show_saved(parent: QWidget | None) -> None:
    info(parent, SAVED_TITLE, SAVED_TEXT)


def error_text(exc: BaseException) -> str:
    """نص الخطأ اللي يتعرض للمستخدم."""
    if isinstance(exc, BusinessError):
        return str(exc)
    if isinstance(exc, psycopg.Error):
        from app.config.settings import get_settings

        return describe_db_error(exc, password=get_settings().db_password)
    logger.exception("خطأ غير متوقع", exc_info=exc)
    return f"حصل خطأ غير متوقع:\n{exc}"


def show_error(parent: QWidget | None, title: str, exc: BaseException) -> None:
    if isinstance(exc, BusinessError):
        warn(parent, title, error_text(exc))
    else:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setText(error_text(exc))
        box.addButton("تمام", QMessageBox.AcceptRole)
        box.exec()
