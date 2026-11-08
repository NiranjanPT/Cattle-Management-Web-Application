"""Home delivery: an order can be confirmed as "delivery unavailable", and the owner can switch delivery on or off.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# schema.sql is a reference copy of this text; tests/test_scripts.py checks they match.
# settings holds single choices the owner makes. No home_delivery row means off, which is the farm's position today.
SCHEMA = """
ALTER TABLE orders ADD COLUMN delivery_unavailable INTEGER NOT NULL DEFAULT 0;
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def statements():
    return [s.strip() for s in SCHEMA.split(";") if s.strip()]


def upgrade():
    for stmt in statements():
        op.execute(stmt)


def downgrade():
    op.execute("DROP TABLE settings")
    op.execute("ALTER TABLE orders DROP COLUMN delivery_unavailable")
