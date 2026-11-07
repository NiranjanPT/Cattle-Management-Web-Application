"""Bulk import from CSV / Excel and the Excel exports, through the web pages, against the test database."""
import csv
import io
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook
from werkzeug.security import generate_password_hash

from conftest import Browser, login
from services import bulk_io

T = date.today()


@pytest.fixture(scope="module")
def herd(fresh_db):
    """A staff member, a customer and three animals."""
    a = fresh_db
    ids = {}
    with a.app.app_context():
        for u, role in [("staff", "staff"), ("customer", "customer")]:
            a.ex("INSERT INTO users (username, password_hash, full_name, role) VALUES (%s,%s,%s,%s)",
                 (u, generate_password_hash(u + "123"), u.title(), role))
        add = "INSERT INTO cattle (tag_no, name, species, sex, status, notes) VALUES (%s,%s,%s,%s,%s,%s)"
        ids["cow"] = a.ex(add, ("GIR-1", "Gauri", "Cow", "Female", "Active", "=1+1"))  # formula-like notes
        ids["buffalo"] = a.ex(add, ("MUR-2", "Kali", "Buffalo", "Female", "Active", None))
        ids["bull"] = a.ex(add, ("KHL-3", "Nandi", "Bull", "Male", "Sold", None))
    return ids


def count(app_module, table, where="TRUE", args=()):
    with app_module.app.app_context():
        return app_module.q(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", args, one=True)["n"]


def csv_file(rows):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue().encode("utf-8")


def xlsx_file(rows):
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def upload(b, kind, name, content):
    return b.post("/import", {"kind": kind, "file": (io.BytesIO(content), name)}, content_type="multipart/form-data")


def payload(page):
    return re.search(r'name="payload" value="([^"]+)"', page).group(1)


def confirm(b, page):
    return b.post("/import/confirm", {"payload": payload(page)})


def text(r):
    return r.get_data(as_text=True)


# ------------------------------------------------------------------ importing

def test_valid_import_saves_nothing_until_confirmed(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")  # staff may import, not only admin
    before = count(fresh_db, "milk_records")
    rows = [["Tag number", "Date", "Session", "Litres", "Notes"],
            ["GIR-1", str(T), "Morning", "6.5", "first"],
            ["gir-1", str(T), "संध्याकाळ", "5", ""],  # tag in another case, the session in Marathi
            ["MUR-2", str(T - timedelta(days=1)), "evening", "7.25", ""]]
    page = text(upload(b, "milk", "milk.csv", csv_file(rows)))
    assert page.count('class="row-ok"') == 3 and "row-bad" not in page
    assert "3 ready to import" in page and "Confirm import" in page
    assert count(fresh_db, "milk_records") == before  # only a preview so far

    done = text(confirm(b, page))
    assert "3 imported, 0 skipped" in done
    assert count(fresh_db, "milk_records") == before + 3
    with fresh_db.app.app_context():
        saved = fresh_db.q("SELECT session, litres, notes FROM milk_records WHERE cattle_id=%s AND date=%s ORDER BY id",
                           (herd["cow"], T))
    assert [(r["session"], r["litres"], r["notes"]) for r in saved][-2:] == [
        ("Morning", Decimal("6.50"), "first"), ("Evening", Decimal("5.00"), None)]

    again = confirm(b, page)  # the same preview sent twice
    assert "already been imported" in text(b.get(again.headers["Location"]))
    assert count(fresh_db, "milk_records") == before + 3


def test_cattle_import_from_excel_links_mothers(fresh_db, herd):
    b = login(fresh_db, "admin", "admin123")
    rows = [["Tag number", "Name", "Type", "Sex", "Date of birth", "Weight (kg)", "Mother (dam)", "Status"],
            ["NEW-CALF", "Chhoti", "Calf", "Female", date(2026, 3, 1), 60, "NEW-MOM", ""],  # mother on a later row
            ["NEW-MOM", "Moti", "", "Female", date(2020, 5, 10), 390.5, "MUR-2", ""],      # mother in the register
            ["NEW-BULL", "Raja", "Bull", "male", "2021-01-02", "", "", "Sold"]]
    page = text(upload(b, "cattle", "herd.xlsx", xlsx_file(rows)))
    assert page.count('class="row-ok"') == 3, page
    assert "3 imported, 0 skipped" in text(confirm(b, page))
    with fresh_db.app.app_context():
        got = {r["tag_no"]: r for r in fresh_db.q("SELECT * FROM cattle WHERE tag_no LIKE 'NEW-%%'")}
    assert got["NEW-CALF"]["dam_id"] == got["NEW-MOM"]["id"]
    assert got["NEW-MOM"]["dam_id"] == herd["buffalo"]
    assert got["NEW-MOM"]["species"] == "Cow" and got["NEW-MOM"]["status"] == "Active"  # blanks: the defaults
    assert got["NEW-MOM"]["source"] == "Born on farm" and got["NEW-MOM"]["weight_kg"] == Decimal("390.50")
    assert got["NEW-CALF"]["dob"] == date(2026, 3, 1) and got["NEW-BULL"]["sex"] == "Male"


def test_file_with_bad_rows(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    before = count(fresh_db, "health_records")
    rows = [["Tag number", "Date", "Type", "Diagnosis / symptoms", "Cost (₹)"],
            ["GIR-1", str(T), "Check-up", "All well", "200"],                # good
            ["GIR-1", "07/11/2026", "Illness", "Fever", "100"],              # date not YYYY-MM-DD
            ["GIR-1", str(T), "Illness", "Fever", "-50"],                    # negative cost
            ["GIR-1", str(T), "Haircut", "", ""],                            # not a choice
            ["GIR-1", str(T), "Illness", '=HYPERLINK("http://x","y")', ""],  # a formula
            ["GIR-1", "", "Illness", "", ""],                                # required date missing
            ["", "", "", "", ""]]                                            # blank: left out
    page = text(upload(b, "health", "health.csv", csv_file(rows)))
    assert page.count('class="row-ok"') == 1 and page.count('class="row-bad"') == 5
    assert "1 ready to import" in page and "5 with problems, will be skipped" in page
    for reason in ["Date: 07/11/2026 is not a date written YYYY-MM-DD.", "Cost (₹): -50 is not a number (0 or more).",
                   "Type: Haircut is not one of the choices (Check-up, Illness,", "Date: required.",
                   "which a spreadsheet would run as a formula"]:
        assert reason in page

    done = text(confirm(b, page))
    assert "1 imported, 5 skipped" in done and count(fresh_db, "health_records") == before + 1

    r = b.post("/import/skipped", {"payload": payload(done)})
    assert r.mimetype == "text/csv" and f"health-records-skipped-{T}.csv" in r.headers["Content-Disposition"]
    got = list(csv.reader(io.StringIO(r.get_data().decode("utf-8-sig"))))
    assert got[0][0] == "Row" and got[0][-1] == "Problems" and len(got) == 6
    assert [line[0] for line in got[1:]] == ["3", "4", "5", "6", "7"]
    assert got[2][got[0].index("Cost (₹)")] == "'-50"  # sent back formula-safe
    assert got[4][got[0].index("Diagnosis / symptoms")].startswith("'=HYPERLINK")
    assert "not a date written YYYY-MM-DD" in got[1][-1]

    # the skipped file, fixed, goes back in: its Row and Problems columns are left out
    fixed = [line[1:-1] for line in got]
    fixed[0] = [label for label in fresh_db.IMPORT_SPECS["health"].labels()]
    fixed[1][1] = str(T)
    page = text(upload(b, "health", "fixed.csv", csv_file(fixed)))
    assert page.count('class="row-ok"') == 1  # the date fixed; the rest still wrong


def test_a_file_sent_as_the_wrong_type_is_recognised(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    template = b.get("/import/template/cattle.xlsx").get_data()  # the cattle template, Health records chosen
    page = text(upload(b, "health", "cattle-register-template.xlsx", template))
    assert "This file has the columns of Cattle register, not Health records, so it is checked as Cattle register." in page
    assert "Check the import: Cattle register" in page and 'class="row-ok"' in page
    # a file that fits no import still says what is missing
    page = text(upload(b, "health", "x.csv", csv_file([["Tag number", "Type"], ["GIR-1", "Illness"]])))
    assert "Required column missing: Date." in page and 'name="payload"' not in page


def test_unknown_tag(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    rows = [["Tag number", "Date given", "Vaccine"], ["NOPE-9", str(T), "Brucellosis"]]
    page = text(upload(b, "vaccinations", "v.csv", csv_file(rows)))
    assert "Tag NOPE-9: no animal with this tag number in the register." in page
    assert re.search(r"<button disabled>\s*Confirm import", page)  # nothing to import
    assert "0 imported, 1 skipped" in text(confirm(b, page))
    assert count(fresh_db, "vaccinations") == 0


def test_new_animals_need_new_unique_tags(fresh_db, herd):
    b = login(fresh_db, "admin", "admin123")
    rows = [["Tag number", "Sex", "Mother (dam)"],
            ["DUP-1", "Female", ""], ["dup-1", "Female", ""],  # twice in the file
            ["GIR-1", "Female", ""],                           # already in the register
            ["KID-1", "Male", "KHL-3"],                        # the "mother" is a bull
            ["KID-2", "Male", "NOBODY"],                       # no such mother
            ["KID-3", "Male", "DUP-1"],                        # which DUP-1?
            ["KID-4", "Male", "BAD-MOM"],                      # the mother's own row is skipped
            ["BAD-MOM", "Female", "NOBODY"],
            ["OK-1", "Female", "GIR-1"]]                       # fine: the mother is in the register
    page = text(upload(b, "cattle", "new.csv", csv_file(rows)))
    assert page.count('class="row-bad"') == 8 and page.count('class="row-ok"') == 1
    for reason in ["Tag DUP-1 appears more than once in this file (rows 2, 3).",
                   "Tag GIR-1: an animal with this tag number is already in the register.",
                   "Mother (dam): KHL-3 is not a female.",
                   "Mother (dam): no animal with tag NOBODY in the register or in this file.",
                   "Mother (dam): DUP-1 appears more than once in this file.",
                   "Mother (dam): the row for BAD-MOM has problems, so it won&#39;t be imported."]:
        assert reason in page


def test_problems_with_the_whole_file(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    no_litres = csv_file([["Tag number", "Date", "Session"], ["GIR-1", str(T), "Morning"]])
    page = text(upload(b, "milk", "m.csv", no_litres))
    assert "Required column missing: Litres." in page and 'name="payload"' not in page  # no preview
    assert "Choose a .csv or .xlsx file." in text(upload(b, "milk", "m.txt", b"tag_no\nGIR-1\n"))
    assert "The file is empty." in text(upload(b, "milk", "m.csv", b""))
    assert "not a valid Excel" in text(upload(b, "milk", "m.xlsx", b"not a zip at all"))
    too_big = upload(b, "milk", "big.csv", b"x" * (5 * 1024 * 1024 + 10))
    assert too_big.status_code == 413 and "the limit is 5 MB" in text(too_big)
    formula = xlsx_file([["Tag number", "Date", "Session", "Litres"], ["GIR-1", str(T), "Morning", "=2*3"]])
    assert "Litres: =2*3 starts with =" in text(upload(b, "milk", "f.xlsx", formula))


def test_the_database_refusing_a_row_saves_none(fresh_db, herd):
    a = fresh_db
    b = login(a, "staff", "staff123")
    before = count(a, "milk_records")
    rows = [["Tag number", "Date", "Session", "Litres"]] + [["GIR-1", str(T), "Morning", v] for v in ("1", "7.77", "2")]
    page = text(upload(b, "milk", "m.csv", csv_file(rows)))
    with a.app.app_context():  # the second row will fail inside the database
        a.ex("CREATE FUNCTION refuse() RETURNS trigger AS $$ BEGIN IF NEW.litres = 7.77 THEN"
             " RAISE EXCEPTION 'refused'; END IF; RETURN NEW; END $$ LANGUAGE plpgsql")
        a.ex("CREATE TRIGGER refuse BEFORE INSERT ON milk_records FOR EACH ROW EXECUTE FUNCTION refuse()")
    try:
        r = confirm(b, page)
        assert "Nothing was imported" in text(b.get(r.headers["Location"]))
        assert count(a, "milk_records") == before  # the first row was rolled back too
    finally:
        with a.app.app_context():
            a.ex("DROP TRIGGER refuse ON milk_records")
            a.ex("DROP FUNCTION refuse()")
    assert "3 imported" in text(confirm(b, page))  # the same preview works once the database accepts it


def test_a_changed_preview_is_refused(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    page = text(upload(b, "milk", "m.csv", csv_file([["Tag number", "Date", "Session", "Litres"],
                                                     ["GIR-1", str(T), "Morning", "3"]])))
    token = payload(page)
    r = b.post("/import/confirm", {"payload": token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb")})
    assert "This preview has expired" in text(b.get(r.headers["Location"]))


def test_breeding_import_fills_expected_calving(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    served = T - timedelta(days=10)
    rows = [["Tag number", "Service date", "Method"], ["MUR-2", str(served), "Artificial insemination"]]
    assert "1 imported" in text(confirm(b, text(upload(b, "breeding", "b.csv", csv_file(rows)))))
    with fresh_db.app.app_context():
        r = fresh_db.q("SELECT * FROM breedings WHERE cattle_id=%s", (herd["buffalo"],), one=True)
    assert r["expected_calving"] == served + timedelta(days=310) and r["pregnancy"] == "Pending"


def test_import_in_marathi(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    b.get("/lang/mr")
    page = text(upload(b, "milk", "m.csv", csv_file([["टॅग क्रमांक", "दिनांक", "वेळ", "लिटर"],
                                                     ["GIR-1", str(T), "सकाळ", "abc"]])))
    assert "लिटर: abc ही संख्या नाही (0 किंवा जास्त)." in page and "आयात निश्चित करा" in page


# ------------------------------------------------------------------ who may use it

def test_customers_are_blocked(fresh_db, herd):
    b = login(fresh_db, "customer", "customer123")
    for url in ["/import", "/import?kind=milk", "/import/template/milk.xlsx", "/cattle/export.xlsx",
                "/records/milk/export.xlsx"]:
        assert b.get(url).status_code == 403, url
    data = csv_file([["Tag number", "Date", "Session", "Litres"], ["GIR-1", str(T), "Morning", "1"]])
    assert upload(b, "milk", "m.csv", data).status_code == 403
    assert b.post("/import/confirm", {"payload": "x"}).status_code == 403
    assert b.post("/import/skipped", {"payload": "x"}).status_code == 403
    visitor = Browser(fresh_db.app.test_client())
    assert "/login" in visitor.get("/import").headers["Location"]
    assert "/login" in visitor.get("/cattle/export.xlsx").headers["Location"]


def test_buttons_on_the_pages(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    register = text(b.get("/cattle?status=&q=gir"))
    assert "Import CSV" in register and "/cattle/export.xlsx?status=&amp;q=gir" in register
    milk = text(b.get(f"/records/milk?cattle_id={herd['cow']}&from={T}"))
    assert "/import?kind=milk" in milk and f"/records/milk/export.xlsx?cattle_id={herd['cow']}&amp;from={T}" in milk
    page = text(b.get("/import?kind=milk"))
    assert all(f"/import/template/{k}.xlsx" in page for k in fresh_db.IMPORT_SPECS)


# ------------------------------------------------------------------ Excel files

def open_xlsx(r):
    assert r.status_code == 200 and r.mimetype == bulk_io.XLSX
    return load_workbook(io.BytesIO(r.get_data()))


def test_cattle_export_opens_with_filters(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    r = b.get("/cattle/export.xlsx?status=Active&q=gir")
    assert f'filename=cattle-register-{T}.xlsx' in r.headers["Content-Disposition"]
    ws = open_xlsx(r).active
    header = [c.value for c in ws[1]]
    assert header[:5] == ["Tag number", "Name", "Type", "Breed", "Sex"] and "Mother (dam)" in header
    assert all(c.font.b for c in ws[1]) and ws.freeze_panes == "A2"
    tags = [row[0] for row in ws.iter_rows(min_row=2, values_only=True)]
    assert tags == ["GIR-1"]  # active and matching "gir"; KHL-3 is sold
    notes = ws.cell(row=2, column=header.index("Notes") + 1)
    assert notes.value == "=1+1" and notes.data_type == "s"  # shown as text, never run as a formula
    assert ws.column_dimensions["A"].width >= len("Tag number") + 2
    sold = open_xlsx(b.get("/cattle/export.xlsx?status=Sold")).active
    assert [row[0] for row in sold.iter_rows(min_row=2, values_only=True)] == ["KHL-3", "NEW-BULL"]


def test_records_export_applies_filters(fresh_db, herd):
    b = login(fresh_db, "staff", "staff123")
    r = b.get(f"/records/milk/export.xlsx?cattle_id={herd['cow']}&from={T}&to={T}")
    assert f"filename=milk-production-{T}.xlsx" in r.headers["Content-Disposition"]
    ws = open_xlsx(r).active
    header = [c.value for c in ws[1]]
    assert header == ["Tag number", "Name", "Date", "Session", "Litres", "Notes"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    expected = count(fresh_db, "milk_records", "cattle_id=%s AND date=%s", (herd["cow"], T))
    assert len(rows) == expected > 0 and all(row[0] == "GIR-1" for row in rows)
    assert ws.cell(row=2, column=3).is_date and ws.cell(row=2, column=3).number_format == "yyyy-mm-dd"
    assert isinstance(rows[0][4], (int, float))  # litres stay numbers
    everything = open_xlsx(b.get("/records/milk/export.xlsx")).active
    assert everything.max_row - 1 == count(fresh_db, "milk_records")


@pytest.mark.parametrize("kind", ["cattle", "health", "vaccinations", "feeding", "breeding", "milk"])
def test_templates(fresh_db, herd, kind):
    b = login(fresh_db, "staff", "staff123")
    r = b.get(f"/import/template/{kind}.xlsx")
    spec = fresh_db.IMPORT_SPECS[kind]
    assert f"filename={spec.slug}-template.xlsx" in r.headers["Content-Disposition"]
    wb = open_xlsx(r)
    assert wb.sheetnames == [spec.title, "Instructions"]
    data, help_ws = wb.worksheets
    assert [c.value for c in data[1]] == spec.labels() and data.freeze_panes == "A2"
    assert data.max_column == len(spec.columns) and any(v not in (None, "") for v in next(
        data.iter_rows(min_row=2, max_row=2, values_only=True)))  # one example row
    explained = {row[0]: row[2] for row in help_ws.iter_rows(min_row=2, values_only=True) if row[0] and row[2]}
    assert set(spec.labels()) <= set(explained)
    date_cols = [c.label for c in spec.columns if c.type == "date"]
    assert date_cols and all("YYYY-MM-DD" in explained[label] for label in date_cols)
    # the template, uploaded unchanged, is understood: no column missing or unknown
    page = text(upload(b, kind, "template.xlsx", r.get_data()))
    assert "Check the import" in page and "Columns not used" not in page


# ------------------------------------------------------------------ the module on its own

def test_reading_csv_from_excel_in_other_regions():
    header, rows = bulk_io.read_table("x.csv", "Tag number;Notes\nA-1;Café\n".encode("cp1252"))
    assert header == ["Tag number", "Notes"] and rows == [(2, ["A-1", "Café"])]


def test_safe_cell():
    assert [bulk_io.safe_cell(v) for v in ["=1+1", "+91", "-5", "@x", "ok", None, 5]] == [
        "'=1+1", "'+91", "'-5", "'@x", "ok", "", "5"]


# ------------------------------------------------------------------ after a big import

def test_big_herd_totals_show_on_the_dashboard_and_record_pages(fresh_db, herd):
    a = fresh_db
    with a.app.app_context():  # 600 days of milk for one cow, and a 120.4 L day today
        a.ex("INSERT INTO milk_records (cattle_id, date, session, litres)"
             " SELECT %s, CURRENT_DATE - g, 'Morning', 5 FROM generate_series(1, 600) g", (herd["cow"],))
        a.ex("INSERT INTO milk_records (cattle_id, date, session, litres) VALUES (%s, CURRENT_DATE, 'Evening', 120.4)",
             (herd["buffalo"],))
    b = login(a, "staff", "staff123")
    page = text(b.get("/records/milk"))
    total = count(a, "milk_records")
    assert f"Showing 1–25 of {total:,}" in page and page.count("/edit") == 25  # 25 a page
    last = -(-total // 25)
    assert f"Showing {(last - 1) * 25 + 1}–{total} of {total:,}" in text(b.get(f"/records/milk?page={last}"))
    assert f"{count(a, 'health_records')} records" in text(b.get("/records/health"))  # all shown: just the count
    dash = text(b.get("/dashboard"))
    with a.app.app_context():
        day = a.q("SELECT SUM(litres) AS l FROM milk_records WHERE date=CURRENT_DATE", one=True)["l"]
    assert day > 100 and '<section class="card wide" aria-labelledby="milk-title">' in dash  # the chart's full width
    # whole litres over the bar once a day passes 100, so the labels fit; the exact figure in its tooltip
    assert f': {a.num(day)} L"><span class="val">{round(day)}</span>' in dash
