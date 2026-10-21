-- Cattle management database (PostgreSQL)
--
-- Reference copy only. The database is built by Alembic: `alembic upgrade head`
-- runs migrations/versions/0001_initial_schema.py, which holds these same statements
-- (tests/test_scripts.py checks the two stay identical). Later schema changes go in
-- new Alembic migrations; update this file to match.
--
-- Tables from migration 0002 (tasks) follow the indexes.
-- Migration 0001 also sets the database's default time zone to Asia/Kolkata.

CREATE TABLE users (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    address TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'staff', 'customer')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE cattle (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tag_no TEXT NOT NULL UNIQUE,
    name TEXT,
    species TEXT NOT NULL DEFAULT 'Cow',
    breed TEXT,
    sex TEXT NOT NULL CHECK (sex IN ('Female', 'Male')),
    dob DATE,
    color TEXT,
    weight_kg NUMERIC(10,2),
    source TEXT DEFAULT 'Born on farm',
    purchase_date DATE,
    purchase_price NUMERIC(12,2),
    dam_id INTEGER REFERENCES cattle(id) ON DELETE SET NULL,
    sire TEXT,
    status TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Sold', 'Dead')),
    photo TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE health_records (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    kind TEXT NOT NULL,
    diagnosis TEXT,
    treatment TEXT,
    vet_name TEXT,
    cost NUMERIC(12,2) DEFAULT 0,
    notes TEXT
);

CREATE TABLE vaccinations (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date_given DATE NOT NULL,
    vaccine TEXT NOT NULL,
    dose TEXT,
    next_due DATE,
    given_by TEXT,
    cost NUMERIC(12,2) DEFAULT 0,
    notes TEXT
);

CREATE TABLE feedings (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    feed_type TEXT NOT NULL,
    quantity_kg NUMERIC(10,2) NOT NULL,
    cost NUMERIC(12,2) DEFAULT 0,
    notes TEXT
);

CREATE TABLE breedings (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    method TEXT NOT NULL,
    bull_or_semen TEXT,
    technician TEXT,
    pregnancy TEXT NOT NULL DEFAULT 'Pending',
    expected_calving DATE,
    actual_calving DATE,
    calf_tag TEXT,
    notes TEXT
);

CREATE TABLE milk_records (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    session TEXT NOT NULL,
    litres NUMERIC(10,2) NOT NULL,
    notes TEXT
);

CREATE TABLE products (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    price NUMERIC(12,2) NOT NULL,
    unit TEXT NOT NULL,
    stock NUMERIC(10,2) NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE orders (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'Pending',
    total NUMERIC(12,2) NOT NULL,
    phone TEXT,
    address TEXT,
    notes TEXT
);

CREATE TABLE order_items (
    id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    price NUMERIC(12,2) NOT NULL,
    qty NUMERIC(10,2) NOT NULL
);

CREATE INDEX idx_health_cattle_date ON health_records (cattle_id, date);
CREATE INDEX idx_vacc_cattle_date ON vaccinations (cattle_id, date_given);
CREATE INDEX idx_feed_cattle_date ON feedings (cattle_id, date);
CREATE INDEX idx_breed_cattle_date ON breedings (cattle_id, date);
CREATE INDEX idx_milk_cattle_date ON milk_records (cattle_id, date);
CREATE INDEX idx_milk_date ON milk_records (date);
CREATE INDEX idx_vacc_due ON vaccinations (next_due);
CREATE INDEX idx_orders_status ON orders (status);
CREATE INDEX idx_orders_user ON orders (user_id);
CREATE INDEX idx_order_items_order ON order_items (order_id);
CREATE INDEX idx_cattle_dam ON cattle (dam_id);

-- Migration 0002: scheduled tasks

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
