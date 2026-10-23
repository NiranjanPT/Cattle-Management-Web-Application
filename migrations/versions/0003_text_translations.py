"""Remembered translations of text people type (animal names, notes, products...).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-23
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# schema.sql is a reference copy of this text; tests/test_scripts.py checks they match.
# Each distinct text is translated once (by the MyMemory service, see app.py) and read from here afterwards.
SCHEMA = """
CREATE TABLE text_translations (
    source TEXT NOT NULL,
    lang TEXT NOT NULL,
    translated TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, lang)
);
"""


def statements():
    return [s.strip() for s in SCHEMA.split(";") if s.strip()]


def upgrade():
    for stmt in statements():
        op.execute(stmt)


def downgrade():
    op.execute("DROP TABLE text_translations")
