import json

import pytest

from app.config import connection_config as cc
from app.config import crypto


def test_crypto_roundtrip():
    token = crypto.encrypt("سر@123")
    assert crypto.is_encrypted(token)
    assert "سر@123" not in token
    assert crypto.decrypt(token) == "سر@123"


def test_crypto_empty_and_legacy_plain():
    assert crypto.decrypt("") == ""
    assert crypto.decrypt("old-plain") == "old-plain"


def test_profile_save_never_writes_plain_password(tmp_path, monkeypatch):
    target = tmp_path / "connection.json"
    monkeypatch.setenv("ITEC_CONFIG_PATH", str(target))
    profile = cc.ConnectionProfile(db_password="TopSecret!9")
    cc.save(profile)

    raw = target.read_text(encoding="utf-8")
    assert "TopSecret!9" not in raw
    data = json.loads(raw)
    assert data["database"]["name"] == "DBITEC"
    assert crypto.is_encrypted(data["database"]["password"])

    loaded = cc.load()
    assert loaded is not None and loaded.db_password == "TopSecret!9"


def test_load_missing_or_broken(tmp_path, monkeypatch):
    target = tmp_path / "connection.json"
    monkeypatch.setenv("ITEC_CONFIG_PATH", str(target))
    assert cc.load() is None
    target.write_text("{not json", encoding="utf-8")
    assert cc.load() is None


@pytest.mark.parametrize("field,value", [
    ("server_address", ""), ("server_port", 0), ("db_name", " "), ("db_user", ""), ("sslmode", "xx"),
])
def test_profile_validation(field, value):
    profile = cc.ConnectionProfile()
    assert profile.validate() == []
    setattr(profile, field, value)
    assert profile.validate()
