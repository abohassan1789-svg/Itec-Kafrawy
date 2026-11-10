"""شاشة «بيانات الشركة» — النموذج 1 «زي Hr GAD: الفورم مجموعات في عمودين» (اختيار المستخدم 2026-11-07).

```
┌ [تعديل][حفظ][إلغاء][تحديث][خروج]                                   (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ [اللوجو] ┌ البيانات الأساسية ┐        │ ┌ السجل والضرايب ┐                    │
│ رفع/إزالة└───────────────────┘        │ ┌ الإعدادات المالية ┐ (EGP 🔒 · 14%)   │
│ ┌ العنوان والاتصال ┐                  │ ┌ الطباعة ┐                           │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* سجل واحد بس للشركة ← مفيش «جديد» ولا «حذف» ولا قائمة بحث.
* أول مرة (مفيش بيانات): «تعديل» واكتب البيانات ← «حفظ» بيضيفها.
* العملة الأساسية EGP ثابتة (قرار المستخدم)، واللوجو أقصى 2 ميجا وبيتحفظ مع «حفظ».
"""

from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.repositories.settings_repository import COMPANY_ID
from app.services.company_service import (
    BASE_CURRENCY,
    BASE_CURRENCY_NAME,
    MAX_LOGO_BYTES,
    MONTHS_AR,
    CompanyService,
)
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN, GREEN_DARK, RED, TEXT_MUTED, WARNING_TEXT, button_style

LOGO_SIZE = 150
NO_LOGO_TEXT = "لا يوجد لوجو"
LOGO_FILTER = "الصور (*.png *.jpg *.jpeg *.gif *.bmp *.webp)"
_MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".bmp": "image/bmp", ".webp": "image/webp"}
_TEXT_WIDGETS = ("name_ar", "name_en", "activity", "address", "city", "phone", "mobile", "email", "website",
                 "commercial_register", "tax_registration_no", "tax_office", "invoice_footer")


def _form() -> QFormLayout:
    form = QFormLayout()
    form.setSpacing(8)
    form.setContentsMargins(12, 18, 12, 10)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    return form


class CompanyScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "company"
    TITLE = "بيانات الشركة"
    ICON = "🏢"
    FORM_TITLE = "بيانات الشركة"
    COLUMNS = [("name_ar", "الاسم")]

    def __init__(self, company: CompanyService | None = None, parent: QWidget | None = None) -> None:
        self.company = company or CompanyService()
        self._logo_bytes: bytes | None = None
        self._logo_mime: str | None = None
        super().__init__(parent)
        self.load(COMPANY_ID)

    # ---- البناء ------------------------------------------------------------
    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        self._list_box = list_box                 # سجل واحد ← مفيش قائمة (بنمسكها عشان ماتتمسحش)
        list_box.hide()
        content.addWidget(form_box, 1)

    def build_form(self, box: QGroupBox) -> None:
        for name in _TEXT_WIDGETS:
            setattr(self, name, QLineEdit())
        self.name_ar.setPlaceholderText("مطلوب")
        self.activity.setPlaceholderText("مثال: توريد وتركيب أنظمة BMS والتيار الخفيف")
        self.email.setPlaceholderText("info@example.com")
        self.website.setPlaceholderText("www.example.com")
        self.invoice_footer.setPlaceholderText("مثال: شكراً لتعاملكم معنا")
        for name, length in (("name_ar", 200), ("name_en", 200), ("activity", 200), ("address", 300),
                             ("city", 100), ("phone", 40), ("mobile", 40), ("email", 120), ("website", 120),
                             ("commercial_register", 60), ("tax_registration_no", 60), ("tax_office", 120),
                             ("invoice_footer", 300)):
            getattr(self, name).setMaxLength(length)

        self.base_currency = QLineEdit(f"{BASE_CURRENCY} — {BASE_CURRENCY_NAME}  🔒")
        self.base_currency.setReadOnly(True)
        self.base_currency.setEnabled(False)
        self.vat_rate = QDoubleSpinBox()
        self.vat_rate.setRange(0, 100)
        self.vat_rate.setDecimals(2)
        self.vat_rate.setSuffix(" %")
        self.vat_rate.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.start_month = QComboBox()
        for number, name in enumerate(MONTHS_AR, start=1):
            self.start_month.addItem(name, number)
        self.print_logo = QCheckBox("اللوجو في الطباعة")
        self.print_name_en = QCheckBox("الاسم بالإنجليزي في الهيدر")

        columns = QHBoxLayout(box)
        columns.setContentsMargins(14, 22, 14, 12)
        columns.setSpacing(14)

        # العمود اليمين: اللوجو + البيانات الأساسية · العنوان والاتصال
        right = QVBoxLayout()
        right.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addLayout(self._build_logo_column())           # RTL: أول عنصر = يمين
        basic = QGroupBox("البيانات الأساسية")
        form = _form()
        form.addRow("الاسم بالعربي *", self.name_ar)
        form.addRow("الاسم بالإنجليزي", self.name_en)
        form.addRow("النشاط", self.activity)
        basic.setLayout(form)
        top.addWidget(basic, 1)
        right.addLayout(top)
        contact = QGroupBox("العنوان والاتصال")
        form = _form()
        form.addRow("العنوان", self.address)
        form.addRow("المدينة", self.city)
        form.addRow("التليفون", self.phone)
        form.addRow("الموبايل", self.mobile)
        form.addRow("البريد", self.email)
        form.addRow("الموقع", self.website)
        contact.setLayout(form)
        right.addWidget(contact)
        right.addStretch(1)

        # العمود الشمال: السجل والضرايب · الإعدادات المالية · الطباعة
        left = QVBoxLayout()
        left.setSpacing(10)
        legal = QGroupBox("السجل والضرايب")
        form = _form()
        form.addRow("السجل التجاري", self.commercial_register)
        form.addRow("رقم التسجيل الضريبي", self.tax_registration_no)
        form.addRow("مأمورية الضرايب", self.tax_office)
        legal.setLayout(form)
        left.addWidget(legal)
        finance = QGroupBox("الإعدادات المالية")
        form = _form()
        form.addRow("العملة الأساسية", self.base_currency)
        currency_note = QLabel("ثابتة (جنيه مصري) — وهتتقفل نهائي بعد أول قيد")
        currency_note.setStyleSheet(f"color:{WARNING_TEXT}; font-weight:700;")
        form.addRow("", currency_note)
        form.addRow("ض.ق.م الافتراضية", self.vat_rate)
        form.addRow("أول شهر في السنة المالية", self.start_month)
        finance.setLayout(form)
        left.addWidget(finance)
        printing = QGroupBox("الطباعة")
        form = _form()
        form.addRow("سطر تحت الفواتير", self.invoice_footer)
        checks = QHBoxLayout()
        checks.setSpacing(18)
        checks.addWidget(self.print_logo)
        checks.addWidget(self.print_name_en)
        checks.addStretch(1)
        form.addRow("", checks)
        printing.setLayout(form)
        left.addWidget(printing)
        self.updated_label = QLabel("")
        self.updated_label.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        self.updated_label.setAlignment(Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter)
        left.addWidget(self.updated_label)
        left.addStretch(1)

        columns.addLayout(right, 1)                        # RTL: يمين
        columns.addLayout(left, 1)                         # شمال

    def _build_logo_column(self) -> QVBoxLayout:
        column = QVBoxLayout()
        column.setSpacing(6)
        column.setAlignment(Qt.AlignTop)
        self.logo_preview = QLabel(NO_LOGO_TEXT)
        self.logo_preview.setFixedSize(LOGO_SIZE, LOGO_SIZE)
        self.logo_preview.setAlignment(Qt.AlignCenter)
        self.logo_preview.setStyleSheet(
            f"QLabel {{ background:#F8FAFC; border:1px dashed {GREEN}; border-radius:10px; "
            "color:#94A3B8; font-weight:800; }")
        column.addWidget(self.logo_preview)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.upload_logo_button = QPushButton("رفع")
        self.upload_logo_button.setStyleSheet(button_style(GREEN, GREEN_DARK))
        self.upload_logo_button.setToolTip("بيتحفظ مع «حفظ» (أقصى 2 ميجا)")
        self.upload_logo_button.clicked.connect(self._upload_logo)
        self.remove_logo_button = QPushButton("إزالة")
        self.remove_logo_button.setStyleSheet(
            f"QPushButton {{ background:#FFFFFF; color:{RED}; border:1px solid #FCA5A5; border-radius:6px; "
            "font-weight:800; padding:7px 9px; } QPushButton:hover { background:#FEF2F2; }"
            "QPushButton:disabled { color:#CBD5E1; border-color:#E2E8F0; }")
        self.remove_logo_button.setToolTip("بيتشال مع «حفظ»")
        self.remove_logo_button.clicked.connect(self._remove_logo)
        buttons.addWidget(self.upload_logo_button)
        buttons.addWidget(self.remove_logo_button)
        column.addLayout(buttons)
        return column

    def editable_widgets(self) -> list[QWidget]:
        return [*(getattr(self, n) for n in _TEXT_WIDGETS), self.vat_rate, self.start_month, self.print_logo,
                self.print_name_en, self.upload_logo_button, self.remove_logo_button]

    # ---- مفيش قائمة ولا بحث (سجل واحد) -------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return []

    def open_lookup(self) -> None:
        """مفيش F1 — الشركة سجل واحد."""

    def show_empty(self) -> None:
        """مفيش «فاضي» هنا: الشاشة دايماً على سجل الشركة (أو القيم الافتراضية)."""
        self.load(COMPANY_ID)

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.company.get_company_or_default()

    def fill_form(self, record: dict) -> None:
        for name in _TEXT_WIDGETS:
            getattr(self, name).setText(record.get(name) or "")
        self.vat_rate.setValue(float(record.get("default_vat_rate") or 0))
        self.start_month.setCurrentIndex(max(0, self.start_month.findData(int(record.get("fiscal_year_start_month")
                                                                              or 1))))
        self.print_logo.setChecked(bool(record.get("print_logo", True)))
        self.print_name_en.setChecked(bool(record.get("print_name_en", True)))
        logo, mime = (None, None)
        if record.get("has_logo"):
            try:
                logo, mime = self.company.get_logo()
            except Exception:  # noqa: BLE001 — اللوجو مش لازم يوقف عرض البيانات
                logo, mime = None, None
        self._set_logo(logo, mime)
        if record.get("updated_at"):
            when = default_format("updated_at", record["updated_at"], record)
            self.updated_label.setText(f"آخر تعديل: ⁦{when}⁩")   # التاريخ معزول LTR عشان مايتقلبش
        else:
            self.updated_label.setText("بيانات الشركة لسه ماتسجلتش — دوس «تعديل» واكتبها وبعدين «حفظ».")

    def clear_form(self) -> None:
        self.fill_form(self.company.get_company_or_default())

    def collect(self) -> dict:
        data: dict[str, Any] = {name: getattr(self, name).text() for name in _TEXT_WIDGETS}
        data.update({
            "default_vat_rate": round(self.vat_rate.value(), 2),
            "fiscal_year_start_month": self.start_month.currentData(),
            "print_logo": self.print_logo.isChecked(),
            "print_name_en": self.print_name_en.isChecked(),
            "logo": self._logo_bytes,
            "logo_mime": self._logo_mime,
        })
        return data

    def save_record(self, payload: dict, record_id: Any) -> Any:
        # 0 = الشاشة شافت إن البيانات لسه ماتسجلتش (لو جهاز تاني سجّلها في الوقت ده ← تعارض)
        self.company.save_company(payload, expected_version=self.current_version or 0)
        return COMPANY_ID

    # ---- اللوجو ------------------------------------------------------------
    def _set_logo(self, data: bytes | None, mime: str | None) -> None:
        self._logo_bytes = data or None
        self._logo_mime = mime if data else None
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.logo_preview.setPixmap(pixmap.scaled(LOGO_SIZE - 6, LOGO_SIZE - 6, Qt.KeepAspectRatio,
                                                      Qt.SmoothTransformation))
        else:
            self.logo_preview.setPixmap(QPixmap())
            self.logo_preview.setText(NO_LOGO_TEXT)

    def _upload_logo(self) -> None:
        if self.mode != "edit":
            return
        path, _ = QFileDialog.getOpenFileName(self, "اختيار لوجو الشركة", "", LOGO_FILTER)
        if path:
            self.set_logo_file(path)

    def set_logo_file(self, path: str) -> bool:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            messages.warn(self, "لوجو الشركة", f"تعذّر قراءة الصورة:\n{exc}")
            return False
        if len(data) > MAX_LOGO_BYTES:
            messages.warn(self, "لوجو الشركة", "الصورة أكبر من 2 ميجا — اختار صورة أصغر.")
            return False
        if not QPixmap().loadFromData(data):
            messages.warn(self, "لوجو الشركة", "الملف ده مش صورة — اختار صورة PNG أو JPG.")
            return False
        self._set_logo(data, _MIME_BY_EXT.get(os.path.splitext(path)[1].lower(), "image/png"))
        return True

    def _remove_logo(self) -> None:
        if self.mode == "edit":
            self._set_logo(None, None)
