"""Gaushala: a cattle management web application (Flask + PostgreSQL)."""
import atexit
import csv
import io
import os
import re
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import panchang

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
TIMEZONE = "Asia/Kolkata"  # order times and "this month" are counted in farm time

load_dotenv(os.path.join(BASE_DIR, ".env"))  # real environment variables win over .env


def _env(name):
    if not os.environ.get(name):
        raise SystemExit(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return os.environ[name]


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # photo uploads
app.secret_key = _env("SECRET_KEY")

# ---------------------------------------------------------------- database


def _configure(conn):
    # set_config, not SET: SET cannot take a bound parameter
    conn.execute("SELECT set_config('TimeZone', %s, false)", (TIMEZONE,))


# Autocommit: each q()/ex() stands alone; group statements with `with get_db().transaction():`.
# prepare_threshold=None because Neon's pooled endpoint (PgBouncer) can't keep prepared statements.
# check_connection replaces connections that died while Neon's compute was asleep.
pool = ConnectionPool(_env("DATABASE_URL"), min_size=1, max_size=int(os.environ.get("DB_POOL_SIZE", 5)),
                      kwargs={"row_factory": dict_row, "autocommit": True, "prepare_threshold": None},
                      configure=_configure, check=ConnectionPool.check_connection, open=True)
atexit.register(pool.close)


def get_db():
    if "db" not in g:
        g.db = pool.getconn()
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        pool.putconn(db)


def q(sql, args=(), one=False):
    db = get_db()
    in_transaction = db.info.transaction_status != psycopg.pq.TransactionStatus.IDLE
    try:
        rows = db.execute(sql, args).fetchall()
    except psycopg.OperationalError:
        # Neon occasionally drops a connection mid-query. A read outside a transaction is
        # safe to repeat once on a fresh connection; writes (ex) are never retried.
        if in_transaction:
            raise
        pool.putconn(g.pop("db"))  # the pool discards it if it is no longer usable
        rows = get_db().execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def ex(sql, args=()):
    """Run an INSERT, UPDATE or DELETE. An INSERT returns the new row's id."""
    insert = sql.lstrip().upper().startswith("INSERT")
    if insert and "RETURNING" not in sql.upper():
        sql += " RETURNING id"
    cur = get_db().execute(sql, args)
    return cur.fetchone()["id"] if insert else None


def init_db():
    """Create a first admin account if there are no users. Tables come from `alembic upgrade head`."""
    with app.app_context():
        if q("SELECT to_regclass('public.users') AS t", one=True)["t"] is None:
            raise SystemExit("The database has no tables yet. Run: alembic upgrade head")
        if not q("SELECT 1 FROM users LIMIT 1"):
            ex("INSERT INTO users (username, password_hash, full_name, role) VALUES (%s,%s,%s,%s)",
               ("admin", generate_password_hash("admin123"), "Farm Administrator", "admin"))
            print("Created admin account: username 'admin', password 'admin123'. Change it after logging in.")


# ---------------------------------------------------------------- auth & helpers

def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    if "user" not in g:
        g.user = q("SELECT * FROM users WHERE id=%s AND active=1", (uid,), one=True)
    return g.user


def login_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            user = current_user()
            if user is None:
                return redirect(url_for("login", next=request.path))
            if roles and user["role"] not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


FARM = ("admin", "staff")


@app.before_request
def csrf_protect():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
        abort(400, "Form expired, please go back and try again.")


@app.context_processor
def inject():
    cart = session.get("cart", {})
    return {"user": current_user(), "csrf_token": session.get("csrf", ""),
            "cart_count": sum(cart.values()) if cart else 0, "today": date.today()}


# NUMERIC columns arrive as Decimal; Decimal(str(v)) also takes ints and floats from Python code
@app.template_filter("money")
def money(v):
    return f"₹{Decimal(str(v or 0)):,.2f}"


@app.template_filter("num")
def num(v):
    if v is None:
        return ""
    return f"{Decimal(str(v)):,.1f}".rstrip("0").rstrip(".")


@app.template_filter("dt")
def dt(v, fmt="%Y-%m-%d %H:%M:%S"):
    """A TIMESTAMPTZ shown in farm time."""
    return v.astimezone(ZoneInfo(TIMEZONE)).strftime(fmt) if v else ""


def age_text(dob):
    if not dob:
        return ""
    d = dob if isinstance(dob, date) else date.fromisoformat(dob)
    months = (date.today().year - d.year) * 12 + date.today().month - d.month
    if date.today().day < d.day:
        months -= 1
    y, m = divmod(max(months, 0), 12)
    return f"{y} y {m} m" if y else f"{m} m"


app.jinja_env.globals["age_text"] = age_text


def parse_date(s, default=None):
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        return default


# ---------------------------------------------------------------- login, register, profile

@app.route("/")
def index():
    user = current_user()
    if user is None:
        return redirect(url_for("login"))
    return redirect(url_for("shop" if user["role"] == "customer" else "dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = q("SELECT * FROM users WHERE username=%s", (request.form["username"].strip(),), one=True)
        if user and user["active"] and check_password_hash(user["password_hash"], request.form["password"]):
            session["user_id"] = user["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
        flash("Wrong username or password, or the account is disabled.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("cart", None)
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    """Self sign-up creates a customer account for the shop."""
    if request.method == "POST":
        f = request.form
        if len(f["password"]) < 6:
            flash("Password must be at least 6 characters.", "error")
        elif q("SELECT 1 FROM users WHERE username=%s", (f["username"].strip(),)):
            flash("That username is taken.", "error")
        else:
            uid = ex("INSERT INTO users (username, password_hash, full_name, phone, email, address, role)"
                     " VALUES (%s,%s,%s,%s,%s,%s, 'customer')",
                     (f["username"].strip(), generate_password_hash(f["password"]), f["full_name"].strip(),
                      f.get("phone"), f.get("email"), f.get("address")))
            session["user_id"] = uid
            flash("Welcome! Your account is ready.", "ok")
            return redirect(url_for("shop"))
    return render_template("register.html")


@app.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    user = current_user()
    if request.method == "POST":
        f = request.form
        ex("UPDATE users SET full_name=%s, phone=%s, email=%s, address=%s WHERE id=%s",
           (f["full_name"], f.get("phone"), f.get("email"), f.get("address"), user["id"]))
        if f.get("new_password"):
            if not check_password_hash(user["password_hash"], f.get("old_password", "")):
                flash("Current password is wrong; password not changed.", "error")
                return redirect(url_for("profile"))
            if len(f["new_password"]) < 6:
                flash("New password must be at least 6 characters.", "error")
                return redirect(url_for("profile"))
            ex("UPDATE users SET password_hash=%s WHERE id=%s",
               (generate_password_hash(f["new_password"]), user["id"]))
        flash("Profile saved.", "ok")
        return redirect(url_for("profile"))
    return render_template("profile.html")


# ---------------------------------------------------------------- admin: users

@app.route("/admin/users")
@login_required("admin")
def users():
    role = request.args.get("role", "")
    sql = "SELECT * FROM users" + (" WHERE role=%s" if role else "") + " ORDER BY role, full_name"
    return render_template("users.html", users=q(sql, (role,) if role else ()), role=role)


@app.route("/admin/users/new", methods=["GET", "POST"])
@app.route("/admin/users/<int:uid>/edit", methods=["GET", "POST"])
@login_required("admin")
def user_form(uid=None):
    u = q("SELECT * FROM users WHERE id=%s", (uid,), one=True) if uid else None
    if uid and not u:
        abort(404)
    if request.method == "POST":
        f = request.form
        role = f["role"] if f["role"] in ("admin", "staff", "customer") else "staff"
        active = 1 if f.get("active") else 0
        if u and u["id"] == current_user()["id"] and (role != "admin" or not active):
            flash("You cannot remove your own admin access.", "error")
            return redirect(request.url)
        clash = q("SELECT id FROM users WHERE username=%s AND id<>%s", (f["username"].strip(), uid or 0))
        if clash:
            flash("That username is taken.", "error")
        elif not u and len(f.get("password", "")) < 6:
            flash("Password must be at least 6 characters.", "error")
        else:
            vals = (f["username"].strip(), f["full_name"], f.get("phone"), f.get("email"),
                    f.get("address"), role, active)
            if u:
                ex("UPDATE users SET username=%s, full_name=%s, phone=%s, email=%s, address=%s, role=%s, active=%s"
                   " WHERE id=%s", vals + (uid,))
                if f.get("password"):
                    ex("UPDATE users SET password_hash=%s WHERE id=%s",
                       (generate_password_hash(f["password"]), uid))
            else:
                ex("INSERT INTO users (username, full_name, phone, email, address, role, active, password_hash)"
                   " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", vals + (generate_password_hash(f["password"]),))
            flash("User saved.", "ok")
            return redirect(url_for("users"))
    return render_template("user_form.html", u=u)


# ---------------------------------------------------------------- cattle

CATTLE_FIELDS = ["tag_no", "name", "species", "breed", "sex", "dob", "color", "weight_kg", "source",
                 "purchase_date", "purchase_price", "dam_id", "sire", "status", "notes"]
SPECIES = ["Cow", "Buffalo", "Bull", "Ox", "Calf", "Heifer"]
BREEDS = ["Gir", "Sahiwal", "Red Sindhi", "Tharparkar", "Khillar", "Deoni", "Kankrej", "Ongole",
          "Hallikar", "HF Cross", "Jersey Cross", "Murrah", "Mehsana", "Jaffarabadi", "Pandharpuri", "Other"]


@app.route("/cattle")
@login_required(*FARM)
def cattle_list():
    status = request.args.get("status", "Active")
    s = request.args.get("q", "").strip()
    sql = "SELECT * FROM cattle WHERE 1=1"
    args = []
    if status:
        sql += " AND status=%s"
        args.append(status)
    if s:
        sql += " AND (tag_no ILIKE %s OR name ILIKE %s OR breed ILIKE %s)"
        args += [f"%{s}%"] * 3
    rows = q(sql + " ORDER BY tag_no", args)
    return render_template("cattle_list.html", rows=rows, status=status, s=s)


def _save_photo(file):
    if not file or not file.filename:
        return None
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        flash("Photo must be a JPG, PNG, WEBP or GIF image.", "error")
        return None
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = secrets.token_hex(8) + "_" + secure_filename(file.filename)
    file.save(os.path.join(UPLOAD_DIR, name))
    return name


@app.route("/cattle/new", methods=["GET", "POST"])
@app.route("/cattle/<int:cid>/edit", methods=["GET", "POST"])
@login_required(*FARM)
def cattle_form(cid=None):
    c = q("SELECT * FROM cattle WHERE id=%s", (cid,), one=True) if cid else None
    if cid and not c:
        abort(404)
    if request.method == "POST":
        f = request.form
        vals = {k: (f.get(k) or None) for k in CATTLE_FIELDS}
        vals["tag_no"] = (vals["tag_no"] or "").strip()
        if not vals["tag_no"]:
            flash("Tag number is required.", "error")
        elif q("SELECT 1 FROM cattle WHERE tag_no=%s AND id<>%s", (vals["tag_no"], cid or 0)):
            flash("Another animal already has that tag number.", "error")
        else:
            photo = _save_photo(request.files.get("photo"))
            if photo:
                vals["photo"] = photo
            cols = list(vals)
            if c:
                ex(f"UPDATE cattle SET {', '.join(k + '=%s' for k in cols)} WHERE id=%s",
                   [vals[k] for k in cols] + [cid])
            else:
                cid = ex(f"INSERT INTO cattle ({', '.join(cols)}) VALUES ({', '.join('%s' for _ in cols)})",
                         [vals[k] for k in cols])
            flash("Animal saved.", "ok")
            return redirect(url_for("cattle_profile", cid=cid))
    dams = q("SELECT id, tag_no, name FROM cattle WHERE sex='Female' AND id<>%s ORDER BY tag_no", (cid or 0,))
    return render_template("cattle_form.html", c=c, dams=dams, species=SPECIES, breeds=BREEDS)


@app.route("/cattle/<int:cid>")
@login_required(*FARM)
def cattle_profile(cid):
    c = q("SELECT c.*, d.tag_no AS dam_tag, d.name AS dam_name FROM cattle c"
          " LEFT JOIN cattle d ON d.id=c.dam_id WHERE c.id=%s", (cid,), one=True)
    if not c:
        abort(404)
    data = {k: q(f"SELECT * FROM {cfg['table']} WHERE cattle_id=%s ORDER BY {cfg['date']} DESC LIMIT 10", (cid,))
            for k, cfg in RECORDS.items()}
    calves = q("SELECT id, tag_no, name, dob FROM cattle WHERE dam_id=%s ORDER BY dob", (cid,))
    milk30 = q("SELECT COALESCE(SUM(litres),0) t, COUNT(DISTINCT date) d FROM milk_records"
               " WHERE cattle_id=%s AND date>=%s", (cid, (date.today() - timedelta(days=30)).isoformat()), one=True)
    return render_template("cattle_profile.html", c=c, data=data, calves=calves, milk30=milk30,
                           records=RECORDS)


@app.route("/cattle/<int:cid>/delete", methods=["POST"])
@login_required("admin")
def cattle_delete(cid):
    ex("DELETE FROM cattle WHERE id=%s", (cid,))
    flash("Animal and all its records deleted.", "ok")
    back = request.form.get("back", "")  # the register sends its own URL, so search and filter are kept
    return redirect(back if back.startswith("/cattle") and not back.startswith("//") else url_for("cattle_list"))


# ---------------------------------------------------------------- health, vaccination, feeding, breeding, milk

# field: (name, label, type, options). type is date | text | number | select | textarea
RECORDS = {
    "health": {
        "table": "health_records", "title": "Health records", "one": "health record", "date": "date",
        "fields": [("date", "Date", "date", None),
                   ("kind", "Type", "select", ["Check-up", "Illness", "Treatment", "Deworming", "Injury", "Surgery"]),
                   ("diagnosis", "Diagnosis / symptoms", "text", None),
                   ("treatment", "Treatment / medicine", "text", None),
                   ("vet_name", "Vet name", "text", None),
                   ("cost", "Cost (₹)", "number", None),
                   ("notes", "Notes", "textarea", None)],
        "required": ["date", "kind"], "list": ["date", "kind", "diagnosis", "treatment", "cost"],
    },
    "vaccinations": {
        "table": "vaccinations", "title": "Vaccinations", "one": "vaccination", "date": "date_given",
        "fields": [("date_given", "Date given", "date", None),
                   ("vaccine", "Vaccine", "select", ["FMD (Foot and Mouth)", "HS (Haemorrhagic Septicaemia)",
                                                     "BQ (Black Quarter)", "Brucellosis", "Theileriosis",
                                                     "Lumpy Skin Disease", "Anthrax", "Rabies", "Other"]),
                   ("dose", "Dose", "text", None),
                   ("next_due", "Next due", "date", None),
                   ("given_by", "Given by", "text", None),
                   ("cost", "Cost (₹)", "number", None),
                   ("notes", "Notes", "textarea", None)],
        "required": ["date_given", "vaccine"], "list": ["date_given", "vaccine", "dose", "next_due"],
    },
    "feeding": {
        "table": "feedings", "title": "Feeding records", "one": "feeding record", "date": "date",
        "fields": [("date", "Date", "date", None),
                   ("feed_type", "Feed", "select", ["Green fodder", "Dry fodder", "Silage", "Concentrate",
                                                    "Cattle feed pellets", "Oil cake", "Mineral mixture", "Other"]),
                   ("quantity_kg", "Quantity (kg)", "number", None),
                   ("cost", "Cost (₹)", "number", None),
                   ("notes", "Notes", "textarea", None)],
        "required": ["date", "feed_type", "quantity_kg"], "list": ["date", "feed_type", "quantity_kg", "cost"],
    },
    "breeding": {
        "table": "breedings", "title": "Breeding records", "one": "breeding record", "date": "date",
        "fields": [("date", "Service date", "date", None),
                   ("method", "Method", "select", ["Artificial insemination", "Natural service"]),
                   ("bull_or_semen", "Bull / semen ID", "text", None),
                   ("technician", "Technician", "text", None),
                   ("pregnancy", "Pregnancy", "select", ["Pending", "Confirmed", "Not pregnant"]),
                   ("expected_calving", "Expected calving (blank = auto)", "date", None),
                   ("actual_calving", "Actual calving", "date", None),
                   ("calf_tag", "Calf tag no.", "text", None),
                   ("notes", "Notes", "textarea", None)],
        "required": ["date", "method"], "list": ["date", "method", "pregnancy", "expected_calving", "actual_calving"],
    },
    "milk": {
        "table": "milk_records", "title": "Milk production", "one": "milk record", "date": "date",
        "fields": [("date", "Date", "date", None),
                   ("session", "Session", "select", ["Morning", "Evening"]),
                   ("litres", "Litres", "number", None),
                   ("notes", "Notes", "textarea", None)],
        "required": ["date", "session", "litres"], "list": ["date", "session", "litres"],
    },
}
GESTATION_DAYS = {"Buffalo": 310}  # others: 283


@app.route("/records/<kind>")
@login_required(*FARM)
def records(kind):
    cfg = RECORDS.get(kind) or abort(404)
    cid = request.args.get("cattle_id", type=int)
    # a malformed date in the URL is ignored rather than sent to a DATE column
    d_from, d_to = parse_date(request.args.get("from")), parse_date(request.args.get("to"))
    sql = (f"SELECT r.*, c.tag_no, c.name AS cname FROM {cfg['table']} r JOIN cattle c ON c.id=r.cattle_id"
           " WHERE 1=1")
    args = []
    if cid:
        sql += " AND r.cattle_id=%s"
        args.append(cid)
    if d_from:
        sql += f" AND r.{cfg['date']}>=%s"
        args.append(d_from)
    if d_to:
        sql += f" AND r.{cfg['date']}<=%s"
        args.append(d_to)
    rows = q(sql + f" ORDER BY r.{cfg['date']} DESC, r.id DESC LIMIT 500", args)
    animals = q("SELECT id, tag_no, name FROM cattle ORDER BY tag_no")
    labels = {f[0]: f[1] for f in cfg["fields"]}
    return render_template("records.html", kind=kind, cfg=cfg, rows=rows, animals=animals, cid=cid,
                           d_from=d_from or "", d_to=d_to or "", labels=labels)


@app.route("/records/<kind>/new", methods=["GET", "POST"])
@app.route("/records/<kind>/<int:rid>/edit", methods=["GET", "POST"])
@login_required(*FARM)
def record_form(kind, rid=None):
    cfg = RECORDS.get(kind) or abort(404)
    r = q(f"SELECT * FROM {cfg['table']} WHERE id=%s", (rid,), one=True) if rid else None
    if rid and not r:
        abort(404)
    if request.method == "POST":
        f = request.form
        cattle_ids = f.getlist("cattle_id") if not r else [f.get("cattle_id")]
        cattle_ids = [int(x) for x in cattle_ids if x]
        vals = {name: (f.get(name) or None) for name, *_ in cfg["fields"]}
        missing = [n for n in cfg["required"] if not vals[n]]
        if not cattle_ids or missing:
            flash("Please choose an animal and fill the required fields.", "error")
        else:
            for cid in cattle_ids:
                row = dict(vals)
                if kind == "breeding" and not row["expected_calving"]:
                    species = q("SELECT species FROM cattle WHERE id=%s", (cid,), one=True)
                    days = GESTATION_DAYS.get(species["species"] if species else "", 283)
                    row["expected_calving"] = (date.fromisoformat(row["date"]) + timedelta(days=days)).isoformat()
                row["cattle_id"] = cid
                cols = list(row)
                if r:
                    ex(f"UPDATE {cfg['table']} SET {', '.join(k + '=%s' for k in cols)} WHERE id=%s",
                       [row[k] for k in cols] + [rid])
                else:
                    ex(f"INSERT INTO {cfg['table']} ({', '.join(cols)}) VALUES ({', '.join('%s' for _ in cols)})",
                       [row[k] for k in cols])
            flash(f"Saved {len(cattle_ids)} {cfg['one']}{'s' if len(cattle_ids) > 1 else ''}.", "ok")
            back = f.get("back")
            return redirect(back if back and back.startswith("/") and not back.startswith("//")
                            else url_for("records", kind=kind))
    animals = q("SELECT id, tag_no, name, sex FROM cattle WHERE status='Active' OR id=%s ORDER BY tag_no",
                (r["cattle_id"] if r else 0,))
    pre = request.args.get("cattle_id", type=int)
    return render_template("record_form.html", kind=kind, cfg=cfg, r=r, animals=animals, pre=pre,
                           back=request.args.get("back", ""))


@app.route("/records/<kind>/<int:rid>/delete", methods=["POST"])
@login_required(*FARM)
def record_delete(kind, rid):
    cfg = RECORDS.get(kind) or abort(404)
    ex(f"DELETE FROM {cfg['table']} WHERE id=%s", (rid,))
    flash("Record deleted.", "ok")
    return redirect(request.referrer or url_for("records", kind=kind))


# ---------------------------------------------------------------- shop

PRODUCT_CATEGORIES = ["Milk", "Ghee", "Curd & Buttermilk", "Paneer", "Butter", "Sweets", "Gomutra & Ayurvedic",
                      "Organic manure", "Other"]


@app.route("/shop")
@login_required()
def shop():
    cat = request.args.get("category", "")
    sql = "SELECT * FROM products WHERE active=1" + (" AND category=%s" if cat else "") + " ORDER BY category, name"
    products = q(sql, (cat,) if cat else ())
    cats = [r["category"] for r in q("SELECT DISTINCT category FROM products WHERE active=1 ORDER BY category")]
    return render_template("shop.html", products=products, cats=cats, cat=cat)


def _cart_lines():
    cart = session.get("cart", {})
    lines, total = [], Decimal(0)
    for pid, qty in cart.items():
        p = q("SELECT * FROM products WHERE id=%s AND active=1", (int(pid),), one=True)
        if p:
            lines.append({"p": p, "qty": qty, "sub": p["price"] * qty})
            total += p["price"] * qty
    return lines, total


@app.route("/cart/add/<int:pid>", methods=["POST"])
@login_required()
def cart_add(pid):
    p = q("SELECT * FROM products WHERE id=%s AND active=1", (pid,), one=True) or abort(404)
    qty = max(request.form.get("qty", 1, type=int), 1)
    cart = session.get("cart", {})
    cart[str(pid)] = cart.get(str(pid), 0) + qty
    session["cart"] = cart
    flash(f"Added {p['name']} to your cart.", "ok")
    return redirect(request.referrer or url_for("shop"))


@app.route("/cart/remove/<int:pid>", methods=["POST"])
@login_required()
def cart_remove(pid):
    cart = session.get("cart", {})
    if cart.pop(str(pid), None) is not None:
        session["cart"] = cart
        p = q("SELECT name FROM products WHERE id=%s", (pid,), one=True)
        flash(f"Removed {p['name'] if p else 'the item'} from your cart.", "ok")
    return redirect(url_for("cart"))


def _valid_phone(phone):
    """At least 10 digits; spaces, +, -, ( and ) allowed (e.g. +91 98220 00000)."""
    return bool(re.fullmatch(r"[0-9 +()-]+", phone)) and 10 <= len(re.sub(r"\D", "", phone)) <= 13


@app.route("/cart", methods=["GET", "POST"])
@login_required()
def cart():
    if request.method == "POST":
        new = {}
        for pid in session.get("cart", {}):
            qty = request.form.get(f"qty_{pid}", 0, type=int)
            if qty > 0:
                new[pid] = qty
        session["cart"] = new
        if request.form.get("action") != "checkout":
            return redirect(url_for("cart"))
        lines, total = _cart_lines()
        if not lines:
            flash("Your cart is empty.", "error")
            return redirect(url_for("shop"))
        f = request.form
        phone, address = f.get("phone", "").strip(), f.get("address", "").strip()
        if not _valid_phone(phone) or not address:
            flash("Please enter a phone number (at least 10 digits) and a delivery address.", "error")
            return render_template("cart.html", lines=lines, total=total, form=f)
        with get_db().transaction():
            # lock the products, then check stock, so two customers can't both buy the last one
            stock = {r["id"]: r["stock"] for r in q("SELECT id, stock FROM products WHERE id = ANY(%s) FOR UPDATE",
                                                   ([l["p"]["id"] for l in lines],))}
            short = [l["p"]["name"] for l in lines if l["qty"] > stock[l["p"]["id"]]]
            if not short:
                oid = ex("INSERT INTO orders (user_id, total, phone, address, notes) VALUES (%s,%s,%s,%s,%s)",
                         (current_user()["id"], total, phone, address, f.get("notes")))
                for l in lines:
                    ex("INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (%s,%s,%s,%s,%s)",
                       (oid, l["p"]["id"], l["p"]["name"], l["p"]["price"], l["qty"]))
                    ex("UPDATE products SET stock=stock-%s WHERE id=%s", (l["qty"], l["p"]["id"]))
        if short:
            flash("Not enough stock for: " + ", ".join(short), "error")
            return redirect(url_for("cart"))
        session["cart"] = {}
        flash(f"Order #{oid} placed. We will confirm it soon.", "ok")
        return redirect(url_for("order_detail", oid=oid))
    lines, total = _cart_lines()
    return render_template("cart.html", lines=lines, total=total)


@app.route("/orders")
@login_required()
def my_orders():
    rows = q("SELECT * FROM orders WHERE user_id=%s ORDER BY id DESC", (current_user()["id"],))
    return render_template("orders.html", rows=rows, manage=False, status="")


@app.route("/orders/<int:oid>")
@login_required()
def order_detail(oid):
    o = q("SELECT o.*, u.full_name, u.username FROM orders o JOIN users u ON u.id=o.user_id WHERE o.id=%s",
          (oid,), one=True) or abort(404)
    if current_user()["role"] == "customer" and o["user_id"] != current_user()["id"]:
        abort(403)
    items = q("SELECT * FROM order_items WHERE order_id=%s", (oid,))
    return render_template("order_detail.html", o=o, items=items, statuses=ORDER_STATUSES)


ORDER_STATUSES = ["Pending", "Confirmed", "Delivered", "Cancelled"]


@app.route("/admin/orders")
@login_required(*FARM)
def manage_orders():
    status = request.args.get("status", "")
    sql = ("SELECT o.*, u.full_name FROM orders o JOIN users u ON u.id=o.user_id"
           + (" WHERE o.status=%s" if status else "") + " ORDER BY o.id DESC")
    return render_template("orders.html", rows=q(sql, (status,) if status else ()), manage=True,
                           status=status, statuses=ORDER_STATUSES)


@app.route("/admin/orders/<int:oid>/status", methods=["POST"])
@login_required(*FARM)
def order_status(oid):
    new = request.form["status"]
    if new not in ORDER_STATUSES:
        abort(400)
    with get_db().transaction():
        # the row lock stops two clicks on "Cancelled" from returning the stock twice
        o = q("SELECT * FROM orders WHERE id=%s FOR UPDATE", (oid,), one=True) or abort(404)
        # return stock when an order is cancelled, take it again if un-cancelled
        if new == "Cancelled" and o["status"] != "Cancelled":
            ex("UPDATE products SET stock=stock+(SELECT qty FROM order_items i WHERE i.order_id=%s AND"
               " i.product_id=products.id) WHERE id IN (SELECT product_id FROM order_items WHERE order_id=%s)",
               (oid, oid))
        elif o["status"] == "Cancelled" and new != "Cancelled":
            ex("UPDATE products SET stock=stock-(SELECT qty FROM order_items i WHERE i.order_id=%s AND"
               " i.product_id=products.id) WHERE id IN (SELECT product_id FROM order_items WHERE order_id=%s)",
               (oid, oid))
        ex("UPDATE orders SET status=%s WHERE id=%s", (new, oid))
    flash(f"Order #{oid} marked {new}.", "ok")
    return redirect(request.referrer or url_for("manage_orders"))


@app.route("/admin/products")
@login_required(*FARM)
def products():
    return render_template("products.html", rows=q("SELECT * FROM products ORDER BY active DESC, category, name"))


@app.route("/admin/products/new", methods=["GET", "POST"])
@app.route("/admin/products/<int:pid>/edit", methods=["GET", "POST"])
@login_required(*FARM)
def product_form(pid=None):
    p = q("SELECT * FROM products WHERE id=%s", (pid,), one=True) if pid else None
    if pid and not p:
        abort(404)
    if request.method == "POST":
        f = request.form
        try:
            vals = (f["name"].strip(), f["category"], f.get("description"), float(f["price"]), f["unit"],
                    float(f.get("stock") or 0), 1 if f.get("active") else 0)
        except ValueError:
            flash("Price and stock must be numbers.", "error")
            return render_template("product_form.html", p=p, cats=PRODUCT_CATEGORIES)
        if p:
            ex("UPDATE products SET name=%s, category=%s, description=%s, price=%s, unit=%s, stock=%s, active=%s WHERE id=%s",
               vals + (pid,))
        else:
            ex("INSERT INTO products (name, category, description, price, unit, stock, active) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               vals)
        flash("Product saved.", "ok")
        return redirect(url_for("products"))
    return render_template("product_form.html", p=p, cats=PRODUCT_CATEGORIES)


# ---------------------------------------------------------------- Hindu calendar

@app.route("/calendar")
@login_required()
def calendar():
    today = date.today()
    year = request.args.get("year", type=int) or panchang.year_for(today)
    year = min(max(year, 1900), 2100)
    months = panchang.lunar_months(year)
    sel = request.args.get("m", type=int)
    if sel is None:
        cur = panchang.month_of(today)
        sel = next((i for i, m in enumerate(months) if cur and m["start_dt"] == cur["start_dt"]), 0)
    sel = min(max(sel, 0), len(months) - 1)
    month = months[sel]
    days = [panchang.day_info(d) for d in panchang.month_days(month)]
    events = {}
    if current_user()["role"] in FARM and days:
        lo, hi = days[0]["date"].isoformat(), days[-1]["date"].isoformat()
        for r in q("SELECT v.next_due d, c.tag_no, v.vaccine FROM vaccinations v JOIN cattle c ON c.id=v.cattle_id"
                   " WHERE v.next_due BETWEEN %s AND %s", (lo, hi)):
            events.setdefault(r["d"].isoformat(), []).append(f"Vaccine due: {r['tag_no']} ({r['vaccine']})")
        for r in q("SELECT b.expected_calving d, c.tag_no FROM breedings b JOIN cattle c ON c.id=b.cattle_id"
                   " WHERE b.actual_calving IS NULL AND b.pregnancy<>'Not pregnant'"
                   " AND b.expected_calving BETWEEN %s AND %s", (lo, hi)):
            events.setdefault(r["d"].isoformat(), []).append(f"Calving expected: {r['tag_no']}")
    # Shaka and (Chaitradi) Vikram Samvat years both begin at Chaitra
    overview = []
    for m in months:
        ds = panchang.month_days(m)
        info = [panchang.day_info(d) for d in ds]
        # a tithi can be skipped (kshaya) when it starts and ends between two sunrises
        purnima = next((i["date"] for i in info if i["tithi"] == "Purnima"),
                       max((i["date"] for i in info if i["paksha"] == "Shukla"), default=None))
        first = ds[0] if info[0]["tithi"] == "Pratipada" else ds[0] - timedelta(days=1)
        overview.append({"first": first, "last": ds[-1], "purnima": purnima})
    return render_template("calendar.html", months=months, overview=overview, sel=sel, month=month, days=days, year=year,
                           shaka=year - 78, vikram=year + 57, events=events, nakshatras=panchang.NAKSHATRAS,
                           lead_blanks=days[0]["date"].weekday() if days else 0)


@app.route("/calendar/nakshatras")
@login_required()
def nakshatras():
    rows = []
    for i, n in enumerate(panchang.NAKSHATRAS):
        start = i * panchang.NAK_SPAN
        rows.append({"no": i + 1, "name": n[0], "dev": n[1], "lord": n[2], "deity": n[3], "symbol": n[4],
                     "rashi": panchang.RASHIS[int(start // 30)],
                     "from": f"{int(start)}°{round((start % 1) * 60):02d}′",
                     "to": f"{int(start + panchang.NAK_SPAN)}°{round(((start + panchang.NAK_SPAN) % 1) * 60):02d}′"})
    return render_template("nakshatras.html", rows=rows, today=panchang.day_info(date.today()))


# ---------------------------------------------------------------- dashboard & reports

@app.route("/dashboard")
@login_required(*FARM)
def dashboard():
    t = date.today()
    ti = t.isoformat()
    soon = (t + timedelta(days=14)).isoformat()
    month_start = t.replace(day=1).isoformat()
    stats = {
        "active": q("SELECT COUNT(*) n FROM cattle WHERE status='Active'", one=True)["n"],
        "female": q("SELECT COUNT(*) n FROM cattle WHERE status='Active' AND sex='Female'", one=True)["n"],
        "milk_today": q("SELECT COALESCE(SUM(litres),0) n FROM milk_records WHERE date=%s", (ti,), one=True)["n"],
        "milk_month": q("SELECT COALESCE(SUM(litres),0) n FROM milk_records WHERE date>=%s", (month_start,),
                        one=True)["n"],
        "pregnant": q("SELECT COUNT(DISTINCT cattle_id) n FROM breedings WHERE pregnancy='Confirmed'"
                      " AND actual_calving IS NULL", one=True)["n"],
        "pending_orders": q("SELECT COUNT(*) n FROM orders WHERE status='Pending'", one=True)["n"],
        "sales_month": q("SELECT COALESCE(SUM(total),0) n FROM orders WHERE status<>'Cancelled' AND created_at>=%s",
                         (month_start,), one=True)["n"],
    }
    vacc_due = q("SELECT v.*, c.tag_no, c.name cname FROM vaccinations v JOIN cattle c ON c.id=v.cattle_id"
                 " WHERE c.status='Active' AND v.next_due<=%s AND NOT EXISTS (SELECT 1 FROM vaccinations v2"
                 " WHERE v2.cattle_id=v.cattle_id AND v2.vaccine=v.vaccine AND v2.date_given>v.date_given)"
                 " ORDER BY v.next_due", (soon,))
    calvings = q("SELECT b.*, c.tag_no, c.name cname FROM breedings b JOIN cattle c ON c.id=b.cattle_id"
                 " WHERE b.pregnancy='Confirmed' AND b.actual_calving IS NULL ORDER BY b.expected_calving LIMIT 8")
    recent_health = q("SELECT h.*, c.tag_no FROM health_records h JOIN cattle c ON c.id=h.cattle_id"
                      " ORDER BY h.date DESC, h.id DESC LIMIT 5")
    days = [(t - timedelta(days=i)) for i in range(13, -1, -1)]
    by_day = {r["date"]: r["l"] for r in q("SELECT date, SUM(litres) l FROM milk_records WHERE date>=%s"
                                            " GROUP BY date", (days[0],))}  # keyed by date objects
    milk = [{"d": d, "l": by_day.get(d, 0)} for d in days]
    m = panchang.month_of(t)
    return render_template("dashboard.html", stats=stats, vacc_due=vacc_due, calvings=calvings,
                           recent_health=recent_health, milk=milk, milk_max=max([x["l"] for x in milk] + [1]),
                           pt=panchang.day_info(t), pmonth=m)


def _report_data(d_from, d_to):
    a = (d_from, d_to)
    return {
        "herd": q("SELECT species, breed, sex, status, COUNT(*) n FROM cattle GROUP BY species, breed, sex, status"
                  " ORDER BY status, species, breed"),
        "milk_month": q("SELECT to_char(date, 'YYYY-MM') AS month, SUM(litres) litres, COUNT(DISTINCT cattle_id) animals"
                        " FROM milk_records WHERE date BETWEEN %s AND %s GROUP BY month ORDER BY month", a),
        "milk_animal": q("SELECT c.tag_no, c.name, SUM(m.litres) litres, COUNT(DISTINCT m.date) days,"
                         " ROUND(SUM(m.litres)*1.0/COUNT(DISTINCT m.date),2) per_day FROM milk_records m"
                         " JOIN cattle c ON c.id=m.cattle_id WHERE m.date BETWEEN %s AND %s"
                         " GROUP BY c.id ORDER BY litres DESC", a),
        "costs": q("SELECT 'Health' item, COALESCE(SUM(cost),0) amount FROM health_records WHERE date BETWEEN %s AND %s"
                   " UNION ALL SELECT 'Vaccination', COALESCE(SUM(cost),0) FROM vaccinations"
                   " WHERE date_given BETWEEN %s AND %s"
                   " UNION ALL SELECT 'Feed', COALESCE(SUM(cost),0) FROM feedings WHERE date BETWEEN %s AND %s", a * 3),
        "feed": q("SELECT feed_type, SUM(quantity_kg) kg, SUM(cost) cost FROM feedings WHERE date BETWEEN %s AND %s"
                  " GROUP BY feed_type ORDER BY kg DESC", a),
        "sales": q("SELECT i.name product, SUM(i.qty) qty, SUM(i.qty*i.price) amount FROM order_items i"
                   " JOIN orders o ON o.id=i.order_id WHERE o.status<>'Cancelled'"
                   " AND o.created_at::date BETWEEN %s AND %s GROUP BY i.name ORDER BY amount DESC", a),
        "breeding": q("SELECT c.tag_no, b.date, b.method, b.pregnancy, b.expected_calving, b.actual_calving"
                      " FROM breedings b JOIN cattle c ON c.id=b.cattle_id WHERE b.date BETWEEN %s AND %s"
                      " ORDER BY b.date DESC", a),
    }


REPORT_TITLES = {"herd": "Herd summary", "milk_month": "Milk by month", "milk_animal": "Milk by animal",
                 "costs": "Costs", "feed": "Feed used", "sales": "Shop sales", "breeding": "Breeding"}


def _range():
    t = date.today()
    d_from = parse_date(request.args.get("from"), t - timedelta(days=90))
    d_to = parse_date(request.args.get("to"), t)
    return d_from.isoformat(), d_to.isoformat()


@app.route("/reports")
@login_required(*FARM)
def reports():
    d_from, d_to = _range()
    return render_template("reports.html", data=_report_data(d_from, d_to), titles=REPORT_TITLES,
                           d_from=d_from, d_to=d_to)


@app.route("/reports/<name>.csv")
@login_required(*FARM)
def report_csv(name):
    if name not in REPORT_TITLES:
        abort(404)
    d_from, d_to = _range()
    rows = _report_data(d_from, d_to)[name]
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        w.writerows([list(r.values()) for r in rows])  # dict rows: tuple(r) would give the keys
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={name}_{d_from}_{d_to}.csv"})


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, msg="You don't have access to this page."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, msg="Page not found."), 404


@app.errorhandler(psycopg.errors.DataError)
def bad_value(e):
    # e.g. a date or number typed by hand that PostgreSQL can't read; SQLite used to store it as text
    return render_template("error.html", code=400, msg="Some of the values entered are not valid. "
                                                       "Please go back and check dates and numbers."), 400


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
