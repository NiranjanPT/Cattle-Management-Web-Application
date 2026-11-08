"""The website's pages, the menu, the Back button and breadcrumbs, lists of 25, and accessibility basics."""
import re
from datetime import date
from html.parser import HTMLParser

import pytest
from werkzeug.security import generate_password_hash

from conftest import Browser, login


@pytest.fixture(scope="module")
def place(fresh_db):
    """Staff, a customer with an order, one animal with a milk record, 30 more animals for the pages of a list."""
    a = fresh_db
    ids = {}
    with a.app.app_context():
        for u, role in [("staff", "staff"), ("customer", "customer")]:
            ids[u] = a.ex("INSERT INTO users (username, password_hash, full_name, role) VALUES (%s,%s,%s,%s)",
                          (u, generate_password_hash(u + "123"), u.title(), role))
        ids["cow"] = a.ex("INSERT INTO cattle (tag_no, name, sex) VALUES ('A-000', 'Gauri', 'Female')")
        for i in range(1, 31):
            a.ex("INSERT INTO cattle (tag_no, sex, status) VALUES (%s, 'Female', 'Active')", (f"B-{i:03}",))
        ids["milk"] = a.ex("INSERT INTO milk_records (cattle_id, date, session, litres) VALUES (%s, %s, 'Morning', 5)",
                           (ids["cow"], date.today()))
        ids["order"] = a.ex("INSERT INTO orders (user_id, total) VALUES (%s, 100)", (ids["customer"],))
    return ids


def text(r):
    return r.get_data(as_text=True)


def back_link(page):
    m = re.search(r'<a class="back" href="([^"]+)"', page)
    return m and m.group(1).replace("&amp;", "&")


def crumbs(page):
    nav = re.search(r'<nav class="crumbs".*?</nav>', page, re.S)
    return [re.sub(r"<[^>]+>|\s+", " ", li).strip() for li in re.findall(r"<li[^>]*>(.*?)</li>", nav.group(0), re.S)] if nav else []


# ------------------------------------------------------------------ the website

@pytest.mark.parametrize("lang", ["en", "mr"])
def test_website_pages_each_have_their_own_address(fresh_db, place, lang):
    b = Browser(fresh_db.app.test_client())
    b.get(f"/lang/{lang}")
    pages = {"/": "", "/about": "About us", "/products": "Products", "/how-to-order": "How to order", "/contact": "Contact"}
    for url, item in pages.items():
        r = b.get(url)
        assert r.status_code == 200, url
        page = text(r)
        current = re.findall(r'<a href="([^"]+)" class="on" aria-current="page">', page)
        assert current == [url], (url, current)  # the menu marks this page, and only this one
    home = text(b.get("/"))
    assert "Our values" not in home and "Today&#39;s panchang" not in home  # moved off the home page
    if lang == "en":
        assert "Our values" in text(b.get("/about")) and "Today&#39;s panchang" in text(b.get("/about"))
        assert "How to order" in text(b.get("/how-to-order")) and "Create an account" in text(b.get("/how-to-order"))
    else:
        assert "आमची मूल्ये" in text(b.get("/about")) and "मागे" in text(b.get("/contact"))


def test_hero_buttons(fresh_db, place):
    visitor = Browser(fresh_db.app.test_client())
    home = text(visitor.get("/"))
    assert 'href="/login?next=/shop">Order fresh dairy' in home and 'href="/products">See our products' in home
    customer = login(fresh_db, "customer", "customer123")
    assert 'href="/shop">Order fresh dairy' in text(customer.get("/home"))


# ------------------------------------------------------------------ Back and breadcrumbs

def test_no_back_button_on_home_login_and_dashboard(fresh_db, place):
    visitor = Browser(fresh_db.app.test_client())
    assert back_link(text(visitor.get("/"))) is None and back_link(text(visitor.get("/login"))) is None
    staff = login(fresh_db, "staff", "staff123")
    assert back_link(text(staff.get("/dashboard"))) is None


def test_back_goes_to_the_page_above(fresh_db, place):
    staff = login(fresh_db, "staff", "staff123")
    cow, rid, oid = place["cow"], place["milk"], place["order"]
    expected = {
        "/cattle": "/dashboard", f"/cattle/{cow}": "/cattle", f"/cattle/{cow}/edit": f"/cattle/{cow}",
        "/cattle/new": "/cattle", "/records/milk": "/dashboard", "/records/milk/new": "/records/milk",
        f"/records/milk/{rid}/edit": "/records/milk", "/tasks/new": "/tasks", "/import?kind=milk": "/records/milk",
        "/import": "/cattle", f"/orders/{oid}": "/admin/orders", "/admin/products/new": "/admin/products",
        "/calendar/nakshatras": "/calendar", "/calendar": "/dashboard", "/profile": "/dashboard", "/cart": "/shop",
        "/shop": "/dashboard", "/no-such-page": "/dashboard",
    }
    got = {url: back_link(text(staff.get(url))) for url in expected}
    assert got == expected
    # sent from an animal's page, a form goes back there
    assert back_link(text(staff.get(f"/records/milk/new?cattle_id={cow}&back=/cattle/{cow}"))) == f"/cattle/{cow}"
    assert back_link(text(staff.get("/records/milk/new?back=//evil.example"))) == "/records/milk"  # not off the site

    customer = login(fresh_db, "customer", "customer123")
    assert back_link(text(customer.get(f"/orders/{oid}"))) == "/orders"
    assert back_link(text(customer.get("/orders"))) == "/shop"
    assert back_link(text(customer.get("/shop"))) == "/home"  # the shop is their first page: back to the website
    visitor = Browser(fresh_db.app.test_client())
    assert back_link(text(visitor.get("/about"))) == "/" and back_link(text(visitor.get("/register"))) == "/login"


def test_breadcrumbs(fresh_db, place):
    staff = login(fresh_db, "staff", "staff123")
    cow = place["cow"]
    assert crumbs(text(staff.get(f"/cattle/{cow}/edit"))) == ["Dashboard", "Cattle", "A-000", "Edit A-000"]
    assert crumbs(text(staff.get("/records/milk/new"))) == ["Dashboard", "Milk production", "Add milk record"]
    staff.get("/lang/mr")
    assert crumbs(text(staff.get(f"/cattle/{cow}"))) == ["डॅशबोर्ड", "जनावरे", "A-000"]
    assert crumbs(text(staff.get("/dashboard"))) == []


# ------------------------------------------------------------------ lists of 25

def test_lists_show_25_rows_a_page(fresh_db, place):
    staff = login(fresh_db, "staff", "staff123")
    first = text(staff.get("/cattle?status=Active"))
    tags = re.findall(r"<b>([AB]-\d{3})</b>", first)
    assert len(tags) == 25 and tags[0] == "A-000" and "Showing 1–25 of 31" in first
    assert 'href="/cattle?status=Active&amp;page=2" rel="next"' in first  # the filter is kept
    second = text(staff.get("/cattle?status=Active&page=2"))
    assert re.findall(r"<b>([AB]-\d{3})</b>", second) == [f"B-{i:03}" for i in range(25, 31)]
    assert "Showing 26–31 of 31" in second and 'aria-current="page" aria-label="Page 2"' in second
    assert "Showing 26–31 of 31" in text(staff.get("/cattle?status=Active&page=99"))  # past the end: the last page
    found = text(staff.get("/cattle?status=&q=b-03"))
    assert re.findall(r"<b>([AB]-\d{3})</b>", found) == ["B-030"] and "Showing 1–1 of 1" in found
    assert 'class="card table-wrap scroll"' in first and "<thead>" in first  # the heading row stays in view


# ------------------------------------------------------------------ menu, dashboard, profile

def menu_links(page):
    nav = re.search(r'<nav class="main".*?</nav>', page, re.S).group(0)
    return re.findall(r'href="([^"]+)"', nav)


def test_menu_shows_only_what_each_role_can_use(fresh_db, place):
    admin, staff = login(fresh_db, "admin", "admin123"), login(fresh_db, "staff", "staff123")
    customer = login(fresh_db, "customer", "customer123")
    assert "/admin/users" in menu_links(text(admin.get("/dashboard")))
    assert "/admin/users" not in menu_links(text(staff.get("/dashboard")))
    assert menu_links(text(customer.get("/shop"))) == ["/shop", "/orders", "/calendar"]
    page = text(staff.get("/records/milk"))
    assert '<summary class="on">Records' in page  # the open section is marked
    assert '<a href="/records/milk" class="on" aria-current="page">Milk production</a>' in page
    assert '<button class="nav-toggle" type="button" aria-expanded="false" aria-controls="menu">' in page
    assert 'id="menu"' in page


def test_customer_orders_has_its_own_menu_item_with_a_green_count(fresh_db, place):
    def badge(page):
        m = re.search(r'href="/admin/orders"[^>]*>Customer orders <span class="badge new"[^>]*>'
                      r'<span aria-hidden="true">(\d+)</span>', page)
        return int(m.group(1)) if m else 0

    a, staff = fresh_db, login(fresh_db, "staff", "staff123")
    before = badge(text(staff.get("/dashboard")))
    with a.app.app_context():
        oid = a.ex("INSERT INTO orders (user_id, total) VALUES (%s, 250)", (place["customer"],))
    page = text(staff.get("/dashboard"))
    assert badge(page) == before + 1  # each new order adds one
    shop_menu = re.search(r"Shop <span aria-hidden.*?</details>", page, re.S).group(0)
    assert "/admin/orders" not in shop_menu and "/shop" in shop_menu  # no longer under Shop
    assert badge(text(staff.get("/cattle"))) == before + 1  # on every page
    staff.post(f"/admin/orders/{oid}/status", {"status": "Confirmed", "expected_delivery": str(date.today())})
    after = text(staff.get(f"/orders/{oid}"))
    assert badge(after) == before  # dealt with
    assert re.search(r'<a href="/admin/orders" class="on" aria-current="page">Customer orders', after)
    customer = text(login(a, "customer", "customer123").get("/shop"))  # customers don't see the count
    assert "badge new" not in customer and "/admin/orders" not in customer


def test_dashboard_quick_actions(fresh_db, place):
    page = text(login(fresh_db, "staff", "staff123").get("/dashboard"))
    quick = re.search(r'<div class="quick.*?</div>\s*</details>\s*</div>', page, re.S).group(0)
    for link in ["/cattle/new", "/records/milk/new", "/import", "/cattle/export.xlsx", "/records/health/export.xlsx"]:
        assert f'href="{link}' in quick, link
    assert "Add cattle" in quick and "Add record" in quick and "Import CSV" in quick and "Reports" in quick
    assert page.count('class="view-all"') >= 4


def test_animal_profile_has_tabs(fresh_db, place):
    page = text(login(fresh_db, "staff", "staff123").get(f"/cattle/{place['cow']}"))
    tabs = re.findall(r'<a role="tab" id="tab-(\w+)" href="#\1" aria-controls="\1"', page)
    assert tabs == ["overview", "health", "vaccinations", "feeding", "breeding", "milk"]
    assert all(f'id="{t}" role="tabpanel" aria-labelledby="tab-{t}"' in page for t in tabs)


# ------------------------------------------------------------------ accessibility basics on every page

class Page(HTMLParser):
    """Collects form fields without a label, images without alt text, and a few landmarks."""

    def __init__(self):
        super().__init__()
        self.labels_for, self.fields, self.open_labels, self.bad_images = set(), [], 0, []
        self.landmarks = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "label":
            self.open_labels += 1
            if a.get("for"):
                self.labels_for.add(a["for"])
        elif tag in ("input", "select", "textarea") and a.get("type") not in ("hidden", "submit", "button"):
            self.fields.append((tag, a.get("name"), a.get("id"), bool(a.get("aria-label")), self.open_labels > 0))
        elif tag == "img" and "alt" not in a:
            self.bad_images.append(a.get("src"))
        if tag in ("header", "nav", "main") or (tag == "a" and a.get("class") == "skip"):
            self.landmarks.add(a.get("class") if tag == "a" else tag)
        if tag == "main" and a.get("id") == "content":
            self.landmarks.add("main#content")

    def handle_endtag(self, tag):
        if tag == "label":
            self.open_labels -= 1

    def unlabelled(self):
        return [(t, n) for t, n, i, aria, inside in self.fields if not (inside or aria or (i and i in self.labels_for))]


def test_every_field_is_labelled_and_every_image_has_alt_text(fresh_db, place):
    from test_app import farm_pages  # its list of farm pages
    admin = login(fresh_db, "admin", "admin123")
    with fresh_db.app.app_context():
        rid = {k: fresh_db.q(f"SELECT id FROM {cfg['table']} ORDER BY id LIMIT 1", one=True)
               for k, cfg in fresh_db.RECORDS.items()}
    f = {"cow": place["cow"], "calf": place["cow"], "product": 0, "order": place["order"],
         "rid": {k: r["id"] for k, r in rid.items() if r}}
    urls = [u for u in farm_pages(f) if "export" not in u and "template" not in u and "/admin/products/0" not in u]
    urls += ["/home", "/about", "/products", "/how-to-order", "/contact", "/shop", "/cart", "/orders", "/profile",
             "/calendar", "/calendar/nakshatras", "/admin/users", "/admin/users/new", f"/admin/users/{place['staff']}/edit"]
    problems = {}
    for url in urls:
        r = admin.get(url)
        assert r.status_code == 200, url
        p = Page()
        p.feed(text(r))
        if p.unlabelled() or p.bad_images or not {"header", "nav", "main", "skip", "main#content"} <= p.landmarks:
            problems[url] = (p.unlabelled(), p.bad_images, p.landmarks)
    assert problems == {}


def test_messages_are_announced(fresh_db, place):
    b = Browser(fresh_db.app.test_client())
    b.get("/login")
    page = text(b.post("/login", {"username": "admin", "password": "wrong"}))
    assert re.search(r'<div class="flashes" role="status" aria-live="polite"><div class="flash error" role="alert">', page)
    assert '<a class="skip" href="#content">Skip to content</a>' in page and '<html lang="en">' in page


def test_calendar_shows_when_agni_moves(fresh_db, place):
    """Every Agni period with its start and end on the 12-hour clock; a time after midnight with its date."""
    import panchang

    def clock(t, day):  # 6:31 AM; 6:32 AM, 08 Nov
        s = f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"
        return s if t.date() == day else f"{s}, {t:%d %b}"

    staff = login(fresh_db, "staff", "staff123")
    months = panchang.lunar_months(2026)
    month = lambda d: next(i for i, mo in enumerate(months) if d in panchang.month_days(mo))  # noqa: E731
    d = date(2026, 11, 7)
    page = text(staff.get(f"/calendar?year=2026&m={month(d)}"))
    for p in panchang.agni_periods(d):  # Prithvi 6:31 AM to 10:48 AM, then Akasha 10:48 AM to 6:32 AM, 08 Nov
        assert f'<span class="t">{clock(p["start"], d)}</span> to <span class="t">{clock(p["end"], d)}</span>' in page
    assert f'<span class="t">{clock(panchang.agni_periods(d)[-1]["start"], d)}</span> to <span class="t">6:' in page  # next sunrise
    assert ":00:00" not in page and not re.search(r"\b(1[3-9]|2[0-3]):[0-5]\d\b", page)  # no 24-hour times
    d = date(2026, 5, 4)
    kind, when = panchang.agni_nakshatra_change(d)
    page = text(staff.get(f"/calendar?year=2026&m={month(d)}"))
    assert kind == "begins" and f'Agni Nakshatra begins: <span class="t">{clock(when, d)}</span>' in page
    assert "PM" in clock(when, d)
    staff.get("/lang/mr")
    page = text(staff.get(f"/calendar?year=2026&m={month(d)}"))
    assert f'अग्नी नक्षत्र सुरू: <span class="t">रात्री {when.hour % 12 or 12}:{when.minute:02d}</span>' in page
    assert "</span> ते <span" in page
