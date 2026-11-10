"""اللوج: ملف دوّار في %LOCALAPPDATA%\\ITEC\\logs\\app.log + الكونسول وقت التطوير."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config.paths import app_root, logs_dir

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


def setup_logging(level: str = "INFO") -> Path | None:
    """يجهّز اللوج مرة واحدة ويرجّع مسار ملف اللوج."""
    global _configured
    root = logging.getLogger()
    if _configured:
        return None
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    formatter = logging.Formatter(_FORMAT)

    log_file: Path | None = None
    for folder in (logs_dir(), app_root() / "logs"):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            log_file = folder / "app.log"
            handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
            handler.setFormatter(formatter)
            root.addHandler(handler)
            break
        except OSError:
            log_file = None

    if sys.stderr is not None:  # في الـ exe من غير كونسول sys.stderr بيبقى None
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    logging.getLogger("psycopg").setLevel(logging.WARNING)
    _configured = True
    return log_file
