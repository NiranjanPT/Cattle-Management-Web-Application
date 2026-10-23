"""Tests for the schema files, the Alembic migrations and the demo seed."""
import importlib.util
import os
import re
import subprocess
import sys

import psycopg
import pytest

from conftest import ROOT, TEST_URL, alembic, reset_db

# every table the migrations create, besides alembic_version
TABLES = {"users", "cattle", "products", "orders", "order_items", "health_records", "vaccinations", "feedings",
          "breedings", "milk_records", "tasks", "task_animals", "text_translations"}


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


def test_alembic_downgrade_and_upgrade(empty_db):
    def tables():
        with psycopg.connect(empty_db, prepare_threshold=None) as c:
            return {r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    alembic("downgrade", "base")
    assert tables() == {"alembic_version"}
    alembic("upgrade", "head")
    assert tables() == {"alembic_version", *TABLES}
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
