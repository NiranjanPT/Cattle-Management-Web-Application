"""The expected delivery date staff give when they confirm an order; the customer sees it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# schema.sql is a reference copy of this text; tests/test_scripts.py checks they match.
SCHEMA = """
ALTER TABLE orders ADD COLUMN expected_delivery DATE;
"""


def statements():
    return [s.strip() for s in SCHEMA.split(";") if s.strip()]


def upgrade():
    for stmt in statements():
        op.execute(stmt)


def downgrade():
    op.execute("ALTER TABLE orders DROP COLUMN expected_delivery")
