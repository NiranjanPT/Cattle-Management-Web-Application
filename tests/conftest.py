"""Shared test setup.

The tests WIPE their database, so they only ever use DATABASE_URL_TEST (a separate Neon
branch or a local Postgres) and refuse to start if it is missing or is the main database.
"""
import os
import sys
from urllib.parse import urlsplit

import pytest
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, ".env"))


def _target(url):
    u = urlsplit(url)
    return (u.hostname or "").replace("-pooler", ""), u.port, u.path


TEST_URL = os.environ.get("DATABASE_URL_TEST")
if not TEST_URL:
    pytest.exit("DATABASE_URL_TEST is not set. The tests wipe their database, so they never use DATABASE_URL.",
                returncode=2)
if os.environ.get("DATABASE_URL") and _target(TEST_URL) == _target(os.environ["DATABASE_URL"]):
    pytest.exit("DATABASE_URL_TEST points at the same database as DATABASE_URL; refusing to run.", returncode=2)
os.environ["DATABASE_URL"] = TEST_URL  # app.py reads DATABASE_URL; set it before app is imported
os.environ["DATA_TRANSLATION"] = "off"  # no calls to the translation service; tests that need it fake it


def alembic(cmd, rev):
    from alembic import command
    from alembic.config import Config
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.attributes["url"] = TEST_URL
    cfg.attributes["skip_logging"] = True
    getattr(command, cmd)(cfg, rev)


def reset_db():
    """Wipe the test database whatever state it is in, then build it with `alembic upgrade head`."""
    import psycopg
    with psycopg.connect(TEST_URL, prepare_threshold=None, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    alembic("upgrade", "head")


@pytest.fixture(scope="session")
def app_module():
    reset_db()  # app.py checks for tables when imported
    import app
    app.app.config["TESTING"] = True
    return app


@pytest.fixture(scope="module")
def fresh_db(app_module):
    """Each test module starts from an empty, freshly migrated database with only the admin user."""
    reset_db()
    app_module.init_db()
    return app_module


class Browser:
    """A logged-in test client that adds the CSRF token to every POST."""

    def __init__(self, client):
        self.client = client

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, data=None, **kw):
        with self.client.session_transaction() as s:
            token = s.get("csrf")
        return self.client.post(url, data={**(data or {}), "csrf": token}, **kw)


def login(app_module, username, password):
    b = Browser(app_module.app.test_client())
    b.get("/login")  # sets the CSRF token in the session
    r = b.post("/login", {"username": username, "password": password})
    assert r.status_code == 302, f"login failed for {username}"
    return b
