"""English / Marathi: every interface text has a Marathi version, the switch works, and the website shows."""
import ast
import glob
import os
import re
import string
from datetime import date
from xml.etree import ElementTree

import pytest

import i18n
import panchang
from conftest import ROOT, Browser, login


def _template_texts():
    """Every literal passed to _() in the templates, plus the label lists the templates loop over."""
    found = set()
    for path in glob.glob(os.path.join(ROOT, "templates", "*.html")):
        s = open(path, encoding="utf-8").read()
        found |= set(re.findall(r"_\(\s*'([^']*)'", s)) | set(re.findall(r'_\(\s*"([^"]*)"', s))
        # {% for x in ['Active', ...] %}, [('health', 'Health'), ...] and the cattle form's opts([...])
        lists = re.findall(r"{%\s*for [\w, ]+ in (\[.*?\])\s*%}", s) + re.findall(r"opts\((\[.*?\])", s)
        for lst in lists:
            for item in ast.literal_eval(lst):
                found.add(item[-1] if isinstance(item, tuple) else item)
    return found - {""}


def _code_texts():
    s = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    return {"".join(re.findall(r'"([^"]*)"', m)) for m in re.findall(r'tr\(((?:"[^"]*"\s*)+)', s)}


def _data_texts(app):
    """Values kept in English in the code or the database and translated for display."""
    out = {*app.SPECIES, *app.BREEDS, *app.PRODUCT_CATEGORIES, *app.ORDER_STATUSES,
           *app.TASK_KINDS.values(), *app.REPEATS.values(),
           "Admin", "Staff", "Customer", "Shukla", "Krishna", "Purnima", "Amavasya", "Sangli",
           i18n.BRAND, i18n.COMPANY, i18n.TAGLINE}
    for cfg in app.RECORDS.values():
        out |= {cfg["title"], cfg["one"], cfg["one"] + "s"}
        for _, label, _, options in cfg["fields"]:
            out |= {label, label.replace(" (blank = auto)", ""), *(options or [])}
    out |= {*panchang.TITHIS, *panchang.WEEKDAYS, *panchang.RASHIS}
    out |= {n[i] for n in panchang.NAKSHATRAS for i in (2, 3, 4)}
    out |= {a[3] for a in panchang.AGNIVAS.values()}
    return out


def test_every_text_has_marathi(app_module):
    texts = _template_texts() | _code_texts() | _data_texts(app_module)
    assert sorted(t for t in texts if t not in i18n.MR) == []


def test_placeholders_match():
    fields = lambda s: sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)  # noqa: E731
    assert [k for k, v in i18n.MR.items() if fields(k) != fields(v)] == []


def test_marathi_dates():
    d = date(2026, 10, 23)  # a Friday
    assert i18n.date_text(d, "%a %d %b %Y", "mr") == "शुक्र 23 ऑक्टो 2026"
    assert i18n.date_text(d, "%a %d %b %Y", "en") == "Fri 23 Oct 2026"


def test_website_for_visitors(fresh_db):
    b = Browser(fresh_db.app.test_client())
    page = b.get("/").get_data(as_text=True)
    assert "VrushaA2 Dairy Tech" in page and "From sanskar to prosperity" in page
    assert "brand/logo-full.svg" in page and 'href="/login"' in page and 'href="/register"' in page
    assert "Gaushala" not in page
    assert b.get("/static/brand/logo-mark.svg").status_code == 200


@pytest.mark.parametrize("name", ["logo-mark.svg", "logo-full.svg"])
def test_logo_is_valid_svg(name):
    # browsers show nothing for an SVG that is not well-formed XML (e.g. an undeclared namespace prefix)
    root = ElementTree.parse(os.path.join(ROOT, "static", "brand", name)).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg" and root.get("viewBox")


def test_switch_to_marathi_and_back(fresh_db):
    b = Browser(fresh_db.app.test_client())
    r = b.get("/lang/mr?next=/login")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    page = b.get("/login").get_data(as_text=True)
    assert '<html lang="mr">' in page and "वापरकर्ता नाव" in page and "वृषाA2 डेरीटेक" in page
    home = b.get("/").get_data(as_text=True)
    assert "संस्कारातून समृद्धीकडे" in home and "आमची मूल्ये" in home
    b.get("/lang/en")
    assert "Username" in b.get("/login").get_data(as_text=True)


@pytest.mark.parametrize("nxt", ["//evil.example", "https://evil.example", "/\\evil.example"])
def test_switch_only_returns_to_this_site(fresh_db, nxt):
    r = Browser(fresh_db.app.test_client()).get("/lang/mr", query_string={"next": nxt})
    assert r.headers["Location"] == "/"


def test_unknown_language_is_ignored(fresh_db):
    b = Browser(fresh_db.app.test_client())
    b.get("/lang/xx")
    assert '<html lang="en">' in b.get("/login").get_data(as_text=True)


# ------------------------------------------------------------------ text people type

@pytest.fixture
def fake_translator(fresh_db, monkeypatch):
    """Stands in for the MyMemory service: 'Gauri' -> 'मरा:Gauri', and records what it was asked."""
    asked = []

    def translate(texts, lang, email=None):
        asked.append(sorted(texts))
        return {t: "मरा:" + t for t in texts}
    monkeypatch.setattr(fresh_db, "DATA_TRANSLATION", True)
    monkeypatch.setattr(fresh_db, "_translation_paused_until", 0.0)
    monkeypatch.setattr(fresh_db.i18n, "machine_translate", translate)
    with fresh_db.app.app_context():
        fresh_db.ex("DELETE FROM text_translations")
    return asked


def test_typed_names_are_translated_once_and_remembered(fresh_db, fake_translator):
    with fresh_db.app.app_context():
        cid = fresh_db.ex("INSERT INTO cattle (tag_no, name, species, sex, breed, color, notes)"
                          " VALUES ('TR-1', 'Gauri', 'Cow', 'Female', 'Gir', 'Brown', 'Calm cow')")
    admin = login(fresh_db, "admin", "admin123")
    assert "Gauri" in admin.get("/cattle").get_data(as_text=True)  # English: nothing translated or asked
    assert fake_translator == []
    admin.get("/lang/mr")
    page = admin.get(f"/cattle/{cid}").get_data(as_text=True)
    assert "मरा:Gauri" in page and "मरा:Brown" in page and "मरा:Calm cow" in page
    assert "गीर" in page  # a breed from the list comes from i18n.MR, not the service
    assert not any("Gir" in batch for batch in fake_translator)
    with fresh_db.app.app_context():
        saved = {r["source"]: r["translated"] for r in fresh_db.q("SELECT * FROM text_translations")}
    assert saved["Gauri"] == "मरा:Gauri"
    asked = len(fake_translator)
    assert "मरा:Gauri" in admin.get("/cattle").get_data(as_text=True)
    assert len(fake_translator) == asked  # read from the database the second time


def test_unreachable_service_leaves_english_and_pauses(fresh_db, fake_translator, monkeypatch):
    calls = []
    monkeypatch.setattr(fresh_db.i18n, "machine_translate", lambda texts, lang, email=None: calls.append(1) or {})
    with fresh_db.app.app_context():
        fresh_db.ex("INSERT INTO cattle (tag_no, name, species, sex) VALUES ('TR-2', 'Shyama', 'Cow', 'Female')")
    admin = login(fresh_db, "admin", "admin123")
    admin.get("/lang/mr")
    assert "Shyama" in admin.get("/cattle").get_data(as_text=True)
    admin.get("/cattle")
    assert len(calls) == 1  # paused after the failure instead of slowing every page


def test_marathi_text_typed_by_people_is_left_alone(fresh_db, fake_translator):
    with fresh_db.app.app_context():
        fresh_db.ex("INSERT INTO cattle (tag_no, name, species, sex) VALUES ('TR-3', 'गंगा', 'Cow', 'Female')")
    admin = login(fresh_db, "admin", "admin123")
    admin.get("/lang/mr")
    admin.get("/cattle")
    assert not any("गंगा" in batch for batch in fake_translator)


def test_machine_translate_drops_the_limit_notice(monkeypatch):
    class Fake:
        def __init__(self, **kw):
            pass

        def translate(self, text):
            return "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY" if text == "b" else "&quot;क&quot;"
    monkeypatch.setattr(i18n, "MyMemoryTranslator", Fake)
    assert i18n.machine_translate(["a", "b", "x" * 600]) == {"a": '"क"'}  # notice dropped, too-long text not sent


def test_messages_follow_the_language(fresh_db):
    b = Browser(fresh_db.app.test_client())
    b.get("/lang/mr")
    b.get("/login")
    page = b.post("/login", {"username": "admin", "password": "wrong"}).get_data(as_text=True)
    assert "वापरकर्ता नाव किंवा पासवर्ड चुकीचा आहे" in page
    admin = login(fresh_db, "admin", "admin123")
    admin.get("/lang/mr")
    dash = admin.get("/dashboard").get_data(as_text=True)
    assert "गोठा डॅशबोर्ड" in dash and "बाहेर पडा" in dash
