"""VrushaA2 Dairy Tech: the company website and a cattle management web application (Flask + PostgreSQL)."""
import atexit
import io
import os
import re
import secrets
import time
from calendar import monthrange
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import wraps
from zoneinfo import ZoneInfo

import flask
import psycopg
from dotenv import load_dotenv
from flask import (Flask, abort, flash, g, has_request_context, redirect, request, send_file, session,
                   url_for)
from werkzeug.exceptions import HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import i18n
import panchang
from services import bulk_io

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
TIMEZONE = "Asia/Kolkata"  # order times and "this month" are counted in farm time

load_dotenv(os.path.join(BASE_DIR, ".env"))  # real environment variables win over .env


def _env(name):
    if not os.environ.get(name):
        raise SystemExit(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return os.environ[name]


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # photo uploads and import files
app.secret_key = _env("SECRET_KEY")
# Log out after an hour without a click. Every request restarts the hour, and Flask also refuses
# a login cookie older than this, so a browser that restores its tabs cannot keep someone signed in.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=1)

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
    session.permanent = True  # a cookie with an expiry date (see PERMANENT_SESSION_LIFETIME)
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
        abort(400, tr("Form expired, please go back and try again."))


# ---------------------------------------------------------------- language: English or Marathi

def lang():
    """The visitor's language, chosen with the switch in the header and kept in a cookie."""
    code = request.cookies.get("lang") if has_request_context() else None
    return code if code in i18n.LANGS else "en"


def tr(text, **kw):
    """Interface text in the visitor's language. Templates call it as _('...')."""
    return i18n.translate(text, lang(), **kw)


@app.template_filter("fdate")
def fdate(d, fmt="%d %b %Y"):
    """A date with month and weekday names in the visitor's language."""
    return i18n.date_text(d, fmt, lang()) if d else ""


app.jinja_env.globals.update(_=tr, LANGS=i18n.LANGS, BRAND=i18n.BRAND, COMPANY=i18n.COMPANY,
                             TAGLINE=i18n.TAGLINE)

# Text people type (animal names, notes, product names...) is stored as typed. In Marathi it is shown
# machine-translated (i18n.machine_translate), each text once: the result is kept in text_translations.
DATA_TRANSLATION = os.environ.get("DATA_TRANSLATION", "on") != "off"
_translation_paused_until = 0.0  # after a failed attempt, so a slow or unreachable service doesn't slow pages


def _saved_translations():
    """English -> Marathi for typed text, read once per request; None before `alembic upgrade head`."""
    if "mr_saved" not in g:
        try:
            g.mr_saved = {r["source"]: r["translated"]
                          for r in q("SELECT source, translated FROM text_translations WHERE lang='mr'")}
        except psycopg.errors.UndefinedTable:
            g.mr_saved = None
    return g.mr_saved


@app.template_filter("dv")
def data_text(text):
    """Text someone typed, in the visitor's language. Not yet translated: English, and noted for translation."""
    if not isinstance(text, str) or lang() != "mr" or not re.search("[A-Za-z]", text):
        return "" if text is None else text
    if text in i18n.MR:  # a breed, vaccine, feed... from the lists
        return i18n.MR[text]
    saved = _saved_translations()
    if saved is None:
        return text
    if text not in saved:
        g.setdefault("mr_missing", set()).add(text)
    return saved.get(text, text)


def _translate_new(texts):
    """Translate texts no page has needed before and save them. True if any were saved."""
    global _translation_paused_until
    if not DATA_TRANSLATION or time.monotonic() < _translation_paused_until:
        return False
    found = i18n.machine_translate(sorted(texts)[:40], "mr", email=os.environ.get("MYMEMORY_EMAIL") or None)
    if not found:
        _translation_paused_until = time.monotonic() + 600
        return False
    with get_db().cursor() as cur:
        cur.executemany("INSERT INTO text_translations (source, lang, translated) VALUES (%s, 'mr', %s)"
                        " ON CONFLICT DO NOTHING", list(found.items()))
    g.pop("mr_saved", None)
    return True


def render_template(name, **context):
    """flask.render_template; a Marathi page showing newly typed text is drawn again once it is translated."""
    page = flask.render_template(name, **context)
    missing = g.pop("mr_missing", None)
    if missing and _translate_new(missing):
        page = flask.render_template(name, **context)
    return page


@app.route("/lang/<code>")
def set_lang(code):
    nxt = request.args.get("next", "")
    resp = redirect(nxt if nxt.startswith("/") and not nxt.startswith(("//", "/\\")) else url_for("index"))
    if code in i18n.LANGS:
        resp.set_cookie("lang", code, max_age=365 * 24 * 3600, samesite="Lax", httponly=True)
    return resp


def home_delivery():
    """Whether the farm is taking home deliveries; the owner switches it on Customer orders. Off until then."""
    try:
        row = q("SELECT value FROM settings WHERE key='home_delivery'", one=True)
    except psycopg.errors.UndefinedTable:  # before `alembic upgrade head`
        return False
    return bool(row) and row["value"] == "on"


@app.context_processor
def inject():
    cart = session.get("cart", {})
    user = current_user()
    # for the menu: open tasks due today or overdue, and orders nobody has dealt with yet (a green count)
    tasks_due = new_orders = 0
    if user and user["role"] in FARM:
        tasks_due = q("SELECT COUNT(*) AS n FROM tasks WHERE status='Open' AND due_date<=%s",
                      (date.today(),), one=True)["n"]
        new_orders = q("SELECT COUNT(*) AS n FROM orders WHERE status='Pending'", one=True)["n"]
    return {"user": user, "csrf_token": session.get("csrf", ""), "tasks_due": tasks_due, "new_orders": new_orders,
            "home_delivery": home_delivery(),
            "cart_count": sum(cart.values()) if cart else 0, "today": date.today(), "lang": lang()}


# ---------------------------------------------------------------- where each page sits: Back button and breadcrumbs

SITE_PAGES = {"site_about": "About us", "site_products": "Products", "site_order": "How to order",
              "site_contact": "Contact"}
TOP_PAGES = {"index", "website", "login", "dashboard"}  # no Back button on these


def _home_of(user):
    """The first page of each kind of user: the dashboard, the shop, or the website."""
    if user is None:
        return "index", {}
    return ("dashboard" if user["role"] in FARM else "shop"), {}


def _request_kind():
    """The record type a page is about (/records/milk, /import?kind=milk, or the import form's choice).

    The form is read only if it can be: for an upload over the size limit, reading it raises the 413 again.
    """
    kind = (request.view_args or {}).get("kind") or request.args.get("kind")
    if kind is None and request.method == "POST":
        try:
            kind = request.form.get("kind")
        except HTTPException:
            kind = None
    return kind


def _parent(ep, args, user):
    """The page above (endpoint, view args), as (endpoint, args); None for a top page."""
    if ep in TOP_PAGES:
        return None
    kind = args.get("kind") or _request_kind()
    customer = user is not None and user["role"] == "customer"
    site_home = ("website" if user else "index"), {}
    parents = {
        "cattle_list": ("dashboard", {}), "cattle_profile": ("cattle_list", {}),
        "cattle_form": ("cattle_profile", {"cid": args["cid"]}) if args.get("cid") else ("cattle_list", {}),
        "records": ("dashboard", {}), "record_form": ("records", {"kind": kind}),
        "import_page": ("records", {"kind": kind}) if kind in RECORDS else ("cattle_list", {}),
        "import_confirm": ("import_page", {}), "tasks": ("dashboard", {}), "task_form": ("tasks", {}),
        "products": ("dashboard", {}), "product_form": ("products", {}), "manage_orders": ("dashboard", {}),
        "users": ("dashboard", {}), "user_form": ("users", {}), "nakshatras": ("calendar", {}),
        "shop": site_home if customer else ("dashboard", {}), "cart": ("shop", {}), "my_orders": ("shop", {}),
        "order_detail": ("my_orders", {}) if customer else ("manage_orders", {}),
        "register": ("login", {}), **{p: site_home for p in SITE_PAGES},
    }
    if ep in parents:
        return parents[ep]
    return site_home if ep in ("calendar", "profile") and user is None else _home_of(user)


def _page_label(ep, args):
    if ep == "records":
        return tr(RECORDS[args["kind"]]["title"])
    if ep == "cattle_profile":
        c = q("SELECT tag_no FROM cattle WHERE id=%s", (args["cid"],), one=True)
        return c["tag_no"] if c else tr("Animal")
    return tr({"index": "Home", "website": "Home", "dashboard": "Dashboard", "cattle_list": "Cattle", "tasks": "Tasks",
               "products": "Products and stock", "manage_orders": "Customer orders", "users": "Users",
               "calendar": "Calendar", "shop": "Shop", "my_orders": "My orders", "login": "Log in",
               "import_page": "Import from a file", **SITE_PAGES}.get(ep, ep))


@app.context_processor
def page_place():
    """trail: the pages above this one, [(label, url)] from the top; back_to: where "← Back" goes (None: no button).

    The Back button goes to the page above (a record form to its list, an animal to the register...), or to
    the page that sent you here when the link said so (?back=/cattle/12), so a form opened from an animal
    returns to it. The script in app.js uses the browser history when a page is not in this map.
    """
    if not has_request_context():
        return {}
    user, ep = current_user(), request.endpoint
    if ep is None or ep == "static":  # a page that was not found: back home
        url = url_for(_home_of(user)[0])
        return {"trail": [(_page_label(_home_of(user)[0], {}), url)], "back_to": url, "back_known": False,
                "page_kind": None}
    place = _page_place(user, ep)
    place["page_kind"] = _request_kind()  # for the menu: which record type is open
    return place


def _page_place(user, ep):
    trail, cur, seen = [], (ep, dict(request.view_args or {})), set()
    while (cur := _parent(*cur, user)) is not None and cur[0] not in seen and len(trail) < 6:
        seen.add(cur[0])
        if cur[0] == "records" and cur[1].get("kind") not in RECORDS:
            cur = ("dashboard", {})
        trail.insert(0, (_page_label(*cur), url_for(cur[0], **cur[1])))
    if ep in TOP_PAGES or not trail:
        return {"trail": [], "back_to": None, "back_known": True}
    back = request.args.get("back", "")
    back = back if back.startswith("/") and not back.startswith(("//", "/\\")) else trail[-1][1]
    return {"trail": trail, "back_to": back, "back_known": True}


# ---------------------------------------------------------------- lists: 25 rows a page

PER_PAGE = 25


def paged(sql, args, order):
    """One page of a list: (rows, pager). `sql` without ORDER BY; ?page=N in the address picks the page."""
    total = q(f"SELECT COUNT(*) AS n FROM ({sql}) AS x", args, one=True)["n"]
    pages = max(1, -(-total // PER_PAGE))
    page = min(max(request.args.get("page", 1, type=int) or 1, 1), pages)
    rows = q(f"{sql} ORDER BY {order} LIMIT {PER_PAGE} OFFSET {(page - 1) * PER_PAGE}", args)
    return rows, {"page": page, "pages": pages, "total": total,
                  "first": (page - 1) * PER_PAGE + 1 if total else 0, "last": min(page * PER_PAGE, total)}


def page_url(n):
    """This page's address with ?page=n, keeping the search and filters."""
    params = {k: v for k, v in request.args.items() if k != "page"}
    return url_for(request.endpoint, **(request.view_args or {}), **params, **({"page": n} if n > 1 else {}))


def page_numbers(pager):
    """1 … 4 5 [6] 7 8 … 42: the pages to link to, None for a gap."""
    p, last = pager["page"], pager["pages"]
    shown = sorted({1, last, *range(max(1, p - 2), min(last, p + 2) + 1)})
    out = []
    for n in shown:
        if out and n - out[-1] > 1:
            out.append(None)
        out.append(n)
    return out


app.jinja_env.globals.update(page_url=page_url, page_numbers=page_numbers)


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
def dt(v, fmt=None):
    """A TIMESTAMPTZ shown in farm time: its date and 12-hour time (07 Nov 2026, 8:15 AM), or `fmt` for just a date."""
    if not v:
        return ""
    local = v.astimezone(ZoneInfo(TIMEZONE))
    if fmt:
        return i18n.date_text(local, fmt, lang())
    return tr("{date}, {time}", date=i18n.date_text(local, "%d %b %Y", lang()), time=i18n.clock_text(local, lang()))


@app.template_filter("clock")
def clock(t, day=None):
    """A time of day on the 12-hour clock in farm time. Given the calendar `day` it belongs to, a time on another
    date (after midnight) also shows that date, so it can't be read as the wrong day: 6:32 AM, 8 Nov."""
    if not t:
        return ""
    local = t.astimezone(ZoneInfo(TIMEZONE)) if getattr(t, "tzinfo", None) else t
    text = i18n.clock_text(local, lang())
    if day is not None and local.date() != day:
        return tr("{time}, {date}", time=text, date=i18n.date_text(local.date(), "%d %b", lang()))
    return text


def age_text(dob):
    if not dob:
        return ""
    d = dob if isinstance(dob, date) else date.fromisoformat(dob)
    months = (date.today().year - d.year) * 12 + date.today().month - d.month
    if date.today().day < d.day:
        months -= 1
    y, m = divmod(max(months, 0), 12)
    return tr("{y} y {m} m", y=y, m=m) if y else tr("{m} m", m=m)


app.jinja_env.globals["age_text"] = age_text


def parse_date(s, default=None):
    try:
        return date.fromisoformat(s) if s else default
    except ValueError:
        return default


# ---------------------------------------------------------------- login, register, profile

def _website():
    """The home page: one screen with the logo, the tagline, two buttons and the facts strip."""
    stats = {"cattle": q("SELECT COUNT(*) AS n FROM cattle WHERE status='Active'", one=True)["n"],
             "products": q("SELECT COUNT(*) AS n FROM products WHERE active=1", one=True)["n"]}
    return render_template("home.html", stats=stats)


# The website's other pages, one each for the menu
@app.route("/about")
def site_about():
    t = date.today()
    return render_template("site_about.html", pt=panchang.day_info(t), pmonth=panchang.month_of(t),
                           location=panchang.LOCATION)


@app.route("/products")
def site_products():
    return render_template("site_products.html",
                           products=q("SELECT * FROM products WHERE active=1 ORDER BY category, name"))


@app.route("/how-to-order")
def site_order():
    return render_template("site_order.html")


@app.route("/contact")
def site_contact():
    # BRAND_ADDRESS, BRAND_PHONE and BRAND_EMAIL in .env (and on Vercel); blank ones are not shown
    contact = {k: os.environ.get(f"BRAND_{k.upper()}", "").strip() for k in ("address", "phone", "email")}
    return render_template("site_contact.html", contact=contact, location=panchang.LOCATION)


@app.route("/")
def index():
    """The company website; logged-in users go straight to their part of the app."""
    user = current_user()
    if user is None:
        return _website()
    return redirect(url_for("shop" if user["role"] == "customer" else "dashboard"))


@app.route("/home")
def website():
    return _website()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = q("SELECT * FROM users WHERE username=%s", (request.form["username"].strip(),), one=True)
        if user and user["active"] and check_password_hash(user["password_hash"], request.form["password"]):
            session["user_id"] = user["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
        flash(tr("Wrong username or password, or the account is disabled."), "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    session.pop("cart", None)
    return redirect(url_for("login"))


PINCODE = re.compile(r"[1-9][0-9]{5}")  # an Indian pincode: 6 digits, not starting with 0


def _address(f, required):
    """(address, city, pincode, problem) from a form. The city is tidied ("navi mumbai" -> "Navi Mumbai"),
    the pincode must be 6 digits. `required`: a customer needs all three, for delivery and for the filters."""
    address = (f.get("address") or "").strip() or None
    city = " ".join((f.get("city") or "").split()) or None
    if city and city.isascii():
        city = city.title()
    pincode = re.sub(r"\s", "", f.get("pincode") or "") or None
    problem = None
    if required and not (address and city and pincode):
        problem = tr("Please enter your address, city and pincode.")
    elif pincode and not PINCODE.fullmatch(pincode):
        problem = tr("The pincode must be 6 digits, like 416416.")
    return address, city, pincode, problem


@app.route("/register", methods=["GET", "POST"])
def register():
    """Self sign-up creates a customer account for the shop."""
    if request.method == "POST":
        f = request.form
        address, city, pincode, problem = _address(f, required=True)
        if len(f["password"]) < 6:
            flash(tr("Password must be at least 6 characters."), "error")
        elif q("SELECT 1 FROM users WHERE username=%s", (f["username"].strip(),)):
            flash(tr("That username is taken."), "error")
        elif problem:
            flash(problem, "error")
        else:
            uid = ex("INSERT INTO users (username, password_hash, full_name, phone, email, address, city, pincode,"
                     " role) VALUES (%s,%s,%s,%s,%s,%s,%s,%s, 'customer')",
                     (f["username"].strip(), generate_password_hash(f["password"]), f["full_name"].strip(),
                      f.get("phone"), f.get("email"), address, city, pincode))
            session["user_id"] = uid
            flash(tr("Welcome! Your account is ready."), "ok")
            return redirect(url_for("shop"))
    return render_template("register.html", form=request.form)


@app.route("/profile", methods=["GET", "POST"])
@login_required()
def profile():
    user = current_user()
    if request.method == "POST":
        f = request.form
        address, city, pincode, problem = _address(f, required=user["role"] == "customer")
        if problem:
            flash(problem, "error")
            return redirect(url_for("profile"))
        ex("UPDATE users SET full_name=%s, phone=%s, email=%s, address=%s, city=%s, pincode=%s WHERE id=%s",
           (f["full_name"], f.get("phone"), f.get("email"), address, city, pincode, user["id"]))
        if f.get("new_password"):
            if not check_password_hash(user["password_hash"], f.get("old_password", "")):
                flash(tr("Current password is wrong; password not changed."), "error")
                return redirect(url_for("profile"))
            if len(f["new_password"]) < 6:
                flash(tr("New password must be at least 6 characters."), "error")
                return redirect(url_for("profile"))
            ex("UPDATE users SET password_hash=%s WHERE id=%s",
               (generate_password_hash(f["new_password"]), user["id"]))
        flash(tr("Profile saved."), "ok")
        return redirect(url_for("profile"))
    return render_template("profile.html")


# ---------------------------------------------------------------- admin: users

@app.route("/admin/users")
@login_required("admin")
def users():
    role, s = request.args.get("role", ""), request.args.get("q", "").strip()
    city, pin = _place_filters()
    sort = request.args.get("sort", "") if request.args.get("sort") in USER_SORTS else ""
    sql, args = "SELECT * FROM users WHERE TRUE", []
    if role:
        sql += " AND role=%s"
        args.append(role)
    if s:
        sql += (" AND (full_name ILIKE %s OR username ILIKE %s OR phone ILIKE %s OR email ILIKE %s"
                " OR address ILIKE %s OR city ILIKE %s)")
        args += [f"%{s}%"] * 6
    if city:
        sql += " AND lower(city)=lower(%s)"
        args.append(city)
    if pin:  # 416 finds every pincode starting 416: a whole district
        sql += " AND pincode LIKE %s"
        args.append(pin + "%")
    rows, pager = paged(sql, args, order=USER_SORTS[sort][1])
    # the cities to choose from, with how many accounts (of the chosen role) are in each
    cities = q("SELECT min(city) AS city, COUNT(*) AS n FROM users WHERE city IS NOT NULL"
               + (" AND role=%s" if role else "") + " GROUP BY lower(city) ORDER BY lower(min(city))",
               (role,) if role else ())
    return render_template("users.html", users=rows, pager=pager, role=role, s=s, city=city, pin=pin, sort=sort,
                           cities=cities, sorts=USER_SORTS)


def _place_filters():
    """?city= and ?pincode= of a list (the pincode as digits, so "416 416" works)."""
    return request.args.get("city", "").strip(), re.sub(r"\D", "", request.args.get("pincode", ""))


# the Users list's sort orders: (label, ORDER BY)
USER_SORTS = {"": ("Role and name", "role, full_name"), "name": ("Name", "lower(full_name)"),
              "city": ("City", "lower(city) NULLS LAST, lower(full_name)"),
              "pincode": ("Pincode", "pincode NULLS LAST, lower(full_name)"),
              "new": ("Newest first", "created_at DESC"), "old": ("Oldest first", "created_at")}


@app.route("/admin/users/new", methods=["GET", "POST"])
@app.route("/admin/users/<int:uid>/edit", methods=["GET", "POST"])
@login_required("admin")
def user_form(uid=None):
    u = q("SELECT * FROM users WHERE id=%s", (uid,), one=True) if uid else None
    if uid and not u:
        abort(404)
    if request.method == "POST":
        f = request.form
        # admins add farm staff (customers sign up themselves); an existing account's role can be changed
        role = (f.get("role") if f.get("role") in ("admin", "staff", "customer") else u["role"]) if u else "staff"
        active = 1 if f.get("active") else 0
        if u and u["id"] == current_user()["id"] and (role != "admin" or not active):
            flash(tr("You cannot remove your own admin access."), "error")
            return redirect(request.url)
        clash = q("SELECT id FROM users WHERE username=%s AND id<>%s", (f["username"].strip(), uid or 0))
        address, city, pincode, problem = _address(f, required=False)
        if clash:
            flash(tr("That username is taken."), "error")
        elif not u and len(f.get("password", "")) < 6:
            flash(tr("Password must be at least 6 characters."), "error")
        elif problem:
            flash(problem, "error")
        else:
            vals = (f["username"].strip(), f["full_name"], f.get("phone"), f.get("email"),
                    address, city, pincode, role, active)
            if u:
                # never the password: it is set when a staff account is added, changed by its owner on
                # My profile, and for staff only, reset by an admin (user_password below)
                ex("UPDATE users SET username=%s, full_name=%s, phone=%s, email=%s, address=%s, city=%s, pincode=%s,"
                   " role=%s, active=%s WHERE id=%s", vals + (uid,))
            else:
                ex("INSERT INTO users (username, full_name, phone, email, address, city, pincode, role, active,"
                   " password_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                   vals + (generate_password_hash(f["password"]),))
            flash(tr("User saved."), "ok")
            return redirect(url_for("users"))
    return render_template("user_form.html", u=u)


@app.route("/admin/users/<int:uid>/password", methods=["POST"])
@login_required("admin")
def user_password(uid):
    """An admin sets a new password for a staff member who forgot theirs. Customers' and admins' are their own."""
    u = q("SELECT * FROM users WHERE id=%s", (uid,), one=True) or abort(404)
    if u["role"] != "staff":
        abort(403)
    pw, again = request.form.get("new_password", ""), request.form.get("confirm_password", "")
    if len(pw) < 6:
        flash(tr("New password must be at least 6 characters."), "error")
    elif pw != again:
        flash(tr("The two passwords do not match."), "error")
    else:
        ex("UPDATE users SET password_hash=%s WHERE id=%s", (generate_password_hash(pw), uid))
        flash(tr("Password reset for {name}. Give them the new password; they can change it on My profile.",
                 name=data_text(u["full_name"])), "ok")
    return redirect(url_for("user_form", uid=uid))


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
@login_required("admin")
def user_delete(uid):
    u = q("SELECT * FROM users WHERE id=%s", (uid,), one=True) or abort(404)
    orders = q("SELECT COUNT(*) AS n FROM orders WHERE user_id=%s", (uid,), one=True)["n"]
    if uid == current_user()["id"]:
        flash(tr("You cannot remove your own account."), "error")
    elif orders:
        # orders keep the customer's name and address, so the sales history stays complete
        flash(tr("{name} has {n} orders, so the account is kept for the order history. "
                 "Untick 'Account active' to stop them logging in.", name=data_text(u["full_name"]), n=orders),
              "error")
        return redirect(url_for("user_form", uid=uid))
    else:
        ex("DELETE FROM users WHERE id=%s", (uid,))  # their tasks stay, with nobody assigned
        flash(tr("{name} removed.", name=data_text(u["full_name"])), "ok")
    return redirect(url_for("users"))


# ---------------------------------------------------------------- cattle

CATTLE_FIELDS = ["tag_no", "name", "species", "breed", "sex", "dob", "color", "weight_kg", "source",
                 "purchase_date", "purchase_price", "dam_id", "sire", "status", "notes"]
SPECIES = ["Cow", "Buffalo", "Bull", "Ox", "Calf", "Heifer"]
BREEDS = ["Gir", "Sahiwal", "Red Sindhi", "Tharparkar", "Khillar", "Deoni", "Kankrej", "Ongole",
          "Hallikar", "HF Cross", "Jersey Cross", "Murrah", "Mehsana", "Jaffarabadi", "Pandharpuri", "Other"]


def _cattle_filters():
    return request.args.get("status", "Active"), request.args.get("q", "").strip()


def _cattle_query(status, s):
    """The register with its filters (the page and its Excel export), with each mother's tag."""
    sql = "SELECT c.*, d.tag_no AS dam_tag FROM cattle c LEFT JOIN cattle d ON d.id=c.dam_id WHERE TRUE"
    args = []
    if status:
        sql += " AND c.status=%s"
        args.append(status)
    if s:
        sql += " AND (c.tag_no ILIKE %s OR c.name ILIKE %s OR c.breed ILIKE %s)"
        args += [f"%{s}%"] * 3
    return sql, args


def _cattle_rows(status, s):
    sql, args = _cattle_query(status, s)
    return q(sql + " ORDER BY c.tag_no", args)


@app.route("/cattle")
@login_required(*FARM)
def cattle_list():
    status, s = _cattle_filters()
    rows, pager = paged(*_cattle_query(status, s), order="tag_no")
    return render_template("cattle_list.html", rows=rows, pager=pager, status=status, s=s)


def _save_photo(file):
    if not file or not file.filename:
        return None
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        flash(tr("Photo must be a JPG, PNG, WEBP or GIF image."), "error")
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
            flash(tr("Tag number is required."), "error")
        elif q("SELECT 1 FROM cattle WHERE tag_no=%s AND id<>%s", (vals["tag_no"], cid or 0)):
            flash(tr("Another animal already has that tag number."), "error")
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
            flash(tr("Animal saved."), "ok")
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
    task_rows = _task_rows("t.status='Open' AND (t.all_animals=1 OR EXISTS (SELECT 1 FROM task_animals x"
                           " WHERE x.task_id=t.id AND x.cattle_id=%s))", (cid,))
    return render_template("cattle_profile.html", c=c, data=data, calves=calves, milk30=milk30,
                           records=RECORDS, task_rows=task_rows)


@app.route("/cattle/<int:cid>/delete", methods=["POST"])
@login_required("admin")
def cattle_delete(cid):
    ex("DELETE FROM cattle WHERE id=%s", (cid,))
    flash(tr("Animal and all its records deleted."), "ok")
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


def _record_filters():
    # a malformed date in the URL is ignored rather than sent to a DATE column
    return (request.args.get("cattle_id", type=int), parse_date(request.args.get("from")),
            parse_date(request.args.get("to")))


def _record_where(cfg, cid, d_from, d_to):
    sql, args = " WHERE TRUE", []
    if cid:
        sql += " AND r.cattle_id=%s"
        args.append(cid)
    if d_from:
        sql += f" AND r.{cfg['date']}>=%s"
        args.append(d_from)
    if d_to:
        sql += f" AND r.{cfg['date']}<=%s"
        args.append(d_to)
    return sql, args


def _record_query(cfg, cid, d_from, d_to):
    where, args = _record_where(cfg, cid, d_from, d_to)
    return f"SELECT r.*, c.tag_no, c.name AS cname FROM {cfg['table']} r JOIN cattle c ON c.id=r.cattle_id" + where, args


def _record_rows(cfg, cid, d_from, d_to):
    """A record page's rows with its filters, newest first: all of them, for the Excel export."""
    sql, args = _record_query(cfg, cid, d_from, d_to)
    return q(sql + f" ORDER BY r.{cfg['date']} DESC, r.id DESC", args)


@app.route("/records/<kind>")
@login_required(*FARM)
def records(kind):
    cfg = RECORDS.get(kind) or abort(404)
    cid, d_from, d_to = _record_filters()
    rows, pager = paged(*_record_query(cfg, cid, d_from, d_to), order=f"{cfg['date']} DESC, id DESC")
    animals = q("SELECT id, tag_no, name FROM cattle ORDER BY tag_no")
    labels = {f[0]: f[1] for f in cfg["fields"]}
    return render_template("records.html", kind=kind, cfg=cfg, rows=rows, pager=pager, animals=animals, cid=cid,
                           d_from=d_from or "", d_to=d_to or "", labels=labels)


@app.route("/records/<kind>/new", methods=["GET", "POST"])
@app.route("/records/<kind>/<int:rid>/edit", methods=["GET", "POST"])
@login_required(*FARM)
def record_form(kind, rid=None):
    cfg = RECORDS.get(kind) or abort(404)
    r = q(f"SELECT * FROM {cfg['table']} WHERE id=%s", (rid,), one=True) if rid else None
    if rid and not r:
        abort(404)
    # opened from a task: pre-fill it, and complete the task when the records are saved
    task_id = None if r else request.values.get("task", type=int)
    task = (_task_rows("t.id=%s AND t.status='Open' AND t.kind=%s", (task_id, kind)) or [None])[0] if task_id else None
    if request.method == "POST":
        f = request.form
        cattle_ids = f.getlist("cattle_id") if not r else [f.get("cattle_id")]
        cattle_ids = [int(x) for x in cattle_ids if x]
        vals = {name: (f.get(name) or None) for name, *_ in cfg["fields"]}
        missing = [n for n in cfg["required"] if not vals[n]]
        if not cattle_ids or missing:
            flash(tr("Please choose an animal and fill the required fields."), "error")
        else:
            nxt = done = None
            with get_db().transaction():  # all the records and the task, or nothing
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
                if task:
                    done = q("SELECT * FROM tasks WHERE id=%s AND status='Open' FOR UPDATE", (task["id"],), one=True)
                    if done:
                        nxt = complete_task(done)
            flash(tr("Saved {n} {what}.", n=len(cattle_ids),
                     what=tr(cfg["one"] + ("s" if len(cattle_ids) > 1 else ""))), "ok")
            if done:
                flash(_done_message(done, nxt), "ok")
            return redirect(_safe_back(url_for("records", kind=kind)))
    animals = q("SELECT id, tag_no, name, sex FROM cattle WHERE status='Active' OR id=%s ORDER BY tag_no",
                (r["cattle_id"] if r else 0,))
    if task:
        pre_ids = {a["id"] for a in animals} if task["all_animals"] else set(task["cattle_ids"])
        prefill = {TASK_ITEM_FIELD[kind]: task["item"]} if task["item"] else {}
    else:
        pre_ids, prefill = {request.args.get("cattle_id", type=int)}, {}
    return render_template("record_form.html", kind=kind, cfg=cfg, r=r, animals=animals, pre_ids=pre_ids,
                           prefill=prefill, task=task, back=request.args.get("back", ""))


@app.route("/records/<kind>/<int:rid>/delete", methods=["POST"])
@login_required(*FARM)
def record_delete(kind, rid):
    cfg = RECORDS.get(kind) or abort(404)
    ex(f"DELETE FROM {cfg['table']} WHERE id=%s", (rid,))
    flash(tr("Record deleted."), "ok")
    return redirect(request.referrer or url_for("records", kind=kind))


# ---------------------------------------------------------------- bulk import (CSV / Excel) and Excel export
# The reading, checking, saving and spreadsheet writing are in services/bulk_io.py; these are the pages.

IMPORT_SPECS = bulk_io.make_specs(RECORDS, SPECIES, BREEDS)
# The checked file goes from the preview to "Confirm import" inside the page, signed with the secret key
# so it can't be changed on the way, and good for an hour. Nothing is stored on the server in between.
_import_signer = URLSafeTimedSerializer(app.secret_key, salt="bulk-import")


def say(m):
    """A bulk_io.Msg in the visitor's language. A column name ({col}) and a list of choices are translated too."""
    kw = {k: tr(v) if k == "col" else ", ".join(tr(x) for x in v) if isinstance(v, tuple) else v
          for k, v in m.values.items()}
    return tr(m.text, **kw)


def _animals_by_tag():
    return {a["tag_no"]: a for a in q("SELECT id, tag_no, sex, species FROM cattle")}


def _unsign(token):
    try:
        return _import_signer.loads(token or "", max_age=3600)
    except BadSignature:  # also SignatureExpired
        return None


def _spreadsheet(data, name):
    resp = send_file(io.BytesIO(data), mimetype=bulk_io.XLSX, as_attachment=True, download_name=name)
    resp.headers["Cache-Control"] = "no-store"  # farm data: not kept in shared caches
    return resp


@app.route("/import", methods=["GET", "POST"])
@login_required(*FARM)
def import_page():
    kind = request.values.get("kind", "cattle")
    spec = IMPORT_SPECS.get(kind) or IMPORT_SPECS["cattle"]
    if request.method == "POST":
        file = request.files.get("file")
        try:
            if not file or not file.filename:
                raise bulk_io.FileProblem(bulk_io.msg("Choose a file to import."))
            header, rows = bulk_io.read_table(file.filename, file.read(bulk_io.MAX_BYTES + 1))
            found, ignored, problems = bulk_io.match_columns(spec, header)
            if problems:  # e.g. a cattle register file sent with "Health records" chosen
                fits = bulk_io.best_fit(IMPORT_SPECS, header)
                if fits is None:
                    raise bulk_io.FileProblem(*problems)
                flash(tr("This file has the columns of {right}, not {chosen}, so it is checked as {right}.",
                         right=tr(fits.title), chosen=tr(spec.title)), "ok")
                spec = fits
                found, ignored, problems = bulk_io.match_columns(spec, header)
        except bulk_io.FileProblem as e:
            for m in e.msgs:
                flash(say(m), "error")
        else:
            results = bulk_io.validate(spec, rows, found, _animals_by_tag())
            good = sum(r.ok for r in results)
            # all rows with problems (up to 300), then the first good ones, up to 500 shown
            bad_shown = [r for r in results if not r.ok][:300]
            shown = sorted(bad_shown + [r for r in results if r.ok][:500 - len(bad_shown)], key=lambda r: r.line)
            payload = _import_signer.dumps({"kind": spec.kind, "file": file.filename, "header": header,
                                            "rows": rows, "nonce": secrets.token_hex(8)})
            return render_template("import_preview.html", spec=spec, file=file.filename, results=shown,
                                   total=len(results), good=good, bad=len(results) - good, ignored=ignored,
                                   payload=payload, say=say)
    return render_template("import.html", spec=spec, specs=IMPORT_SPECS, max_rows=bulk_io.MAX_ROWS)


@app.route("/import/confirm", methods=["POST"])
@login_required(*FARM)
def import_confirm():
    data = _unsign(request.form.get("payload"))
    if not data or data.get("kind") not in IMPORT_SPECS:
        flash(tr("This preview has expired. Please upload the file again."), "error")
        return redirect(url_for("import_page"))
    spec = IMPORT_SPECS[data["kind"]]
    if data["nonce"] in session.get("imports_done", []):  # "Confirm" pressed twice, or the page sent again
        flash(tr("This file has already been imported."), "error")
        return redirect(url_for("import_page", kind=spec.kind))
    found, _, _ = bulk_io.match_columns(spec, data["header"])
    # checked again: the register may have changed since the preview
    results = bulk_io.validate(spec, [tuple(r) for r in data["rows"]], found, _animals_by_tag())
    try:
        imported = bulk_io.save(get_db(), spec, results, GESTATION_DAYS)
    except psycopg.Error as e:  # rolled back: nothing saved
        app.logger.warning("bulk import of %s refused: %s", spec.kind, type(e).__name__)
        flash(tr("Nothing was imported: the database refused one of the rows. Please check the file "
                 "and try again."), "error")
        return redirect(url_for("import_page", kind=spec.kind))
    session["imports_done"] = (session.get("imports_done", []) + [data["nonce"]])[-10:]
    skipped = [[r.line, *r.cells, "; ".join(say(m) for m in r.errors)] for r in results if not r.ok]
    return render_template("import_done.html", spec=spec, file=data["file"], imported=imported,
                           skipped=len(skipped), skipped_payload=_import_signer.dumps(
                               {"kind": spec.kind, "rows": skipped}) if skipped else "")


@app.route("/import/skipped", methods=["POST"])
@login_required(*FARM)
def import_skipped():
    """The rows an import skipped, with the reasons, as a CSV to fix and import again."""
    data = _unsign(request.form.get("payload"))
    if not data or data.get("kind") not in IMPORT_SPECS:
        flash(tr("This preview has expired. Please upload the file again."), "error")
        return redirect(url_for("import_page"))
    spec = IMPORT_SPECS[data["kind"]]
    header = [tr("Row")] + [tr(label) for label in spec.labels()] + [tr("Problems")]
    resp = flask.Response(bulk_io.csv_bytes(header, data["rows"]), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="{spec.slug}-skipped-{date.today()}.csv"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/import/template/<kind>.xlsx")
@login_required(*FARM)
def import_template(kind):
    spec = IMPORT_SPECS.get(kind) or abort(404)
    return _spreadsheet(bulk_io.template(spec, tr), f"{spec.slug}-template.xlsx")


@app.route("/cattle/export.xlsx")
@login_required(*FARM)
def cattle_export():
    spec = IMPORT_SPECS["cattle"]
    rows = [[c["dam_tag"] if col.name == "mother_tag" else c[col.name] for col in spec.columns]
            for c in _cattle_rows(*_cattle_filters())]
    return _spreadsheet(bulk_io.workbook(tr(spec.title), [tr(label) for label in spec.labels()], rows),
                        f"{spec.slug}-{date.today()}.xlsx")


@app.route("/records/<kind>/export.xlsx")
@login_required(*FARM)
def records_export(kind):
    cfg = RECORDS.get(kind) or abort(404)
    spec = IMPORT_SPECS[kind]
    cols = [c for c in spec.columns if c.name != "tag_no"]
    header = [tr("Tag number"), tr("Name")] + [tr(c.label.replace(" (blank = auto)", "")) for c in cols]
    rows = [[r["tag_no"], r["cname"]] + [r[c.name] for c in cols] for r in _record_rows(cfg, *_record_filters())]
    return _spreadsheet(bulk_io.workbook(tr(spec.title), header, rows), f"{spec.slug}-{date.today()}.xlsx")


# ---------------------------------------------------------------- tasks

# A task's kind is a record module (completed by saving that record) or havan / other (marked done directly).
TASK_KINDS = {"vaccinations": "Vaccination", "health": "Health / deworming", "feeding": "Feeding",
              "breeding": "Breeding / AI", "milk": "Milking", "havan": "Havan / puja", "other": "Other"}
REPEATS = {"none": "Does not repeat", "daily": "Every day", "weekly": "Every week",
           "monthly": "Every month", "yearly": "Every year"}
# the record-form field a task's "item" fills in, e.g. which vaccine
TASK_ITEM_FIELD = {"vaccinations": "vaccine", "health": "kind", "feeding": "feed_type",
                   "breeding": "method", "milk": "session"}


def _step(d, repeat):
    if repeat == "daily":
        return d + timedelta(days=1)
    if repeat == "weekly":
        return d + timedelta(weeks=1)
    y, m = divmod(d.month - 1 + (1 if repeat == "monthly" else 12), 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))  # 31 Jan + 1 month = 28/29 Feb


def next_occurrence(due, repeat, after):
    """The next date of a repeating task: at least one step past `due`, and after `after` (today)."""
    if repeat not in REPEATS or repeat == "none":
        return None
    d = _step(due, repeat)
    while d <= after:  # a task done late does not leave a trail of overdue copies
        d = _step(d, repeat)
    return d


def _task_rows(where="TRUE", args=(), order="t.due_date, t.id"):
    """Tasks with their assignee and animals (tags as text, ids as a list)."""
    return q("SELECT t.*, u.full_name AS assignee,"
             " COALESCE(string_agg(c.tag_no, ', ' ORDER BY c.tag_no), '') AS tags,"
             " array_remove(array_agg(c.id ORDER BY c.tag_no), NULL) AS cattle_ids"
             " FROM tasks t LEFT JOIN users u ON u.id=t.assigned_to"
             " LEFT JOIN task_animals ta ON ta.task_id=t.id LEFT JOIN cattle c ON c.id=ta.cattle_id"
             f" WHERE {where} GROUP BY t.id, u.full_name ORDER BY {order}", args)


def complete_task(t):
    """Mark task t done and schedule its next repeat. Call inside a transaction with t locked."""
    ex("UPDATE tasks SET status='Done', done_at=now(), done_by=%s WHERE id=%s", (current_user()["id"], t["id"]))
    nxt = next_occurrence(t["due_date"], t["repeat"], date.today())
    if nxt:
        new_id = ex("INSERT INTO tasks (title, kind, item, due_date, repeat, all_animals, assigned_to, notes,"
                    " created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (t["title"], t["kind"], t["item"], nxt, t["repeat"], t["all_animals"], t["assigned_to"],
                     t["notes"], t["created_by"]))
        # task_animals has no id column, so not ex() (which adds RETURNING id)
        get_db().execute("INSERT INTO task_animals (task_id, cattle_id)"
                         " SELECT %s, cattle_id FROM task_animals WHERE task_id=%s", (new_id, t["id"]))
    return nxt


def _done_message(t, nxt):
    return tr("Task done: {title}.", title=data_text(t["title"])) + (
        " " + tr("Next one scheduled for {date}.", date=fdate(nxt)) if nxt else "")


def next_havan_day(d, within=30):
    """The first day after d with Agni on earth (Prithvi), within `within` days."""
    for i in range(1, within + 1):
        if panchang.day_info(d + timedelta(days=i))["agni"]["good_for_havan"]:
            return d + timedelta(days=i)
    return None


def _safe_back(default):
    back = request.values.get("back", "")
    return back if back.startswith("/") and not back.startswith("//") else default


app.jinja_env.globals.update(TASK_KINDS=TASK_KINDS, REPEATS=REPEATS,
                             agni_on=lambda d: panchang.day_info(d)["agni"],
                             agni_periods=panchang.agni_periods, agni_nakshatra_change=panchang.agni_nakshatra_change)


@app.route("/tasks")
@login_required(*FARM)
def tasks():
    mine = request.args.get("mine") == "1"
    extra, args = (" AND t.assigned_to=%s", (current_user()["id"],)) if mine else ("", ())
    # open tasks 25 a page, by their ids; then the full rows (with animals and assignee) for that page
    ids, pager = paged("SELECT t.id, t.due_date FROM tasks t WHERE t.status='Open'" + extra, args, order="due_date, id")
    open_rows = _task_rows("t.id = ANY(%s)", ([r["id"] for r in ids],)) if ids else []
    return render_template("tasks.html", mine=mine, open_rows=open_rows, pager=pager,
                           done_rows=_task_rows("t.status='Done'" + extra, args, order="t.done_at DESC LIMIT 20"))


@app.route("/tasks/new", methods=["GET", "POST"])
@app.route("/tasks/<int:tid>/edit", methods=["GET", "POST"])
@login_required(*FARM)
def task_form(tid=None):
    t = (_task_rows("t.id=%s", (tid,)) or [None])[0] if tid else None
    if tid and not t:
        abort(404)
    staff = q("SELECT id, full_name FROM users WHERE role IN ('admin', 'staff') AND active=1 ORDER BY full_name")
    if request.method == "POST":
        f = request.form
        kind = f.get("kind") if f.get("kind") in TASK_KINDS else "other"
        due = parse_date(f.get("due_date"))
        repeat = f.get("repeat") if f.get("repeat") in REPEATS else "none"
        item = (f.get("item") or "").strip() or None
        title = (f.get("title") or "").strip() or TASK_KINDS[kind] + (f": {item}" if item else "")
        all_animals = 1 if f.get("all_animals") else 0
        wanted = [] if all_animals else [int(x) for x in f.getlist("cattle_id") if x.isdigit()]
        cattle_ids = [r["id"] for r in q("SELECT id FROM cattle WHERE id = ANY(%s) ORDER BY id", (wanted,))]
        assigned = f.get("assigned_to", type=int)
        assigned = assigned if assigned in {s["id"] for s in staff} else None
        if not due:
            flash(tr("Please choose a date for the task."), "error")
        else:
            vals = (title, kind, item, due, repeat, all_animals, assigned, f.get("notes") or None)
            with get_db().transaction():
                if t:
                    ex("UPDATE tasks SET title=%s, kind=%s, item=%s, due_date=%s, repeat=%s, all_animals=%s,"
                       " assigned_to=%s, notes=%s WHERE id=%s", vals + (tid,))
                    get_db().execute("DELETE FROM task_animals WHERE task_id=%s", (tid,))
                else:
                    tid = ex("INSERT INTO tasks (title, kind, item, due_date, repeat, all_animals, assigned_to,"
                             " notes, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                             vals + (current_user()["id"],))
                for cid in cattle_ids:
                    get_db().execute("INSERT INTO task_animals (task_id, cattle_id) VALUES (%s, %s)", (tid, cid))
            flash(tr("Task saved: {title}, {date}.", title=data_text(title), date=fdate(due)), "ok")
            agni = panchang.day_info(due)["agni"]
            if kind == "havan" and not agni["good_for_havan"]:
                good = next_havan_day(due)
                flash(tr("{date}: {note}.", date=fdate(due, "%d %b"), note=tr(agni["note"]))
                      + (" " + tr("Next day with Agni on earth: {date}.", date=fdate(good, "%a %d %b"))
                         if good else ""), "error")
            return redirect(_safe_back(url_for("tasks")))
    animals = q("SELECT id, tag_no, name FROM cattle WHERE status='Active'"
                " OR id = ANY(%s) ORDER BY tag_no", (t["cattle_ids"] if t else [],))
    items = {k: next((opts for name, _, _, opts in RECORDS[k]["fields"] if name == field), [])
             for k, field in TASK_ITEM_FIELD.items()}
    pre_date = parse_date(request.args.get("date")) or date.today()
    pre_cattle = request.args.get("cattle_id", type=int)
    return render_template("task_form.html", t=t, animals=animals, staff=staff, items=items,
                           pre_date=pre_date, pre_cattle=pre_cattle, back=request.args.get("back", ""))


@app.route("/tasks/<int:tid>/done", methods=["POST"])
@login_required(*FARM)
def task_done(tid):
    t = q("SELECT * FROM tasks WHERE id=%s", (tid,), one=True) or abort(404)
    if t["kind"] in RECORDS:  # completed by saving the record, in the record form
        return redirect(url_for("record_form", kind=t["kind"], task=tid, back=_safe_back(url_for("tasks"))))
    with get_db().transaction():
        t = q("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (tid,), one=True)
        if t["status"] == "Open":
            flash(_done_message(t, complete_task(t)), "ok")
    return redirect(_safe_back(url_for("tasks")))


@app.route("/tasks/<int:tid>/delete", methods=["POST"])
@login_required(*FARM)
def task_delete(tid):
    ex("DELETE FROM tasks WHERE id=%s", (tid,))
    flash(tr("Task deleted."), "ok")
    return redirect(_safe_back(url_for("tasks")))


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
    flash(tr("Added {product} to your cart.", product=data_text(p["name"])), "ok")
    return redirect(request.referrer or url_for("shop"))


@app.route("/cart/remove/<int:pid>", methods=["POST"])
@login_required()
def cart_remove(pid):
    cart = session.get("cart", {})
    if cart.pop(str(pid), None) is not None:
        session["cart"] = cart
        p = q("SELECT name FROM products WHERE id=%s", (pid,), one=True)
        flash(tr("Removed {product} from your cart.", product=data_text(p["name"]) if p else tr("the item")), "ok")
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
            flash(tr("Your cart is empty."), "error")
            return redirect(url_for("shop"))
        f = request.form
        phone, address = f.get("phone", "").strip(), f.get("address", "").strip()
        if not _valid_phone(phone) or not address:
            flash(tr("Please enter a phone number (at least 10 digits) and a delivery address."), "error")
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
            flash(tr("Not enough stock for: {products}", products=", ".join(short)), "error")
            return redirect(url_for("cart"))
        session["cart"] = {}
        flash(tr("Order #{id} placed. We will confirm it soon.", id=oid), "ok")
        return redirect(url_for("order_detail", oid=oid))
    lines, total = _cart_lines()
    return render_template("cart.html", lines=lines, total=total)


@app.route("/orders")
@login_required()
def my_orders():
    rows, pager = paged("SELECT * FROM orders WHERE user_id=%s", (current_user()["id"],), order="id DESC")
    return render_template("orders.html", rows=rows, pager=pager, manage=False, status="", s="")


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
    status, s = request.args.get("status", ""), request.args.get("q", "").strip().lstrip("#")
    city, pin = _place_filters()
    sql, args = "SELECT o.*, u.full_name, u.city, u.pincode FROM orders o JOIN users u ON u.id=o.user_id WHERE TRUE", []
    if status:
        sql += " AND o.status=%s"
        args.append(status)
    if city:  # the customer's city and pincode, for planning deliveries by area
        sql += " AND lower(u.city)=lower(%s)"
        args.append(city)
    if pin:
        sql += " AND u.pincode LIKE %s"
        args.append(pin + "%")
    if s:  # an order number or part of the customer's name
        sql += " AND (o.id::text=%s OR u.full_name ILIKE %s OR u.username ILIKE %s)"
        args += [s, f"%{s}%", f"%{s}%"]
    rows, pager = paged(sql, args, order="id DESC")
    cities = q("SELECT min(u.city) AS city, COUNT(*) AS n FROM orders o JOIN users u ON u.id=o.user_id"
               " WHERE u.city IS NOT NULL GROUP BY lower(u.city) ORDER BY lower(min(u.city))")
    return render_template("orders.html", rows=rows, pager=pager, manage=True, status=status, s=s, city=city,
                           pin=pin, cities=cities, statuses=ORDER_STATUSES)


@app.route("/admin/orders/<int:oid>/status", methods=["POST"])
@login_required(*FARM)
def order_status(oid):
    new = request.form["status"]
    if new not in ORDER_STATUSES:
        abort(400)
    unavailable = bool(request.form.get("delivery_unavailable"))
    expected = None if unavailable else parse_date(request.form.get("expected_delivery"))
    with get_db().transaction():
        # the row lock stops two clicks on "Cancelled" from returning the stock twice
        o = q("SELECT * FROM orders WHERE id=%s FOR UPDATE", (oid,), one=True) or abort(404)
        if new == "Confirmed" and not unavailable:  # confirming promises a date, which the customer then sees
            problem = None
            if expected is None:
                problem = tr("Please enter the expected delivery date to confirm this order.")
            elif expected < datetime.now(ZoneInfo(TIMEZONE)).date() and expected != o["expected_delivery"]:
                problem = tr("The expected delivery date cannot be in the past.")  # a date already saved may have passed
            if problem:
                flash(problem, "error")
                return redirect(request.referrer or url_for("manage_orders"))
        # return stock when an order is cancelled, take it again if un-cancelled
        if new == "Cancelled" and o["status"] != "Cancelled":
            ex("UPDATE products SET stock=stock+(SELECT qty FROM order_items i WHERE i.order_id=%s AND"
               " i.product_id=products.id) WHERE id IN (SELECT product_id FROM order_items WHERE order_id=%s)",
               (oid, oid))
        elif o["status"] == "Cancelled" and new != "Cancelled":
            ex("UPDATE products SET stock=stock-(SELECT qty FROM order_items i WHERE i.order_id=%s AND"
               " i.product_id=products.id) WHERE id IN (SELECT product_id FROM order_items WHERE order_id=%s)",
               (oid, oid))
        if new == "Confirmed":
            ex("UPDATE orders SET status=%s, expected_delivery=%s, delivery_unavailable=%s WHERE id=%s",
               (new, expected, 1 if unavailable else 0, oid))
        else:  # what was promised stays on record
            ex("UPDATE orders SET status=%s WHERE id=%s", (new, oid))
    if new == "Confirmed" and unavailable:
        flash(tr("Order #{id} confirmed: delivery unavailable.", id=oid), "ok")
    elif new == "Confirmed":
        flash(tr("Order #{id} confirmed: expected delivery {date}.", id=oid, date=fdate(expected)), "ok")
    else:
        flash(tr("Order #{id} marked {status}.", id=oid, status=tr(new)), "ok")
    return redirect(request.referrer or url_for("manage_orders"))


@app.route("/admin/home-delivery", methods=["POST"])
@login_required("admin")
def set_home_delivery():
    """The owner's switch: customers see whether the farm is taking home deliveries."""
    on = request.form.get("value") == "on"
    get_db().execute("INSERT INTO settings (key, value) VALUES ('home_delivery', %s)"
                     " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", ("on" if on else "off",))
    flash(tr("Customers now see that you are accepting home delivery.") if on
          else tr("Customers now see that there is no home delivery for now."), "ok")
    return redirect(url_for("manage_orders"))


@app.route("/admin/products")
@login_required(*FARM)
def products():
    s = request.args.get("q", "").strip()
    sql, args = "SELECT * FROM products WHERE TRUE", []
    if s:
        sql += " AND (name ILIKE %s OR category ILIKE %s)"
        args += [f"%{s}%"] * 2
    rows, pager = paged(sql, args, order="active DESC, category, name")
    return render_template("products.html", rows=rows, pager=pager, s=s)


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
            flash(tr("Price and stock must be numbers."), "error")
            return render_template("product_form.html", p=p, cats=PRODUCT_CATEGORIES)
        if p:
            ex("UPDATE products SET name=%s, category=%s, description=%s, price=%s, unit=%s, stock=%s, active=%s WHERE id=%s",
               vals + (pid,))
        else:
            ex("INSERT INTO products (name, category, description, price, unit, stock, active) VALUES (%s,%s,%s,%s,%s,%s,%s)",
               vals)
        flash(tr("Product saved."), "ok")
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
            # events are worded in the template, so typed text in them can be shown in Marathi
            events.setdefault(r["d"].isoformat(), []).append({"kind": "vaccine", "tag": r["tag_no"],
                                                              "what": r["vaccine"]})
        for r in q("SELECT b.expected_calving d, c.tag_no FROM breedings b JOIN cattle c ON c.id=b.cattle_id"
                   " WHERE b.actual_calving IS NULL AND b.pregnancy<>'Not pregnant'"
                   " AND b.expected_calving BETWEEN %s AND %s", (lo, hi)):
            events.setdefault(r["d"].isoformat(), []).append({"kind": "calving", "tag": r["tag_no"]})
        for t in _task_rows("t.due_date BETWEEN %s AND %s", (lo, hi)):
            events.setdefault(t["due_date"].isoformat(), []).append(
                {"kind": "done" if t["status"] == "Done" else "task", "what": t["title"],
                 "herd": bool(t["all_animals"]), "tags": t["tags"]})
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
                           lead_blanks=days[0]["date"].weekday() if days else 0, location=panchang.LOCATION)


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


# ---------------------------------------------------------------- dashboard

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
    task_rows = _task_rows("t.status='Open' AND t.due_date<=%s", (t + timedelta(days=7),))
    return render_template("dashboard.html", stats=stats, vacc_due=vacc_due, calvings=calvings,
                           recent_health=recent_health, milk=milk, milk_max=max([x["l"] for x in milk] + [1]),
                           pt=panchang.day_info(t), pmonth=m, task_rows=task_rows)


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, msg=tr("You don't have access to this page.")), 403


@app.errorhandler(413)
def too_large(e):
    return render_template("error.html", code=413, msg=tr("The file is too big: the limit is 5 MB.")), 413


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, msg=tr("Page not found.")), 404


@app.errorhandler(psycopg.errors.DataError)
def bad_value(e):
    # e.g. a date or number typed by hand that PostgreSQL can't read; SQLite used to store it as text
    return render_template("error.html", code=400, msg=tr("Some of the values entered are not valid. "
                                                          "Please go back and check dates and numbers.")), 400


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
