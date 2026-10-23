"""Scheduled tasks and how they show up in, and are completed through, the other modules."""
from datetime import date, timedelta

import pytest
from werkzeug.security import generate_password_hash

import panchang
from conftest import login

T = date.today()


@pytest.fixture(scope="module")
def farm(fresh_db):
    a, ids = fresh_db, {}
    with a.app.app_context():
        for u, role in [("staff", "staff"), ("customer", "customer")]:
            ids[u] = a.ex("INSERT INTO users (username, password_hash, full_name, role) VALUES (%s,%s,%s,%s)",
                          (u, generate_password_hash(u + "123"), u.title() + " Person", role))
        cattle = "INSERT INTO cattle (tag_no, name, species, sex) VALUES (%s,%s,%s,%s)"
        ids["cow"] = a.ex(cattle, ("GIR-1", "Kamdhenu", "Cow", "Female"))
        ids["buffalo"] = a.ex(cattle, ("MUR-2", "Kali", "Buffalo", "Female"))
        ids["bull"] = a.ex(cattle, ("KHL-3", "Nandi", "Bull", "Male"))
    return ids


def one(a, sql, args=()):
    with a.app.app_context():
        return a.q(sql, args, one=True)


def rows(a, sql, args=()):
    with a.app.app_context():
        return a.q(sql, args)


def new_task(b, **form):
    data = {"kind": "other", "due_date": T.isoformat(), "repeat": "none", **form}
    return b.post("/tasks/new", data)


# ------------------------------------------------------------------ repeats

@pytest.mark.parametrize("due,repeat,after,expected", [
    (date(2026, 1, 31), "monthly", date(2026, 1, 1), date(2026, 2, 28)),   # end of month is clamped
    (date(2026, 3, 10), "yearly", date(2026, 3, 1), date(2027, 3, 10)),
    (date(2026, 3, 10), "weekly", date(2026, 3, 1), date(2026, 3, 17)),    # done early: still one step on
    (date(2026, 3, 10), "weekly", date(2026, 3, 30), date(2026, 3, 31)),   # done late: next after today
    (date(2026, 3, 10), "daily", date(2026, 3, 20), date(2026, 3, 21)),
    (date(2026, 3, 10), "none", date(2026, 3, 1), None),
])
def test_next_occurrence(fresh_db, due, repeat, after, expected):
    assert fresh_db.next_occurrence(due, repeat, after) == expected


# ------------------------------------------------------------------ create and show everywhere

def test_task_shows_in_every_module(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    r = new_task(staff, kind="vaccinations", item="FMD (Foot and Mouth)", title="FMD booster",
                 cattle_id=[str(farm["cow"]), str(farm["buffalo"])], repeat="monthly",
                 assigned_to=str(farm["staff"]), notes="Bring cold box")
    assert r.status_code == 302
    t = one(fresh_db, "SELECT * FROM tasks WHERE title='FMD booster'")
    assert (t["kind"], t["item"], t["due_date"], t["repeat"], t["assigned_to"]) == (
        "vaccinations", "FMD (Foot and Mouth)", T, "monthly", farm["staff"])
    linked = {x["cattle_id"] for x in rows(fresh_db, "SELECT cattle_id FROM task_animals WHERE task_id=%s", (t["id"],))}
    assert linked == {farm["cow"], farm["buffalo"]}

    assert "FMD booster" in staff.get("/tasks").get_data(as_text=True)
    assert "FMD booster" in staff.get("/tasks?mine=1").get_data(as_text=True)
    dash = staff.get("/dashboard").get_data(as_text=True)
    assert "Tasks this week" in dash and "FMD booster" in dash and "GIR-1, MUR-2" in dash
    assert "Task: FMD booster (GIR-1, MUR-2)" in staff.get("/calendar").get_data(as_text=True)
    assert "FMD booster" in staff.get(f"/cattle/{farm['cow']}").get_data(as_text=True)
    assert "FMD booster" not in staff.get(f"/cattle/{farm['bull']}").get_data(as_text=True)
    assert 'title="due today or overdue">' in staff.get("/cattle").get_data(as_text=True)  # menu count


def test_whole_herd_task_shows_on_every_animal(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    new_task(staff, kind="health", item="Deworming", title="Herd deworming", all_animals="1",
             due_date=(T + timedelta(days=3)).isoformat())
    for cid in (farm["cow"], farm["bull"]):
        assert "Herd deworming" in staff.get(f"/cattle/{cid}").get_data(as_text=True)
    assert "Whole herd" in staff.get("/tasks").get_data(as_text=True)


def test_title_made_from_kind_and_item(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    new_task(staff, kind="feeding", item="Silage", due_date=(T + timedelta(days=20)).isoformat())
    assert one(fresh_db, "SELECT title FROM tasks WHERE item='Silage'")["title"] == "Feeding: Silage"


def test_bad_input_is_ignored_not_crashing(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    r = new_task(staff, title="Odd", kind="nonsense", repeat="hourly", cattle_id=["999999", "abc"],
                 assigned_to=str(farm["customer"]), due_date=(T + timedelta(days=21)).isoformat())
    assert r.status_code == 302
    t = one(fresh_db, "SELECT * FROM tasks WHERE title='Odd'")
    assert (t["kind"], t["repeat"], t["assigned_to"]) == ("other", "none", None)  # customers can't be assigned
    assert rows(fresh_db, "SELECT 1 FROM task_animals WHERE task_id=%s", (t["id"],)) == []
    r = new_task(staff, title="No date", due_date="")
    assert r.status_code == 200 and "Please choose a date" in r.get_data(as_text=True)


# ------------------------------------------------------------------ completing through the record modules

def test_done_opens_record_form_and_completes_task(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    t = one(fresh_db, "SELECT * FROM tasks WHERE title='FMD booster'")
    r = staff.post(f"/tasks/{t['id']}/done", {"back": "/tasks"})  # record kinds go to the record form
    assert r.status_code == 302 and f"/records/vaccinations/new?task={t['id']}" in r.headers["Location"]

    html = staff.get(f"/records/vaccinations/new?task={t['id']}").get_data(as_text=True)
    assert "Completing task FMD booster" in html
    assert f'<option value="{farm["cow"]}" selected>' in html and f'<option value="{farm["buffalo"]}" selected>' in html
    assert f'<option value="{farm["bull"]}" >' in html
    assert '<option value="FMD (Foot and Mouth)" selected>FMD (Foot and Mouth)</option>' in html

    r = staff.post("/records/vaccinations/new", {"task": str(t["id"]), "back": "/tasks",
                                                 "cattle_id": [str(farm["cow"]), str(farm["buffalo"])],
                                                 "date_given": T.isoformat(), "vaccine": "FMD (Foot and Mouth)",
                                                 "dose": "2 ml"}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert "Saved 2 vaccinations" in page and "Task done: FMD booster" in page
    nxt = fresh_db.next_occurrence(T, "monthly", T)
    assert f"Next one scheduled for {nxt:%d %b %Y}" in page
    assert len(rows(fresh_db, "SELECT 1 FROM vaccinations WHERE date_given=%s AND vaccine='FMD (Foot and Mouth)'",
                    (T,))) == 2
    done = one(fresh_db, "SELECT status, done_by FROM tasks WHERE id=%s", (t["id"],))
    assert done == {"status": "Done", "done_by": farm["staff"]}
    again = one(fresh_db, "SELECT * FROM tasks WHERE title='FMD booster' AND status='Open'")
    assert again["due_date"] == nxt and again["assigned_to"] == farm["staff"] and again["notes"] == "Bring cold box"
    linked = {x["cattle_id"] for x in rows(fresh_db, "SELECT cattle_id FROM task_animals WHERE task_id=%s",
                                           (again["id"],))}
    assert linked == {farm["cow"], farm["buffalo"]}


def test_task_is_completed_only_once(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    t = one(fresh_db, "SELECT * FROM tasks WHERE title='FMD booster' AND status='Done'")
    before = len(rows(fresh_db, "SELECT 1 FROM tasks WHERE title='FMD booster'"))
    staff.post("/records/vaccinations/new", {"task": str(t["id"]), "cattle_id": str(farm["cow"]),
                                             "date_given": T.isoformat(), "vaccine": "FMD (Foot and Mouth)"})
    assert len(rows(fresh_db, "SELECT 1 FROM tasks WHERE title='FMD booster'")) == before  # no extra repeat


def test_whole_herd_prefills_all_animals(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    t = one(fresh_db, "SELECT id FROM tasks WHERE title='Herd deworming'")
    html = staff.get(f"/records/health/new?task={t['id']}").get_data(as_text=True)
    for cid in (farm["cow"], farm["buffalo"], farm["bull"]):
        assert f'<option value="{cid}" selected>' in html
    assert '<option value="Deworming" selected>Deworming</option>' in html


def test_task_of_other_kind_is_ignored_by_record_form(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    t = one(fresh_db, "SELECT id FROM tasks WHERE title='Herd deworming'")
    assert "Completing task" not in staff.get(f"/records/milk/new?task={t['id']}").get_data(as_text=True)


# ------------------------------------------------------------------ havan and Agni

def _days_with(good):
    return next(T + timedelta(days=i) for i in range(1, 60)
                if panchang.day_info(T + timedelta(days=i))["agni"]["good_for_havan"] == good)


def test_havan_on_a_bad_agni_day_warns(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    bad = _days_with(False)
    page = new_task(staff, kind="havan", title="Gau puja havan", due_date=bad.isoformat()).headers
    r = staff.get(page["Location"]).get_data(as_text=True)
    assert "havan not advised" in r and "Next day with Agni on earth" in r
    good = _days_with(True)
    r = staff.get(new_task(staff, kind="havan", title="Good havan", due_date=good.isoformat())
                  .headers["Location"]).get_data(as_text=True)
    assert "Task saved: Good havan" in r and "Next day with Agni on earth" not in r  # no warning this time
    assert "Agni on earth: good for havan" in staff.get("/tasks").get_data(as_text=True)


def test_havan_done_directly(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    t = one(fresh_db, "SELECT id FROM tasks WHERE title='Good havan'")
    r = staff.post(f"/tasks/{t['id']}/done", {"back": "/tasks"}, follow_redirects=True)
    assert "Task done: Good havan." in r.get_data(as_text=True)
    assert one(fresh_db, "SELECT status FROM tasks WHERE id=%s", (t["id"],))["status"] == "Done"
    assert len(rows(fresh_db, "SELECT 1 FROM tasks WHERE title='Good havan'")) == 1  # does not repeat


def test_calendar_shows_agni_and_schedule_links(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    html = staff.get("/calendar").get_data(as_text=True)
    assert "🔥 Agni:" in html and "Sunrise " in html and "sunrise in Sangli" in html
    assert f'/tasks/new?date={T.isoformat()}' in html
    may = staff.get("/calendar?year=2026&m=1").get_data(as_text=True)  # Vaishakha 2026 covers May
    assert "Agni Nakshatra (Kathiri)" in may
    customer = login(fresh_db, "customer", "customer123")
    html = customer.get("/calendar").get_data(as_text=True)
    assert "🔥 Agni:" in html and "/tasks/new" not in html and "Task:" not in html


# ------------------------------------------------------------------ edit, delete, permissions

def test_edit_task(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    t = one(fresh_db, "SELECT id FROM tasks WHERE item='Silage'")
    assert staff.get(f"/tasks/{t['id']}/edit").status_code == 200
    staff.post(f"/tasks/{t['id']}/edit", {"kind": "feeding", "item": "Silage", "title": "Silage for heifers",
                                          "due_date": (T + timedelta(days=22)).isoformat(), "repeat": "weekly",
                                          "cattle_id": str(farm["cow"])})
    t2 = one(fresh_db, "SELECT * FROM tasks WHERE id=%s", (t["id"],))
    assert (t2["title"], t2["repeat"], t2["due_date"]) == ("Silage for heifers", "weekly", T + timedelta(days=22))
    assert [x["cattle_id"] for x in rows(fresh_db, "SELECT cattle_id FROM task_animals WHERE task_id=%s",
                                         (t["id"],))] == [farm["cow"]]


def test_deleting_an_animal_keeps_the_task(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    with fresh_db.app.app_context():
        cid = fresh_db.ex("INSERT INTO cattle (tag_no, sex) VALUES ('TMP-9', 'Female')")
    new_task(staff, title="Check TMP-9", cattle_id=str(cid), due_date=(T + timedelta(days=23)).isoformat())
    login(fresh_db, "admin", "admin123").post(f"/cattle/{cid}/delete")
    t = one(fresh_db, "SELECT id FROM tasks WHERE title='Check TMP-9'")
    assert t and rows(fresh_db, "SELECT 1 FROM task_animals WHERE task_id=%s", (t["id"],)) == []


def test_delete_task(fresh_db, farm):
    staff = login(fresh_db, "staff", "staff123")
    t = one(fresh_db, "SELECT id FROM tasks WHERE title='Check TMP-9'")
    r = staff.post(f"/tasks/{t['id']}/delete", {"back": "/tasks"}, follow_redirects=True)
    assert "Task deleted" in r.get_data(as_text=True)
    assert one(fresh_db, "SELECT 1 AS x FROM tasks WHERE id=%s", (t["id"],)) is None


def test_customers_cannot_use_tasks(fresh_db, farm):
    customer = login(fresh_db, "customer", "customer123")
    t = one(fresh_db, "SELECT id FROM tasks LIMIT 1")
    for url in ("/tasks", "/tasks/new", f"/tasks/{t['id']}/edit"):
        assert customer.get(url).status_code == 403
    for url in (f"/tasks/{t['id']}/done", f"/tasks/{t['id']}/delete"):
        assert customer.post(url).status_code == 403
    assert "Tasks" not in customer.get("/shop").get_data(as_text=True).split("<main>")[0]
