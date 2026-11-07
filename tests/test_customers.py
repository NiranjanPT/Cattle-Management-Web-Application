"""Customers' address, city and pincode: sign-up, profile, and the admin's filters and sorting."""
import re

import pytest
from werkzeug.security import generate_password_hash

from conftest import Browser, login


@pytest.fixture(scope="module")
def town(fresh_db):
    """Staff and five customers in four towns, two with orders."""
    a, ids = fresh_db, {}
    with a.app.app_context():
        ids["staff"] = a.ex("INSERT INTO users (username, password_hash, full_name, role) VALUES ('staff', %s, 'Staff', 'staff')",
                            (generate_password_hash("staff123"),))
        for u, name, city, pin in [("asha", "Asha Kulkarni", "Pune", "411038"), ("bala", "Bala Joshi", "Pune", "411001"),
                                   ("chitra", "Chitra More", "Sangli", "416416"), ("deepak", "Deepak Pawar", "Miraj", "416410"),
                                   ("esha", "Esha Shah", None, None)]:
            ids[u] = a.ex("INSERT INTO users (username, password_hash, full_name, role, address, city, pincode)"
                          " VALUES (%s,%s,%s,'customer','Main road',%s,%s)", (u, generate_password_hash(u + "123"), name, city, pin))
        for u in ("asha", "chitra"):
            a.ex("INSERT INTO orders (user_id, total, address) VALUES (%s, 100, 'Main road')", (ids[u],))
    return ids


def text(r):
    return r.get_data(as_text=True)


def names(page):
    """Customer names in the order the Users list shows them."""
    return re.findall(r"<tr><td>([A-Z][a-z]+ [A-Z][a-z]+)</td>", page)


def sign_up(b, **fields):
    form = {"full_name": "Kiran Patil", "username": "kiran", "password": "kiran123", "phone": "98220 11111",
            "address": "Plot 4, Vishrambag", "city": "Sangli", "pincode": "416416"}
    return b.post("/register", {**form, **fields})


def user(a, username):
    with a.app.app_context():
        return a.q("SELECT * FROM users WHERE username=%s", (username,), one=True)


# ------------------------------------------------------------------ signing up and the profile

def test_sign_up_takes_address_city_and_pincode(fresh_db, town):
    b = Browser(fresh_db.app.test_client())
    b.get("/register")
    form = text(b.get("/register"))
    for field in ('name="address"', 'name="city"', 'name="pincode"'):
        assert re.search(field + r'[^>]*required', form), field
    r = sign_up(b, username="navi", city="  navi   mumbai ", pincode="400 703")
    assert r.status_code == 302
    u = user(fresh_db, "navi")
    assert (u["address"], u["city"], u["pincode"], u["role"]) == ("Plot 4, Vishrambag", "Navi Mumbai", "400703", "customer")


@pytest.mark.parametrize("fields, message", [
    ({"city": ""}, "Please enter your address, city and pincode."),
    ({"pincode": ""}, "Please enter your address, city and pincode."),
    ({"address": "  "}, "Please enter your address, city and pincode."),
    ({"pincode": "12345"}, "The pincode must be 6 digits, like 416416."),
    ({"pincode": "041641"}, "The pincode must be 6 digits, like 416416."),
    ({"pincode": "41641a"}, "The pincode must be 6 digits, like 416416."),
])
def test_sign_up_refuses_a_missing_or_wrong_address(fresh_db, town, fields, message):
    b = Browser(fresh_db.app.test_client())
    b.get("/register")
    page = text(sign_up(b, username="wrong", **fields))
    assert message in page and user(fresh_db, "wrong") is None
    assert 'value="Kiran Patil"' in page  # what was typed stays
    assert ("Plot 4, Vishrambag" in page) == ("address" not in fields)


def test_a_customer_keeps_city_and_pincode_on_the_profile(fresh_db, town):
    b = login(fresh_db, "esha", "esha123")
    profile = {"full_name": "Esha Shah", "address": "Main road"}
    r = b.post("/profile", profile, follow_redirects=True)
    assert "Please enter your address, city and pincode." in text(r) and user(fresh_db, "esha")["city"] is None
    b.post("/profile", {**profile, "city": "kolhapur", "pincode": "416 003"})
    assert (user(fresh_db, "esha")["city"], user(fresh_db, "esha")["pincode"]) == ("Kolhapur", "416003")
    staff = login(fresh_db, "staff", "staff123")  # staff may leave them blank
    assert "Profile saved." in text(staff.post("/profile", {"full_name": "Staff"}, follow_redirects=True))


# ------------------------------------------------------------------ the admin's filters and sorting

def test_admin_filters_customers_by_city_and_pincode(fresh_db, town):
    admin = login(fresh_db, "admin", "admin123")
    page = text(admin.get("/admin/users?role=customer&city=pune"))  # any case
    assert sorted(names(page)) == ["Asha Kulkarni", "Bala Joshi"] and "2 accounts" in page
    assert re.search(r'<option value="Pune" selected>Pune \(2\)</option>', page)  # the city list counts them
    assert sorted(names(text(admin.get("/admin/users?role=customer&pincode=416")))) == [  # a whole district
        "Chitra More", "Deepak Pawar", "Esha Shah"]
    assert names(text(admin.get("/admin/users?role=customer&pincode=416%20416"))) == ["Chitra More"]
    assert names(text(admin.get("/admin/users?role=customer&q=sangli"))) == ["Chitra More"]  # search finds the city
    # a city in the list is a link to everyone there
    assert 'href="/admin/users?role=customer&amp;city=Sangli"' in text(admin.get("/admin/users?role=customer"))


def test_admin_sorts_customers(fresh_db, town):
    admin = login(fresh_db, "admin", "admin123")
    by = lambda sort: names(text(admin.get(f"/admin/users?role=customer&sort={sort}")))  # noqa: E731
    # by now: Esha moved to Kolhapur 416003 (profile test) and Kiran Patil signed up in Navi Mumbai 400703
    assert by("city") == ["Esha Shah", "Deepak Pawar", "Kiran Patil", "Asha Kulkarni", "Bala Joshi", "Chitra More"]
    assert by("pincode") == ["Kiran Patil", "Bala Joshi", "Asha Kulkarni", "Esha Shah", "Deepak Pawar", "Chitra More"]
    assert by("name") == sorted(by("name"))
    assert by("new")[0] == "Kiran Patil" and by("old")[-1] == "Kiran Patil"  # the newest account
    # a filter and a sort together, kept by the role tabs and the page links
    page = text(admin.get("/admin/users?role=customer&city=Pune&sort=name"))
    assert names(page) == ["Asha Kulkarni", "Bala Joshi"]
    assert 'href="/admin/users?city=Pune&amp;sort=name"' in page  # the All tab keeps them
    staff = login(fresh_db, "staff", "staff123")
    assert staff.get("/admin/users?city=Pune").status_code == 403  # only admins


def test_orders_by_the_customers_city(fresh_db, town):
    staff = login(fresh_db, "staff", "staff123")
    page = text(staff.get("/admin/orders?city=Sangli"))
    assert "Chitra More" in page and "Asha Kulkarni" not in page and "416416" in page
    assert "Asha Kulkarni" in text(staff.get("/admin/orders?pincode=411"))
    assert "No orders found." in text(staff.get("/admin/orders?city=Miraj"))


def test_the_cart_fills_in_the_delivery_address(fresh_db, town):
    b = login(fresh_db, "chitra", "chitra123")
    with fresh_db.app.app_context():
        pid = fresh_db.ex("INSERT INTO products (name, category, price, unit, stock) VALUES ('Ghee', 'Ghee', 500, 'jar', 5)")
    b.post(f"/cart/add/{pid}", {"qty": "1"})
    assert "Main road\nSangli - 416416</textarea>" in text(b.get("/cart"))
