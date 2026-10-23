"""Tests for the schema files, the SQLite -> PostgreSQL data move and the demo seed."""
import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
import pytest

from conftest import ROOT, TEST_URL, alembic, reset_db

sys.path.insert(0, ROOT)
import migrate_sqlite_to_postgres as mig  # noqa: E402


MIGRATIONS = ["0001_initial_schema.py", "0002_tasks.py", "0003_text_translations.py"]  # in order


def _migration_module(name="0001_initial_schema.py"):
    path = os.path.join(ROOT, "migrations", "versions", name)
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _norm(stmt):
    return re.sub(r"\s+", " ", stmt).strip()


def test_schema_sql_matches_migration():
    text = open(os.path.join(ROOT, "schema.sql"), encoding="utf-8").read()
    text = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
    from_file = [_norm(s) for s in text.split(";") if s.strip()]
    assert from_file == [_norm(s) for name in MIGRATIONS for s in _migration_module(name).statements()]


def test_every_migration_is_listed():
    found = sorted(f for f in os.listdir(os.path.join(ROOT, "migrations", "versions")) if f.endswith(".py"))
    assert found == MIGRATIONS


@pytest.fixture
def empty_db(fresh_db):
    reset_db()
    return TEST_URL


def _count(table):
    with psycopg.connect(TEST_URL, prepare_threshold=None) as c:
        return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


@pytest.fixture
def old_db(tmp_path):
    """A SQLite file in the old format, with the awkward cases the real data can have."""
    path = tmp_path / "cattle.db"
    db = sqlite3.connect(path)
    db.executescript(open(os.path.join(ROOT, "tests", "sqlite_schema.sql"), encoding="utf-8").read())
    db.executescript("""
        INSERT INTO users (id, username, password_hash, full_name, role, created_at)
            VALUES (1, 'admin', 'x', 'Admin', 'admin', '2025-01-05 02:30:00'),
                   (4, 'cust', 'x', 'Customer', 'customer', '2025-02-01 10:00:00');
        -- the calf (id 2) points at a mother with a higher id (5)
        INSERT INTO cattle (id, tag_no, sex, dob, weight_kg, dam_id) VALUES (5, 'MOM', 'Female', '2019-03-01', 410.333, NULL);
        INSERT INTO cattle (id, tag_no, sex, dob, dam_id, purchase_price) VALUES (2, 'CALF', 'Female', '2025-06-01', 5, 45000);
        INSERT INTO products (id, name, category, price, unit, stock) VALUES (7, 'Ghee', 'Ghee', 1450, 'jar', 18.5);
        INSERT INTO orders (id, user_id, created_at, total) VALUES (9, 4, '2025-03-01 08:15:00', 2900);
        INSERT INTO order_items (id, order_id, product_id, name, price, qty) VALUES (3, 9, 7, 'Ghee', 1450, 2);
        INSERT INTO milk_records (id, cattle_id, date, session, litres) VALUES (40, 5, '2025-03-01', 'Morning', 4.7);
        INSERT INTO health_records (id, cattle_id, date, kind, cost) VALUES (6, 5, '2025-03-02', 'Check-up', 200);
        INSERT INTO vaccinations (id, cattle_id, date_given, vaccine, next_due) VALUES (8, 5, '2025-01-01', 'FMD', '2025-07-01');
        INSERT INTO feedings (id, cattle_id, date, feed_type, quantity_kg) VALUES (11, 5, '2025-03-01', 'Silage', 12.5);
        INSERT INTO breedings (id, cattle_id, date, method, expected_calving) VALUES (12, 5, '2024-08-27', 'AI', '2025-06-06');
    """)
    db.commit()
    db.close()
    return str(path)


def test_migrate_copies_everything_with_ids(empty_db, old_db):
    notes = []
    counts = mig.migrate(old_db, empty_db, out=notes.append)
    assert counts == {"users": (2, 2), "cattle": (2, 2), "products": (1, 1), "orders": (1, 1),
                      "order_items": (1, 1), "health_records": (1, 1), "vaccinations": (1, 1),
                      "feedings": (1, 1), "breedings": (1, 1), "milk_records": (1, 1)}
    assert any("cattle.weight_kg: 410.333 -> 410.33" in n for n in notes)
    with psycopg.connect(empty_db, prepare_threshold=None) as c:
        calf = c.execute("SELECT dam_id, dob, purchase_price FROM cattle WHERE id=2").fetchone()
        assert calf == (5, date(2025, 6, 1), Decimal("45000.00"))
        # users.created_at was UTC in SQLite, orders.created_at was farm time
        u = c.execute("SELECT created_at FROM users WHERE id=1").fetchone()[0]
        assert u == datetime(2025, 1, 5, 2, 30, tzinfo=timezone.utc)
        o = c.execute("SELECT created_at FROM orders WHERE id=9").fetchone()[0]
        assert o == datetime(2025, 3, 1, 8, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
        assert c.execute("SELECT stock FROM products WHERE id=7").fetchone()[0] == Decimal("18.50")
        # sequences continue after the copied ids
        assert c.execute("INSERT INTO users (username, password_hash, full_name, role)"
                         " VALUES ('new', 'x', 'New', 'staff') RETURNING id").fetchone()[0] == 5
        assert c.execute("INSERT INTO milk_records (cattle_id, date, session, litres)"
                         " VALUES (5, '2025-03-02', 'Evening', 4) RETURNING id").fetchone()[0] == 41
        assert c.execute("INSERT INTO breedings (cattle_id, date, method)"
                         " VALUES (5, '2025-03-02', 'AI') RETURNING id").fetchone()[0] == 13
        c.rollback()


def test_migrate_refuses_when_tables_have_rows(empty_db, old_db):
    mig.migrate(old_db, empty_db, out=lambda s: None)
    with pytest.raises(mig.MigrationError, match="already contain data"):
        mig.migrate(old_db, empty_db)
    assert _count("users") == 2  # nothing duplicated


def test_migrate_needs_alembic_first(empty_db, old_db):
    with psycopg.connect(empty_db, prepare_threshold=None, autocommit=True) as c:
        c.execute("ALTER TABLE milk_records RENAME TO milk_records_hidden")
        try:
            with pytest.raises(mig.MigrationError, match="alembic upgrade head"):
                mig.migrate(old_db, empty_db)
        finally:
            c.execute("ALTER TABLE milk_records_hidden RENAME TO milk_records")


def test_alembic_downgrade_and_upgrade(empty_db):
    def tables():
        with psycopg.connect(empty_db, prepare_threshold=None) as c:
            return {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    alembic("downgrade", "base")
    assert tables() == {"alembic_version"}
    alembic("upgrade", "head")
    assert tables() == {"alembic_version", *mig.TABLES, "tasks", "task_animals", "text_translations"}
    with psycopg.connect(empty_db.replace("-pooler", "", 1), prepare_threshold=None) as c:
        assert c.execute("SHOW timezone").fetchone()[0] == "Asia/Kolkata"  # database default, direct connection


def test_seed_demo(empty_db):
    env = {**os.environ, "DATABASE_URL": empty_db}
    run = [sys.executable, os.path.join(ROOT, "seed_demo.py")]
    r = subprocess.run(run, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert "Demo data added" in r.stdout
    assert (_count("cattle"), _count("milk_records"), _count("feedings"), _count("order_items")) == (8, 600, 144, 4)
    with psycopg.connect(empty_db, prepare_threshold=None) as c:
        names = c.execute("SELECT i.name, p.name FROM order_items i JOIN products p ON p.id=i.product_id").fetchall()
    assert all(a == b for a, b in names)  # items point at the right products
    again = subprocess.run(run, cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert again.returncode != 0 and "already has cattle" in again.stderr
    assert _count("cattle") == 8
