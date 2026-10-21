"""Scheduled tasks, linked to animals and to the record modules.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# schema.sql is a reference copy of this text; tests/test_scripts.py checks they match.
# kind is a record module (health, vaccinations, feeding, breeding, milk) or havan / other.
SCHEMA = """
CREATE TABLE tasks (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('health', 'vaccinations', 'feeding', 'breeding', 'milk', 'havan', 'other')),
    item TEXT,
    due_date DATE NOT NULL,
    repeat TEXT NOT NULL DEFAULT 'none' CHECK (repeat IN ('none', 'daily', 'weekly', 'monthly', 'yearly')),
    all_animals INTEGER NOT NULL DEFAULT 0,
    assigned_to INTEGER REFERENCES users(id) ON DELETE SET NULL,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'Open' CHECK (status IN ('Open', 'Done')),
    done_at TIMESTAMPTZ,
    done_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE task_animals (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, cattle_id)
);

CREATE INDEX idx_tasks_status_due ON tasks (status, due_date);
CREATE INDEX idx_tasks_assigned ON tasks (assigned_to);
CREATE INDEX idx_task_animals_cattle ON task_animals (cattle_id);
"""


def statements():
    return [s.strip() for s in SCHEMA.split(";") if s.strip()]


def upgrade():
    for stmt in statements():
        op.execute(stmt)


def downgrade():
    op.execute("DROP TABLE task_animals")
    op.execute("DROP TABLE tasks")
