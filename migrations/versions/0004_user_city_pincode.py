"""City and pincode for every account, so customers can be filtered and sorted by where they live.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-07
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

# schema.sql is a reference copy of this text; tests/test_scripts.py checks they match.
# The address column keeps the house, street and area; city and pincode are their own columns.
SCHEMA = """
ALTER TABLE users ADD COLUMN city TEXT;
ALTER TABLE users ADD COLUMN pincode TEXT CHECK (pincode ~ '^[1-9][0-9]{5}$');
CREATE INDEX idx_users_city ON users (lower(city));
CREATE INDEX idx_users_pincode ON users (pincode);
"""

# An address typed before this change may already hold a 6-digit pincode: copy it over (the address is kept as it is)
BACKFILL = r"""
UPDATE users SET pincode = substring(address from '(?:^|\D)([1-9]\d{5})(?:\D|$)')
 WHERE pincode IS NULL AND address ~ '(?:^|\D)[1-9]\d{5}(?:\D|$)'
"""


def statements():
    return [s.strip() for s in SCHEMA.split(";") if s.strip()]


def upgrade():
    for stmt in statements():
        op.execute(stmt)
    op.execute(BACKFILL)


def downgrade():
    op.execute("DROP INDEX idx_users_pincode")
    op.execute("DROP INDEX idx_users_city")
    op.execute("ALTER TABLE users DROP COLUMN pincode")
    op.execute("ALTER TABLE users DROP COLUMN city")
