"""Copy every row from the old SQLite database (cattle.db) into the PostgreSQL database.

Run once, after `alembic upgrade head` and BEFORE starting the app for the first time
(the app creates an admin account on start, and this script only fills empty tables):

    python migrate_sqlite_to_postgres.py                 # reads cattle.db, writes to DATABASE_URL
    python migrate_sqlite_to_postgres.py path/to/old.db

Rows keep their ids. Everything is copied in one transaction, so a failure leaves the
database empty. Afterwards each id sequence is moved on to max(id)+1 and the row
counts on both sides are compared.
"""
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg import sql

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Parents before children, so foreign keys are satisfied as rows arrive.
TABLES = ["users", "cattle", "products", "orders", "order_items", "health_records", "vaccinations",
          "feedings", "breedings", "milk_records"]

# SQLite stored these as text without a zone: users.created_at came from datetime('now') (UTC),
# orders.created_at from datetime('now', 'localtime') (the farm computer's clock, IST).
TIMESTAMP_ZONES = {("users", "created_at"): timezone.utc, ("orders", "created_at"): ZoneInfo("Asia/Kolkata"),
                   ("cattle", "created_at"): timezone.utc}


class MigrationError(Exception):
    pass


def _pg_columns(pg, table):
    rows = pg.execute("SELECT column_name, data_type, numeric_scale FROM information_schema.columns"
                      " WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,)).fetchall()
    return {name: (dtype, scale) for name, dtype, scale in rows}


def _convert(table, col, value, dtype, scale, rounded):
    """Turn one SQLite value into the Python type psycopg should send for this column."""
    if value is None or (value == "" and dtype != "text"):
        return None
    if dtype == "date":
        return date.fromisoformat(str(value)[:10])
    if dtype == "timestamp with time zone":
        ts = datetime.fromisoformat(str(value))
        return ts if ts.tzinfo else ts.replace(tzinfo=TIMESTAMP_ZONES.get((table, col), timezone.utc))
    if dtype == "numeric":
        exact = Decimal(str(value))
        fixed = exact.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)
        if fixed != exact:
            rounded.append(f"{table}.{col}: {value} -> {fixed}")
        return fixed
    if dtype == "integer":
        return int(value)
    return value


def migrate(sqlite_path, pg_url, out=print):
    """Copy all rows; returns {table: (sqlite_count, postgres_count)}. Raises MigrationError if refused."""
    if not os.path.exists(sqlite_path):
        raise MigrationError(f"SQLite file not found: {sqlite_path}")
    # read-only: the old file is never changed
    src = sqlite3.connect(Path(sqlite_path).resolve().as_uri() + "?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    broken = src.execute("PRAGMA foreign_key_check").fetchall()
    if broken:
        raise MigrationError("cattle.db has rows pointing at missing parents (table, rowid, parent): "
                             + ", ".join(f"({r[0]}, {r[1]}, {r[2]})" for r in broken[:10]))

    with psycopg.connect(pg_url, prepare_threshold=None, autocommit=True) as pg:
        missing = [t for t in TABLES if pg.execute("SELECT to_regclass(%s)", (f"public.{t}",)).fetchone()[0] is None]
        if missing:
            raise MigrationError(f"Tables missing in PostgreSQL ({', '.join(missing)}). Run: alembic upgrade head")
        filled = {t: n for t in TABLES
                  if (n := pg.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0])}
        if filled:
            raise MigrationError("Refusing to run: the PostgreSQL tables already contain data ("
                                 + ", ".join(f"{t}: {n}" for t, n in filled.items()) + "). The data move must go"
                                 " into an empty database, before the app is started for the first time.")

        rounded = []
        with pg.transaction():
            for table in TABLES:
                pg_cols = _pg_columns(pg, table)
                rows = src.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                src_cols = [d[0] for d in src.execute(f"SELECT * FROM {table} LIMIT 0").description]
                if set(src_cols) != set(pg_cols):
                    raise MigrationError(f"{table}: columns differ. Only in SQLite: "
                                         f"{sorted(set(src_cols) - set(pg_cols))}; only in PostgreSQL: "
                                         f"{sorted(set(pg_cols) - set(src_cols))}")
                cols = list(pg_cols)
                data = []
                for r in rows:
                    try:
                        data.append([_convert(table, c, r[c], *pg_cols[c], rounded) for c in cols])
                    except (ValueError, TypeError) as e:
                        raise MigrationError(f"{table} id {r['id']}: cannot convert a value ({e})") from None
                # A cow's mother can have a higher id than her calf: insert without dam_id, then set it.
                dams = []
                if table == "cattle":
                    i = cols.index("dam_id")
                    dams = [(row[i], row[cols.index("id")]) for row in data if row[i] is not None]
                    for row in data:
                        row[i] = None
                insert = sql.SQL("INSERT INTO {} ({}) OVERRIDING SYSTEM VALUE VALUES ({})").format(
                    sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, cols)),
                    sql.SQL(", ").join(sql.Placeholder() * len(cols)))
                with pg.cursor() as cur:
                    cur.executemany(insert, data)
                    if dams:
                        cur.executemany("UPDATE cattle SET dam_id=%s WHERE id=%s", dams)
                # next generated id = max(id) + 1 (or 1 for an empty table)
                pg.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE(MAX(id), 0) + 1, false)"
                                   " FROM {}").format(sql.Identifier(table)), (table,))

            counts = {}
            for table in TABLES:
                a = src.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                b = pg.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
                counts[table] = (a, b)
            wrong = {t: c for t, c in counts.items() if c[0] != c[1]}
            if wrong:
                raise MigrationError(f"Row counts differ after copying, nothing was saved: {wrong}")

    src.close()
    if rounded:
        out(f"Note: {len(rounded)} number(s) had more than 2 decimals and were rounded, for example:")
        for line in rounded[:10]:
            out("  " + line)
    return counts


def main(argv):
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    sqlite_path = argv[1] if len(argv) > 1 else os.path.join(BASE_DIR, "cattle.db")
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set. Copy .env.example to .env and fill it in.")
    try:
        counts = migrate(sqlite_path, url)
    except MigrationError as e:
        raise SystemExit(str(e)) from None
    print(f"{'table':<16}{'SQLite':>8}{'Postgres':>10}")
    for table, (a, b) in counts.items():
        print(f"{table:<16}{a:>8}{b:>10}")
    print("Done. All rows copied with their ids; id sequences continue from max(id)+1.")


if __name__ == "__main__":
    main(sys.argv)
