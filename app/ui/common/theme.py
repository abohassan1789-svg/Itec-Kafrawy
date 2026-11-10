"""الثيم — كل الألوان والخطوط وأزرار شريط الأدوات في ملف واحد (منقولة من Hr GAD).

أي شاشة بتاخد ألوانها من هنا بس، عشان التصميم يفضل واحد في البرنامج كله
(التفاصيل: docs/02_hrgad_design_and_security_study.md القسم 2).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

# ---- الخط ------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"
FONT_FALLBACKS = "'Segoe UI', 'Tahoma', 'Arial'"
BASE_FONT_PT = 12  # = 16px
FONT_SCALE = BASE_FONT_PT / 10


def scaled(size: float) -> int:
    """مقاس متظبط على خط 10 ← نفس المقاس على خط 12."""
    return int(round(size * FONT_SCALE))


# ---- الألوان ---------------------------------------------------------------
GREEN = "#137A38"            # الأساسي: عناوين، هيدر الجداول، أزرار أساسية
GREEN_DARK = "#0F6B30"       # الشريط الجانبي، hover
TEXT = "#111827"
TEXT_MUTED = "#64748B"
BORDER = "#D9E2EC"
BORDER_CARD = "#E2E8F0"
BORDER_INPUT = "#CBD5E1"
BG_CONTENT = "#F1F5F9"
BG_CARD = "#FFFFFF"
ROW_ALT = "#F8FAFC"
ROW_SELECTED = "#DDF3E6"
FIRST_COLUMN = "#ECFDF3"
RED = "#DC2626"
RED_DARK = "#B91C1C"
BADGE_BG = "#ECFDF3"
BADGE_TEXT = "#087443"
BADGE_BORDER = "#B7E4C7"
DISABLED = "#CBD5E1"
WARNING_BG = "#FFF7ED"
WARNING_TEXT = "#9A3412"

# ---- ألوان أزرار شريط الأدوات (ثابتة في كل الشاشات) --------------------------
BUTTON_COLORS: dict[str, tuple[str, str]] = {
    "new": ("#1A7A3C", "#166534"),
    "edit": ("#64748B", "#475569"),
    "save": ("#2563EB", "#1D4ED8"),
    "delete": ("#DC2626", "#B91C1C"),
    "cancel": ("#6B7280", "#4B5563"),
    "exit": ("#374151", "#1F2937"),
    "delete_all": ("#991B1B", "#7F1D1D"),
    "export": ("#0E7490", "#155E75"),
    "import": ("#7C3AED", "#6D28D9"),
    "toggle": ("#B45309", "#92400E"),
    "lock": ("#B45309", "#92400E"),
    "rate": ("#0F766E", "#115E59"),
    "login_log": ("#0F766E", "#115E59"),
    "preview_permissions": ("#6D28D9", "#5B21B6"),
    "primary": (GREEN, GREEN_DARK),
}


def button_style(bg: str, hover: str, fg: str = "#FFFFFF") -> str:
    return (
        f"QPushButton {{ background:{bg}; color:{fg}; border:none; border-radius:6px; "
        "font-weight:800; padding:7px 9px; min-height:24px; }"
        f"QPushButton:hover {{ background:{hover}; }}"
        f"QPushButton:disabled {{ background:{DISABLED}; color:#FFFFFF; }}"
    )


def toolbar_button_style(kind: str) -> str:
    if kind == "refresh":  # تحديث: أبيض بحد رمادي
        return (
            f"QPushButton {{ background:#FFFFFF; color:{TEXT}; border:1px solid {BORDER_INPUT}; "
            "border-radius:6px; font-weight:800; padding:7px 9px; min-height:24px; }"
            "QPushButton:hover { background:#F3F4F6; }"
            f"QPushButton:disabled {{ color:{DISABLED}; }}"
        )
    bg, hover = BUTTON_COLORS.get(kind, BUTTON_COLORS["primary"])
    return button_style(bg, hover)


def app_stylesheet() -> str:
    """الستايل العام للبرنامج (حقول، مجموعات، جداول)."""
    return f"""
    QWidget {{ font-family: {FONT_FALLBACKS}; color:{TEXT}; }}
    QMainWindow, QDialog {{ background:{BG_CONTENT}; }}
    QGroupBox {{
        background:{BG_CARD}; border:1px solid {BORDER_CARD}; border-radius:8px;
        margin-top:14px; padding:14px 10px 10px 10px; font-weight:800;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; subcontrol-position: top right;
        padding:0 8px; color:{GREEN}; font-weight:900;
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit, QPlainTextEdit {{
        background:#FFFFFF; border:1px solid {BORDER_INPUT}; border-radius:7px;
        padding:6px 8px; min-height:26px;
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QDateEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border:1px solid {GREEN}; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled,
    QTextEdit:disabled, QPlainTextEdit:disabled {{ background:{ROW_ALT}; color:{TEXT_MUTED}; }}
    QTableView, QTableWidget {{
        background:#FFFFFF; alternate-background-color:{ROW_ALT};
        border:1px solid {BORDER_CARD}; gridline-color:{BORDER_CARD};
        selection-background-color:{ROW_SELECTED}; selection-color:{TEXT};
    }}
    QHeaderView::section {{
        background:{GREEN}; color:#FFFFFF; font-weight:900; padding:10px 8px; border:none;
        border-left:1px solid {GREEN_DARK};
    }}
    QToolTip {{ background:#FFFFFF; color:{TEXT}; border:1px solid {BORDER_INPUT}; }}
    """


def apply_app_theme(app: QApplication) -> None:
    app.setLayoutDirection(Qt.RightToLeft)
    app.setStyle("Fusion")
    app.setFont(QFont(FONT_FAMILY, BASE_FONT_PT))
    app.setStyleSheet(app_stylesheet())
