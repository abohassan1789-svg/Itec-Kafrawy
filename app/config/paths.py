"""مسارات البرنامج (شغالة في وضع التطوير وفي ملف exe بتاع PyInstaller)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app import APP_DIRNAME


def app_root() -> Path:
    """جذر البرنامج: فولدر الـ exe لو متجمّع، وإلا فولدر المشروع."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """مكان الملفات المرفقة (migrations، أيقونات) — جوه _MEIPASS لو متجمّع."""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else app_root()


def user_data_dir() -> Path:
    """%LOCALAPPDATA%\\ITEC (اللوج وإعدادات المستخدم) — وإلا فولدر جنب البرنامج."""
    local = os.getenv("LOCALAPPDATA")
    base = Path(local) / APP_DIRNAME if local else app_root() / "data"
    return base


def logs_dir() -> Path:
    return user_data_dir() / "logs"
