"""End-to-end tests of the web app against the test database (see conftest.py)."""
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import psycopg
import pytest
from werkzeug.security import generate_password_hash

from conftest import login

T = date.today()
REPORTS = ["herd", "milk_month", "milk_animal", "costs", "feed", "sales", "breeding"]


@pytest.fixture(scope="module")
def farm(fresh_db):
    """Users, animals, one record of every kind, a product and an order."""
    a = fresh_db
    ids = {}
    with a.app.app_context():
        ex, q = a.ex, a.q
        for u, role in [("staff", "staff"), ("customer", "customer"), ("customer2", "customer")]:
            ids[u] = ex("INSERT INTO users (username, password_hash, full_name, role) VALUES (%s,%s,%s,%s)",
                        (u, generate_password_hash(u + "123"), u.title(), role))
        ids["admin"] = q("SELECT id FROM users WHERE username='admin'", one=True)["id"]
        cattle = "INSERT INTO cattle (tag_no, name, species, breed, sex, dob, weight_kg) VALUES (%s,%s,%s,%s,%s,%s,%s)"
        ids["cow"] = ex(cattle, ("GIR-T1", "Kamdhenu", "Cow", "Gir", "Female", T - timedelta(days=800), 410.5))
        ids["buffalo"] = ex(cattle, ("MUR-T2", "Kali", "Buffalo", "Murrah", "Female", T - timedelta(days=1500), 520))
        ids["bull"] = ex(cattle, ("KHL-T3", "Nandi", "Bull", "Khillar", "Male", T - timedelta(days=2000), 600))
        ids["calf"] = ex("INSERT INTO cattle (tag_no, species, sex, dob, dam_id) VALUES ('GIR-T4','Calf','Female',%s,%s)",
                         (T - timedelta(days=60), ids["cow"]))
        rid = {}
        rid["health"] = ex("INSERT INTO health_records (cattle_id, date, kind, diagnosis, cost) VALUES (%s,%s,%s,%s,%s)",
                           (ids["cow"], T - timedelta(days=3), "Illness", "Mild fever", 650))
        # one overdue (dashboard), one due today (calendar shows it in the current month)
        rid["vaccinations"] = ex("INSERT INTO vaccinations (cattle_id, date_given, vaccine, next_due, cost)"
                                 " VALUES (%s,%s,%s,%s,%s)", (ids["cow"], T - timedelta(days=180),
                                                              "FMD (Foot and Mouth)", T - timedelta(days=1), 40))
        ex("INSERT INTO vaccinations (cattle_id, date_given, vaccine, next_due, cost) VALUES (%s,%s,%s,%s,%s)",
           (ids["cow"], T - timedelta(days=300), "HS (Haemorrhagic Septicaemia)", T, 30))
        rid["feeding"] = ex("INSERT INTO feedings (cattle_id, date, feed_type, quantity_kg, cost) VALUES (%s,%s,%s,%s,%s)",
                            (ids["cow"], T, "Green fodder", 25.5, 50))
        rid["breeding"] = ex("INSERT INTO breedings (cattle_id, date, method, pregnancy, expected_calving)"
                             " VALUES (%s,%s,%s,%s,%s)", (ids["buffalo"], T - timedelta(days=100),
                                                          "Natural service", "Confirmed", T + timedelta(days=210)))
        rid["milk"] = ex("INSERT INTO milk_records (cattle_id, date, session, litres) VALUES (%s,%s,'Morning',4.5)",
                         (ids["cow"], T))
        ex("INSERT INTO milk_records (cattle_id, date, session, litres) VALUES (%s,%s,'Evening',4.5)", (ids["cow"], T))
        ids["product"] = ex("INSERT INTO products (name, category, price, unit, stock) VALUES (%s,%s,%s,%s,%s)",
                            ("Test ghee", "Ghee", Decimal("450.50"), "jar", 10))
        ids["order"] = ex("INSERT INTO orders (user_id, total, created_at) VALUES (%s, 901, %s)",
                          (ids["customer"], f"{T - timedelta(days=1)} 08:15:00"))
        ex("INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (%s,%s,'Test ghee',450.50,2)",
           (ids["order"], ids["product"]))
    ids["rid"] = rid
    return ids


def stock(app_module, pid):
    with app_module.app.app_context():
        return app_module.q("SELECT stock FROM products WHERE id=%s", (pid,), one=True)["stock"]


def farm_pages(f):
    pages = ["/dashboard", "/cattle", "/cattle?status=&q=gir", "/cattle/new", f"/cattle/{f['cow']}",
             f"/cattle/{f['cow']}/edit", f"/cattle/{f['calf']}", "/admin/orders", "/admin/orders?status=Pending",
             "/admin/products", "/admin/products/new", f"/admin/products/{f['product']}/edit", "/reports",
             f"/reports?from={T - timedelta(days=400)}&to={T}"]
    for kind, rid in f["rid"].items():
        pages += [f"/records/{kind}", f"/records/{kind}/new", f"/records/{kind}/{rid}/edit",
                  f"/records/{kind}?cattle_id={f['cow']}&from={T - timedelta(days=30)}&to={T}"]
    pages += [f"/reports/{name}.csv" for name in REPORTS]
    return pages


def everyone_pages(f):
    return ["/shop", "/shop?category=Ghee", "/cart", "/orders", "/profile", "/calendar", "/calendar?year=2025&m=3",
            "/calendar/nakshatras"]


ADMIN_ONLY = ["/admin/users", "/admin/users?role=staff", "/admin/users/new"]


def assert_pages(b, urls, status):
    bad = [(u, r.status_code) for u in urls if (r := b.get(u)).status_code != status]
    assert not bad, f"expected {status}: {bad}"


# ------------------------------------------------------------------ every page, every role

def test_wrong_password_is_refused(fresh_db, farm):
    from conftest import Browser
    b = Browser(fresh_db.app.test_client())
    b.get("/login")
    r = b.post("/login", {"username": "admin", "password": "nope"})
    assert r.status_code == 200 and "Wrong username or password" in r.get_data(as_text=True)


def test_idle_login_expires(fresh_db, farm, monkeypatch):
    b = login(fresh_db, "admin", "admin123")
    r = b.get("/dashboard")
    expires = parsedate_to_datetime(re.search(r"Expires=([^;]+)", r.headers["Set-Cookie"]).group(1))
    # an expiry date, so the browser drops it; renewed on every page, an hour ahead
    assert timedelta(minutes=59) < expires - datetime.now(expires.tzinfo) <= timedelta(hours=1)
    # the server also refuses a cookie older than the limit
    monkeypatch.setitem(fresh_db.app.config, "PERMANENT_SESSION_LIFETIME", timedelta(seconds=1))
    time.sleep(2.1)
    assert "/login" in b.get("/dashboard").headers["Location"]


def test_admin_sees_every_page(fresh_db, farm):
    b = login(fresh_db, "admin", "admin123")
    assert b.get("/").headers["Location"].endswith("/dashboard")
    assert_pages(b, farm_pages(farm) + everyone_pages(farm) + ADMIN_ONLY
                 + [f"/admin/users/{farm['staff']}/edit", f"/orders/{farm['order']}"], 200)


def test_staff_sees_farm_pages_but_not_users(fresh_db, farm):
    b = login(fresh_db, "staff", "staff123")
    assert_pages(b, farm_pages(farm) + everyone_pages(farm) + [f"/orders/{farm['order']}"], 200)
    assert_pages(b, ADMIN_ONLY + [f"/admin/users/{farm['staff']}/edit"], 403)


def test_customer_sees_shop_pages_only(fresh_db, farm):
    b = login(fresh_db, "customer", "customer123")
    assert b.get("/").headers["Location"].endswith("/shop")
    b.post(f"/cart/add/{farm['product']}", {"qty": "2"})  # so /cart adds up Decimal prices
    assert_pages(b, everyone_pages(farm) + [f"/orders/{farm['order']}"], 200)
    assert "₹901.00" in b.get("/cart").get_data(as_text=True)
    assert_pages(b, farm_pages(farm)[:6] + ADMIN_ONLY + ["/records/milk"], 403)
    other = login(fresh_db, "customer2", "customer2123")
    assert other.get(f"/orders/{farm['order']}").status_code == 403


# ------------------------------------------------------------------ dates and Decimals render as before

def test_dates_and_numbers_render(fresh_db, farm):
    b = login(fresh_db, "admin", "admin123")
    dash = b.get("/dashboard").get_data(as_text=True)
    assert "9 L</div>" in dash  # milk today: 4.5 + 4.5
    assert f"{T:%d %b}: 9 L" in dash  # 14-day chart reads the date-keyed totals
    assert f"Overdue {T - timedelta(days=1)}" in dash
    vacc = b.get("/records/vaccinations").get_data(as_text=True)
    assert f'<span class="badge bad">{T - timedelta(days=1)}</span>' in vacc
    assert f'<span class="badge ">{T}</span>' in vacc
    assert "Vaccine due: GIR-T1 (HS" in b.get("/calendar").get_data(as_text=True)
    profile = b.get(f"/cattle/{farm['cow']}").get_data(as_text=True)
    age = fresh_db.age_text(T - timedelta(days=800))
    assert age.startswith("2 y") and f"({age})" in profile  # age_text given a DATE from the database
    assert "410.5 kg" in profile
    assert "₹450.50" in b.get("/shop").get_data(as_text=True)
    users = b.get("/admin/users").get_data(as_text=True)
    assert f"<td>{datetime.now(ZoneInfo('Asia/Kolkata')).date()}</td>" in users
    orders = b.get("/admin/orders").get_data(as_text=True)
    assert f"<td>{T - timedelta(days=1)} 08:15:00</td>" in orders  # farm time, no +05:30
    assert "+0" not in orders


def test_cattle_search_ignores_case(fresh_db, farm):
    b = login(fresh_db, "staff", "staff123")
    html = b.get("/cattle?status=&q=gir").get_data(as_text=True)
    assert "GIR-T1" in html and "MUR-T2" not in html


def test_csv_export_has_values(fresh_db, farm):
    b = login(fresh_db, "staff", "staff123")
    lines = b.get("/reports/milk_animal.csv").get_data(as_text=True).splitlines()
    assert lines[0] == "tag_no,name,litres,days,per_day"
    assert lines[1] == "GIR-T1,Kamdhenu,9.00,1,9.00"
    sales = b.get(f"/reports/sales.csv?from={T - timedelta(days=7)}&to={T}").get_data(as_text=True).splitlines()
    assert "Test ghee" in sales[1]


def test_bad_values_do_not_crash(fresh_db, farm):
    b = login(fresh_db, "staff", "staff123")
    assert b.get("/records/milk?from=yesterday&to=2026-13-45").status_code == 200
    r = b.post("/cattle/new", {"tag_no": "BAD-1", "sex": "Female", "species": "Cow", "weight_kg": "heavy"})
    assert r.status_code == 400 and "not valid" in r.get_data(as_text=True)


def _drop(conn):
    """Cut the TCP connection under libpq, as when Neon drops it. A duplicate handle is shut down,
    so libpq's own handle stays valid and nothing is closed twice."""
    import socket
    dup = socket.fromfd(conn.pgconn.socket, socket.AF_INET, socket.SOCK_STREAM)
    dup.shutdown(socket.SHUT_RDWR)
    dup.close()


def test_read_survives_a_dropped_connection(fresh_db, farm):
    from flask import g
    with fresh_db.app.app_context():
        db = fresh_db.get_db()
        _drop(db)
        assert fresh_db.q("SELECT tag_no FROM cattle WHERE id=%s", (farm["cow"],), one=True)["tag_no"] == "GIR-T1"
        assert g.db is not db and not g.db.closed
        second = g.db
        with pytest.raises(psycopg.OperationalError):  # inside a transaction a failure is raised, never repeated
            with second.transaction():
                _drop(second)
                fresh_db.q("SELECT 1")
        assert g.db is second
    stats = fresh_db.pool.get_stats()
    assert stats["pool_size"] <= 5 and stats.get("requests_waiting", 0) == 0


# ------------------------------------------------------------------ the main flows

def test_add_animal(fresh_db, farm):
    b = login(fresh_db, "staff", "staff123")
    r = b.post("/cattle/new", {"tag_no": "SAH-N1", "name": "Lakshmi", "species": "Cow", "breed": "Sahiwal",
                               "sex": "Female", "dob": "2023-04-02", "weight_kg": "380.25",
                               "purchase_price": "45000", "dam_id": str(farm["cow"]), "status": "Active"})
    assert r.status_code == 302
    with fresh_db.app.app_context():
        c = fresh_db.q("SELECT * FROM cattle WHERE tag_no='SAH-N1'", one=True)
    assert r.headers["Location"].endswith(f"/cattle/{c['id']}")
    assert c["dob"] == date(2023, 4, 2) and c["weight_kg"] == Decimal("380.25") and c["dam_id"] == farm["cow"]
    assert "Lakshmi" in b.get(r.headers["Location"]).get_data(as_text=True)
    dup = b.post("/cattle/new", {"tag_no": "SAH-N1", "sex": "Female", "species": "Cow"})
    assert "already has that tag number" in dup.get_data(as_text=True)


def test_breeding_fills_expected_calving(fresh_db, farm):
    b = login(fresh_db, "staff", "staff123")
    served = date(2026, 1, 10)
    r = b.post("/records/breeding/new", {"cattle_id": [str(farm["cow"]), str(farm["buffalo"])],
                                         "date": served.isoformat(), "method": "Artificial insemination",
                                         "pregnancy": "Pending"})
    assert r.status_code == 302
    with fresh_db.app.app_context():
        got = {row["cattle_id"]: row["expected_calving"] for row in
               fresh_db.q("SELECT cattle_id, expected_calving FROM breedings WHERE date=%s", (served,))}
    assert got == {farm["cow"]: served + timedelta(days=283), farm["buffalo"]: served + timedelta(days=310)}
    b.post("/records/breeding/new", {"cattle_id": str(farm["cow"]), "date": "2026-02-01", "pregnancy": "Pending",
                                     "method": "Natural service", "expected_calving": "2026-11-05"})
    with fresh_db.app.app_context():
        kept = fresh_db.q("SELECT expected_calving FROM breedings WHERE date='2026-02-01'", one=True)
    assert kept["expected_calving"] == date(2026, 11, 5)


def test_group_milk_record(fresh_db, farm):
    b = login(fresh_db, "staff", "staff123")
    day = T - timedelta(days=2)
    herd = [farm["cow"], farm["buffalo"], farm["calf"]]
    r = b.post("/records/milk/new", {"cattle_id": [str(i) for i in herd], "date": day.isoformat(),
                                     "session": "Morning", "litres": "3.75"})
    assert r.status_code == 302
    with fresh_db.app.app_context():
        rows = fresh_db.q("SELECT cattle_id, litres FROM milk_records WHERE date=%s ORDER BY cattle_id", (day,))
    assert [(x["cattle_id"], x["litres"]) for x in rows] == [(i, Decimal("3.75")) for i in sorted(herd)]
    assert "Saved 3 milk records" in b.get("/records/milk").get_data(as_text=True)


def test_order_then_cancel_returns_stock(fresh_db, farm):
    pid = farm["product"]
    before = stock(fresh_db, pid)
    shopper = login(fresh_db, "customer", "customer123")
    shopper.post(f"/cart/add/{pid}", {"qty": "3"})
    r = shopper.post("/cart", {f"qty_{pid}": "3", "action": "checkout", "phone": "98220 00000",
                               "address": "Kothrud, Pune"})
    assert r.status_code == 302
    oid = int(re.search(r"/orders/(\d+)$", r.headers["Location"]).group(1))
    assert stock(fresh_db, pid) == before - 3
    with fresh_db.app.app_context():
        o = fresh_db.q("SELECT total, status FROM orders WHERE id=%s", (oid,), one=True)
        items = fresh_db.q("SELECT qty, price FROM order_items WHERE order_id=%s", (oid,))
    assert o["total"] == Decimal("1351.50") and o["status"] == "Pending"
    assert [(i["qty"], i["price"]) for i in items] == [(Decimal("3"), Decimal("450.50"))]
    assert "₹1,351.50" in shopper.get(f"/orders/{oid}").get_data(as_text=True)

    staff = login(fresh_db, "staff", "staff123")
    staff.post(f"/admin/orders/{oid}/status", {"status": "Cancelled"})
    assert stock(fresh_db, pid) == before
    staff.post(f"/admin/orders/{oid}/status", {"status": "Cancelled"})  # again: no double return
    assert stock(fresh_db, pid) == before
    staff.post(f"/admin/orders/{oid}/status", {"status": "Confirmed"})  # un-cancel takes it again
    assert stock(fresh_db, pid) == before - 3
    staff.post(f"/admin/orders/{oid}/status", {"status": "Cancelled"})
    assert stock(fresh_db, pid) == before


def test_checkout_refuses_more_than_stock(fresh_db, farm):
    pid = farm["product"]
    before = stock(fresh_db, pid)
    with fresh_db.app.app_context():
        orders_before = fresh_db.q("SELECT count(*) AS n FROM orders", one=True)["n"]
    shopper = login(fresh_db, "customer", "customer123")
    shopper.post(f"/cart/add/{pid}", {"qty": "99"})
    r = shopper.post("/cart", {f"qty_{pid}": "99", "action": "checkout", "phone": "98220 00000",
                               "address": "Kothrud, Pune"}, follow_redirects=True)
    assert "Not enough stock for: Test ghee" in r.get_data(as_text=True)
    assert stock(fresh_db, pid) == before
    with fresh_db.app.app_context():
        assert fresh_db.q("SELECT count(*) AS n FROM orders", one=True)["n"] == orders_before


def cart_qty(b, pid):
    with b.client.session_transaction() as s:
        return s.get("cart", {}).get(str(pid))


def test_remove_button_empties_the_line(fresh_db, farm):
    pid = farm["product"]
    shopper = login(fresh_db, "customer", "customer123")
    shopper.post(f"/cart/add/{pid}", {"qty": "2"})
    html = shopper.get("/cart").get_data(as_text=True)
    assert f'formaction="/cart/remove/{pid}" formnovalidate' in html
    assert 'value="update" formnovalidate' in html
    r = shopper.post(f"/cart/remove/{pid}", follow_redirects=True)  # no phone or address needed
    assert "Removed Test ghee from your cart" in r.get_data(as_text=True)
    assert cart_qty(shopper, pid) is None
    assert "Your cart is empty" in shopper.get("/cart").get_data(as_text=True)


def test_update_to_zero_removes(fresh_db, farm):
    pid = farm["product"]
    shopper = login(fresh_db, "customer", "customer123")
    shopper.post(f"/cart/add/{pid}", {"qty": "2"})
    shopper.post("/cart", {f"qty_{pid}": "0", "action": "update"})
    assert cart_qty(shopper, pid) is None


@pytest.mark.parametrize("phone,address", [("", "Kothrud, Pune"), ("98220 00000", "   "), ("12345", "Kothrud, Pune"),
                                           ("call me maybe", "Kothrud, Pune")])
def test_checkout_needs_phone_and_address(fresh_db, farm, phone, address):
    pid = farm["product"]
    before = stock(fresh_db, pid)
    with fresh_db.app.app_context():
        orders_before = fresh_db.q("SELECT count(*) AS n FROM orders", one=True)["n"]
    shopper = login(fresh_db, "customer", "customer123")
    shopper.post(f"/cart/add/{pid}", {"qty": "1"})
    r = shopper.post("/cart", {f"qty_{pid}": "1", "action": "checkout", "phone": phone, "address": address,
                               "notes": "after 6 pm"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Please enter a phone number" in html
    assert "after 6 pm" in html  # what was typed is kept
    assert stock(fresh_db, pid) == before and cart_qty(shopper, pid) == 1
    with fresh_db.app.app_context():
        assert fresh_db.q("SELECT count(*) AS n FROM orders", one=True)["n"] == orders_before


def test_valid_phone_formats(fresh_db):
    ok = ["9822000000", "98220 00000", "+91 98220 00000", "+91-98220-00000", "(020) 2543 1234"]
    bad = ["", "12345", "98220-ABCDE", "+91 98220 00000 00000"]
    assert all(fresh_db._valid_phone(p) for p in ok)
    assert not any(fresh_db._valid_phone(p) for p in bad)


def test_remove_animal_from_register(fresh_db, farm):
    with fresh_db.app.app_context():
        cid = fresh_db.ex("INSERT INTO cattle (tag_no, name, sex) VALUES ('DEL-1', 'Shyama', 'Female')")
        fresh_db.ex("INSERT INTO milk_records (cattle_id, date, session, litres) VALUES (%s, %s, 'Morning', 3)", (cid, T))
    staff = login(fresh_db, "staff", "staff123")
    assert "Remove" not in staff.get("/cattle").get_data(as_text=True)  # only admins may delete
    assert staff.post(f"/cattle/{cid}/delete").status_code == 403

    admin = login(fresh_db, "admin", "admin123")
    html = admin.get("/cattle?status=Active&q=del").get_data(as_text=True)
    assert f'action="/cattle/{cid}/delete"' in html and 'aria-label="Remove DEL-1"' in html
    assert "Remove DEL-1 (Shyama) and delete all its" in html.replace("&#34;", '"')
    r = admin.post(f"/cattle/{cid}/delete", {"back": "/cattle?status=Active&q=del"})
    assert r.status_code == 302 and r.headers["Location"] == "/cattle?status=Active&q=del"
    assert "Animal and all its records deleted" in admin.get(r.headers["Location"]).get_data(as_text=True)
    with fresh_db.app.app_context():
        assert fresh_db.q("SELECT 1 FROM cattle WHERE id=%s", (cid,)) == []
        assert fresh_db.q("SELECT 1 FROM milk_records WHERE cattle_id=%s", (cid,)) == []  # ON DELETE CASCADE


def test_remove_ignores_outside_back_link(fresh_db, farm):
    with fresh_db.app.app_context():
        cid = fresh_db.ex("INSERT INTO cattle (tag_no, sex) VALUES ('DEL-2', 'Male')")
    admin = login(fresh_db, "admin", "admin123")
    r = admin.post(f"/cattle/{cid}/delete", {"back": "//evil.example/cattle"})
    assert r.headers["Location"] == "/cattle"
