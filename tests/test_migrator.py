import pytest

from app.database import migrator

EXPECTED_SCHEMAS = {"sec", "core", "acc", "party", "inv", "sales", "purch", "cash", "exp"}


def test_discover_real_migrations():
    found = migrator.discover()
    assert found and found[0].version == 1
    assert [m.version for m in found] == sorted({m.version for m in found})


def test_discover_rejects_bad_names(tmp_path):
    (tmp_path / "1_bad.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(migrator.MigrationError):
        migrator.discover(tmp_path)


def test_discover_rejects_duplicates(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(migrator.MigrationError):
        migrator.discover(tmp_path)


def test_migrate_fresh_db_creates_schemas_and_is_idempotent(temp_db):
    applied = migrator.migrate(temp_db)
    assert [m.version for m in applied] == [m.version for m in migrator.discover()]

    rows = temp_db.fetch_all("SELECT nspname FROM pg_namespace")
    assert EXPECTED_SCHEMAS <= {r["nspname"] for r in rows}
    assert temp_db.fetch_value("SELECT value FROM core.app_info WHERE key='base_currency'") == "EGP"

    assert migrator.migrate(temp_db) == []          # تاني مرة: مفيش حاجة تتنفذ
    assert migrator.current_version(temp_db) == migrator.discover()[-1].version


def test_failed_migration_rolls_back(temp_db, tmp_path):
    (tmp_path / "0001_ok.sql").write_text("CREATE TABLE core.t_ok (id int);", encoding="utf-8")
    (tmp_path / "0002_bad.sql").write_text(
        "CREATE TABLE core.t_half (id int);\nSELECT * FROM no_such_table;", encoding="utf-8")
    with pytest.raises(migrator.MigrationError):
        migrator.migrate(temp_db, tmp_path)
    assert migrator.current_version(temp_db) == 1
    assert temp_db.fetch_value("SELECT to_regclass('core.t_half')") is None   # اترجعت كلها
    assert temp_db.fetch_value("SELECT to_regclass('core.t_ok')") is not None


def test_edited_migration_is_rejected(temp_db, tmp_path):
    script = tmp_path / "0001_x.sql"
    script.write_text("CREATE TABLE core.t_x (id int);", encoding="utf-8")
    migrator.migrate(temp_db, tmp_path)
    script.write_text("CREATE TABLE core.t_x (id int, extra int);", encoding="utf-8")
    with pytest.raises(migrator.MigrationError, match="اتعدّل"):
        migrator.migrate(temp_db, tmp_path)


def test_transaction_rolls_back_on_error(temp_db):
    temp_db.execute("CREATE TABLE public.t_tx (id int)")
    with pytest.raises(RuntimeError):
        with temp_db.transaction() as cur:
            cur.execute("INSERT INTO public.t_tx VALUES (1)")
            raise RuntimeError("stop")
    assert temp_db.fetch_value("SELECT count(*) FROM public.t_tx") == 0
