"""Alembic environment: connects with psycopg 3 using DATABASE_URL (from the environment or .env).

The URL is never written to alembic.ini or printed. Tests pass their own URL in
config.attributes["url"] so they can't touch the main database.
"""
import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging"):
    fileConfig(config.config_file_name)


def _url():
    url = config.attributes.get("url") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    # Neon gives postgresql://...; SQLAlchemy needs to be told to use psycopg 3
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def run_migrations_offline():
    """`alembic upgrade head --sql` prints the SQL without connecting."""
    context.configure(dialect_name="postgresql", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # NullPool: one short-lived connection. prepare_threshold=None for Neon's pooled endpoint.
    engine = create_engine(_url(), poolclass=pool.NullPool, connect_args={"prepare_threshold": None})
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
