-- Cattle management database (SQLite)

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    address TEXT,
    role TEXT NOT NULL CHECK (role IN ('admin', 'staff', 'customer')),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cattle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_no TEXT NOT NULL UNIQUE,
    name TEXT,
    species TEXT NOT NULL DEFAULT 'Cow',
    breed TEXT,
    sex TEXT NOT NULL CHECK (sex IN ('Female', 'Male')),
    dob TEXT,
    color TEXT,
    weight_kg REAL,
    source TEXT DEFAULT 'Born on farm',
    purchase_date TEXT,
    purchase_price REAL,
    dam_id INTEGER REFERENCES cattle(id) ON DELETE SET NULL,
    sire TEXT,
    status TEXT NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Sold', 'Dead')),
    photo TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS health_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    diagnosis TEXT,
    treatment TEXT,
    vet_name TEXT,
    cost REAL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS vaccinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date_given TEXT NOT NULL,
    vaccine TEXT NOT NULL,
    dose TEXT,
    next_due TEXT,
    given_by TEXT,
    cost REAL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS feedings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    feed_type TEXT NOT NULL,
    quantity_kg REAL NOT NULL,
    cost REAL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS breedings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    method TEXT NOT NULL,
    bull_or_semen TEXT,
    technician TEXT,
    pregnancy TEXT NOT NULL DEFAULT 'Pending',
    expected_calving TEXT,
    actual_calving TEXT,
    calf_tag TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS milk_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cattle_id INTEGER NOT NULL REFERENCES cattle(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    session TEXT NOT NULL,
    litres REAL NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT,
    price REAL NOT NULL,
    unit TEXT NOT NULL,
    stock REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    status TEXT NOT NULL DEFAULT 'Pending',
    total REAL NOT NULL,
    phone TEXT,
    address TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_milk_date ON milk_records(date);
CREATE INDEX IF NOT EXISTS idx_vacc_due ON vaccinations(next_due);
