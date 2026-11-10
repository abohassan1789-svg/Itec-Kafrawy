"""اختبارات «بيانات الشركة» (القاعدة + الـ Service + الشاشة) على قاعدة مؤقتة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.company_service import CompanyService
from app.services.errors import BusinessError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    answers = {"save": True}
    monkeypatch.setattr(messages, "ask_save", lambda _p: answers["save"])
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _x: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    yield {"db": temp_db, "shown": shown, "answers": answers}
    SESSION.clear()


def make_screen(db):
    from app.ui.screens.company_screen import CompanyScreen

    return CompanyScreen(CompanyService(db))


def png_bytes(size: int = 40) -> bytes:
    image = QImage(size, size, QImage.Format_RGB32)
    image.fill(QColor("#137A38"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data)


def test_table_allows_one_row_only(env):
    db = env["db"]
    db.execute("INSERT INTO core.company_settings (name_ar) VALUES ('شركة')")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO core.company_settings (id, name_ar) VALUES (2, 'تانية')")
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE core.company_settings SET default_vat_rate = 120")
    row = db.fetch_one("SELECT base_currency_code, default_vat_rate, fiscal_year_start_month FROM core.company_settings")
    assert row == {"base_currency_code": "EGP", "default_vat_rate": Decimal("14.00"), "fiscal_year_start_month": 1}


def test_first_open_shows_defaults_and_toolbar(env):
    screen = make_screen(env["db"])
    assert screen.mode == "view" and screen.current_id == 1
    assert screen.name_ar.text() == "" and screen.vat_rate.value() == 14
    assert screen.start_month.currentText() == "يناير"
    assert "EGP" in screen.base_currency.text() and not screen.base_currency.isEnabled()
    assert "لسه ماتسجلتش" in screen.updated_label.text()
    # سجل واحد: مفيش جديد ولا حذف ولا قائمة
    assert screen.new_button.isHidden() and screen.delete_button.isHidden() and screen.export_button.isHidden()
    assert screen._list_box.isHidden()
    assert screen.edit_button.isEnabled() and not screen.save_button.isEnabled()
    assert not screen.name_ar.isEnabled()


def test_edit_save_creates_then_updates_with_audit(env):
    db = env["db"]
    screen = make_screen(db)
    screen.edit_record()
    assert screen.mode == "edit" and screen.name_ar.isEnabled() and screen.save_button.isEnabled()
    screen.name_ar.setText("  شركة آي تك  ")
    screen.name_en.setText("ITEC")
    screen.commercial_register.setText("00123")
    screen.vat_rate.setValue(10)
    screen.start_month.setCurrentIndex(6)          # يوليو
    screen.save()
    assert env["shown"] == ["saved"] and screen.mode == "view"
    row = db.fetch_one("SELECT * FROM core.company_settings")
    assert row["name_ar"] == "شركة آي تك" and row["commercial_register"] == "00123"
    assert row["default_vat_rate"] == Decimal("10.00") and row["fiscal_year_start_month"] == 7
    assert row["base_currency_code"] == "EGP"
    assert "آخر تعديل" in screen.updated_label.text()

    screen.edit_record()
    screen.city.setText("القاهرة")
    screen.save()
    logs = db.fetch_all("SELECT action, details FROM sec.audit_log WHERE entity='core.company_settings' ORDER BY id")
    assert [log["action"] for log in logs] == ["create", "update"]
    assert logs[1]["details"]["changed"] == ["city"]


def test_cancel_restores_saved_values(env):
    screen = make_screen(env["db"])
    CompanyService(env["db"]).save_company({"name_ar": "شركة", "default_vat_rate": 14})
    screen.refresh_screen()
    screen.edit_record()
    screen.name_ar.setText("اسم غلط")
    screen.cancel()
    assert screen.mode == "view" and screen.name_ar.text() == "شركة"


def test_validation_messages(env):
    service = CompanyService(env["db"])
    with pytest.raises(BusinessError, match="مطلوب"):
        service.save_company({"name_ar": "  "})
    with pytest.raises(BusinessError, match="البريد"):
        service.save_company({"name_ar": "شركة", "email": "info-at-example"})
    with pytest.raises(BusinessError, match="ض.ق.م"):
        service.save_company({"name_ar": "شركة", "default_vat_rate": 140})
    with pytest.raises(BusinessError, match="أول شهر"):
        service.save_company({"name_ar": "شركة", "fiscal_year_start_month": 13})
    # الشاشة بتعرض الخطأ بالعربي ومابتحفظش
    screen = make_screen(env["db"])
    screen.edit_record()
    screen.save()
    assert any("مطلوب" in s for s in env["shown"]) and screen.mode == "edit"
    assert env["db"].fetch_value("SELECT COUNT(*) FROM core.company_settings") == 0


def test_logo_upload_save_and_remove(env, tmp_path):
    db = env["db"]
    screen = make_screen(db)
    logo = tmp_path / "logo.png"
    logo.write_bytes(png_bytes())
    big = tmp_path / "big.png"
    big.write_bytes(b"0" * (2 * 1024 * 1024 + 1))
    screen.edit_record()
    assert not screen.set_logo_file(str(big)) and "أكبر من 2 ميجا" in env["shown"][-1]
    assert screen.set_logo_file(str(logo))
    screen.name_ar.setText("شركة")
    screen.save()
    data, mime = CompanyService(db).get_logo()
    assert data == logo.read_bytes() and mime == "image/png"
    assert not screen.logo_preview.pixmap().isNull()
    log = db.fetch_one("SELECT details FROM sec.audit_log WHERE entity='core.company_settings'")
    assert log["details"]["extra"] == {"logo_changed": True} and "logo" not in log["details"]["after"]

    screen.edit_record()
    screen._remove_logo()
    screen.save()
    assert CompanyService(db).get_logo() == (None, None)


def test_needs_edit_and_save_permissions(env):
    SESSION.start({"id": 999, "username": "clerk", "is_admin": False, "full_access": False},
                  {"sys.company.view", "sys.company.edit"})
    with pytest.raises(PermissionDenied):
        CompanyService(env["db"]).save_company({"name_ar": "شركة"})
    screen = make_screen(env["db"])
    screen.edit_record()
    assert screen.mode == "edit" and not screen.save_button.isEnabled()
