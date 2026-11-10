"""نقطة تشغيل البرنامج.

تسلسل الفتح (الخطة — نفس Hr GAD، القسم 2.9 في ملف 02):
ملف الاتصال موجود وشغال؟ لأ ← شاشة إعداد الاتصال
        ↓
شاشة التحميل ← الاتصال ← تنفيذ سكريبتات القاعدة (migrations)
        ↓
المدير الافتراضي + مزامنة الصلاحيات ← شاشة الدخول ← الواجهة الرئيسية (مكبّرة)
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_TITLE
from app.config.paths import resource_root
from app.config.settings import get_settings
from app.logging_setup import setup_logging

logger = logging.getLogger(__name__)

APP_ICON = resource_root() / "app" / "assets" / "app_icon.ico"


def _set_windows_app_id() -> None:
    """من غيرها ويندوز بيعرض لوجو بايثون في شريط المهام (البرنامج بيشتغل بـ pythonw)."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AHCODE.ITEC.Accounting")
        except Exception:  # noqa: BLE001 — مجرد شكل
            logger.debug("تعذّر ضبط AppUserModelID", exc_info=True)


def run_app() -> int:
    setup_logging(get_settings().log_level)
    logger.info("تشغيل %s", APP_TITLE)

    _set_windows_app_id()
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.common import no_wheel
    from app.ui.common.theme import apply_app_theme

    apply_app_theme(app)
    if APP_ICON.exists():                  # لوجو ITEC لكل الشبابيك وشريط المهام
        app.setWindowIcon(QIcon(str(APP_ICON)))
    no_wheel.install(app)

    from app.ui.screens.connection_screen import ensure_connection

    if not ensure_connection():
        logger.info("المستخدم قفل شاشة الاتصال من غير حفظ")
        return 0

    from app.database.db import get_db, reset_db
    from app.services.startup_service import prepare_database
    from app.ui.common.loading_splash import LoadingSplash

    reset_db()  # عشان ياخد ملف الاتصال اللي اتحفظ حالاً
    splash = LoadingSplash()
    splash.show()
    app.processEvents()
    result = prepare_database(get_db(), step=splash.set_step)
    splash.close()

    if not result.ok:
        QMessageBox.critical(None, "تنبيه قاعدة البيانات", result.message)
        return 1

    from app.services.auth_service import AuthService
    from app.ui.screens.login_screen import LoginWindow

    auth = AuthService(get_db())
    login = LoginWindow(auth)
    if login.exec() != LoginWindow.Accepted:
        logger.info("المستخدم قفل شاشة الدخول")
        get_db().close()
        return 0

    from app.ui.main_window import MainWindow

    window = MainWindow(auth)
    window.showMaximized()
    code = app.exec()
    auth.logout()          # لو اتقفل بأي طريقة تانية (قفل الجهاز) وقت الخروج يتسجل برضه
    get_db().close()
    return code


def main() -> None:
    sys.exit(run_app())


if __name__ == "__main__":
    main()
