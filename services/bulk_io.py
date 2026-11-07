"""Bulk import from CSV or Excel, and Excel export, for the cattle register and the record pages.

Nothing here knows about Flask: app.py passes in the column specs, the animals already in the register,
a database connection and its translation function. Problems come back as Msg (English text with values),
which the app shows in the visitor's language.

An import, step by step:
    header, rows = read_table(filename, data)          # .csv or .xlsx, checked for size and shape
    found, ignored, problems = match_columns(spec, header)
    results = validate(spec, rows, found, animals)      # every row checked; nothing saved yet
    save(conn, spec, results, gestation)                # the valid rows, all in one transaction
"""
import csv
import io
import os
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from psycopg.rows import tuple_row

import i18n

MAX_BYTES = 5 * 1024 * 1024          # the largest file accepted
MAX_ROWS = 5000                      # rows per file
MAX_COLUMNS = 60
MAX_UNZIPPED = 60 * 1024 * 1024      # an .xlsx is a zip: refuse one that unpacks to more than this
MAX_TEXT = 2000                      # characters in one cell
MAX_NUMBER = Decimal("99999999.99")  # fits every NUMERIC column (10 or 12 digits, 2 after the point)
# A spreadsheet runs a cell that starts with one of these as a formula ("CSV injection")
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class Msg(NamedTuple):
    """A message in English with its values, e.g. Msg("Tag {tag}: ...", {"tag": "GIR-1"})."""
    text: str
    values: dict


def msg(text, **values):
    return Msg(text, values)


class FileProblem(Exception):
    """The file as a whole can't be imported (wrong type, too big, a required column missing...)."""

    def __init__(self, *msgs):
        super().__init__(msgs[0].text)
        self.msgs = list(msgs)


# ---------------------------------------------------------------- what each import expects

@dataclass(frozen=True)
class Column:
    name: str             # the database column (mother_tag: the dam, by tag number)
    label: str            # English heading, the same as on the forms
    type: str             # newtag | tag | mother | text | date | number | choice
    required: bool = False
    options: tuple = ()   # choice: the allowed values. A blank optional choice means the first one
    example: object = ""  # the template's example row; a timedelta means today plus that
    examples: tuple = ()  # text: suggestions shown in the instructions (breeds)


@dataclass(frozen=True)
class Spec:
    kind: str             # cattle, or a key of app.RECORDS
    title: str
    table: str
    slug: str             # start of the file names
    columns: tuple

    def labels(self):
        return [c.label for c in self.columns]


_EXAMPLES = {"date": timedelta(0), "date_given": timedelta(0), "next_due": timedelta(days=180),
             "litres": Decimal("6.5"), "quantity_kg": 25, "cost": 150, "dose": "2 ml", "diagnosis": "Mild fever",
             "treatment": "Paracetamol bolus", "vet_name": "Dr. Patil", "given_by": "Dr. Patil",
             "bull_or_semen": "GIR-S-2041", "technician": "A. Shinde"}


def make_specs(records, species, breeds):
    """The import / export columns: the cattle register's, and each record page's from app.RECORDS."""
    specs = {"cattle": Spec("cattle", "Cattle register", "cattle", "cattle-register", (
        Column("tag_no", "Tag number", "newtag", True, example="GIR-101"),
        Column("name", "Name", "text", example="Gauri"),
        Column("species", "Type", "choice", options=tuple(species), example=species[0]),
        Column("breed", "Breed", "text", example="Gir", examples=tuple(b for b in breeds if b != "Other")),
        Column("sex", "Sex", "choice", True, ("Female", "Male"), example="Female"),
        Column("dob", "Date of birth", "date", example=date(2022, 6, 15)),
        Column("color", "Colour / markings", "text", example="Red with white patches"),
        Column("weight_kg", "Weight (kg)", "number", example=380),
        Column("source", "Source", "choice", options=("Born on farm", "Purchased", "Donated"), example="Born on farm"),
        Column("purchase_date", "Purchase date", "date"),
        Column("purchase_price", "Purchase price (₹)", "number"),
        Column("mother_tag", "Mother (dam)", "mother"),
        Column("sire", "Father (sire / semen)", "text"),
        Column("status", "Status", "choice", options=("Active", "Sold", "Dead"), example="Active"),
        Column("notes", "Notes", "text"),
    ))}
    for kind, cfg in records.items():
        cols = [Column("tag_no", "Tag number", "tag", True, example="GIR-101")]
        for name, label, ftype, options in cfg["fields"]:
            ctype = {"select": "choice", "textarea": "text"}.get(ftype, ftype)
            example = (options[0] if options else "") if name not in _EXAMPLES else _EXAMPLES[name]
            if name == "session":
                example = "Morning"
            cols.append(Column(name, label, ctype, name in cfg["required"], tuple(options or ()), example))
        specs[kind] = Spec(kind, cfg["title"], cfg["table"], cfg["title"].lower().replace(" ", "-"), tuple(cols))
    return specs


# ---------------------------------------------------------------- reading the file

def read_table(filename, data):
    """(header, [(row number, [cells as text])]) from a .csv or .xlsx file. Raises FileProblem."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in (".csv", ".xlsx"):
        raise FileProblem(msg("Choose a .csv or .xlsx file."))
    if not data:
        raise FileProblem(msg("The file is empty."))
    if len(data) > MAX_BYTES:
        raise FileProblem(msg("The file is larger than 5 MB."))
    header, rows = None, []
    for line, cells in (_read_csv(data) if ext == ".csv" else _read_xlsx(data)):
        cells = [_clean(c) for c in cells[:MAX_COLUMNS]]
        if not any(cells):
            continue  # blank rows are left out
        if header is None:
            header = cells
            continue
        rows.append((line, cells))
        if len(rows) > MAX_ROWS:
            raise FileProblem(msg("The file has more than {n} rows. Please split it into smaller files.", n=MAX_ROWS))
    if header is None:
        raise FileProblem(msg("The file is empty."))
    if not rows:
        raise FileProblem(msg("The file has column names but no rows under them."))
    return header, rows


def _clean(text):
    return CONTROL_CHARS.sub("", text).strip()


def _read_csv(data):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")  # "CSV" (not "CSV UTF-8") saved by Excel on Windows
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")  # Excel uses ; in some regions
    except csv.Error:
        dialect = csv.excel
    try:
        yield from enumerate(csv.reader(io.StringIO(text, newline=""), dialect), start=1)
    except csv.Error:
        raise FileProblem(msg("The file could not be read as CSV.")) from None


def _read_xlsx(data):
    buf = io.BytesIO(data)
    try:
        with zipfile.ZipFile(buf) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_UNZIPPED:
                raise FileProblem(msg("The Excel file is too large once unpacked."))
        wb = load_workbook(buf, read_only=True, data_only=False)  # formulas come back as "=...", and are refused
    except FileProblem:
        raise
    except Exception:  # not a zip, not a workbook, broken XML...
        raise FileProblem(msg("The file is not a valid Excel .xlsx file.")) from None
    try:
        sheets = [ws for ws in wb.worksheets if _key(ws.title) not in INSTRUCTIONS_KEYS] or wb.worksheets
        for line, row in enumerate(sheets[0].iter_rows(values_only=True), start=1):
            yield line, [_cell_text(v) for v in row[:MAX_COLUMNS]]
    except FileProblem:
        raise
    except Exception:
        raise FileProblem(msg("The file is not a valid Excel .xlsx file.")) from None
    finally:
        wb.close()


def _cell_text(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


# ---------------------------------------------------------------- columns and rows

def _key(s):
    """A heading or value compared loosely: case, spaces, brackets and punctuation don't matter."""
    return re.sub(r"[\s_\-.()*:/₹,'\"]+", "", str(s).strip().lower())


INSTRUCTIONS_KEYS = {_key("Instructions"), _key(i18n.MR.get("Instructions", "Instructions"))}


def _names(col):
    """Every heading accepted for a column: its database name, its label in English or Marathi..."""
    labels = {col.label, re.sub(r"\s*\(.*?\)", "", col.label)}
    names = {col.name, *labels, *(i18n.MR.get(label, "") for label in labels)}
    if col.type in ("tag", "newtag"):
        names |= {"tag", "tag no", "tag number", i18n.MR.get("Tag", "")}
    if col.type == "mother":
        names |= {"mother tag", "dam", "dam tag", "mother"}
    return {_key(n) for n in names if n}


def match_columns(spec, header):
    """({column name: position in the file}, [headings not used], [problems with the columns])."""
    lookup = {}
    for col in spec.columns:
        for k in _names(col):
            lookup.setdefault(k, col.name)
    found, ignored, problems = {}, [], []
    for i, heading in enumerate(header):
        if not heading:
            continue
        name = lookup.get(_key(heading))
        if name is None:
            ignored.append(heading)
        elif name in found:
            problems.append(msg("The column {name} appears twice.", name=heading))
        else:
            found[name] = i
    for col in spec.columns:
        if col.required and col.name not in found:
            problems.append(msg("Required column missing: {col}.", col=col.label))
    return found, ignored, problems


def best_fit(specs, header):
    """The import a file's columns fit (every required column there), the one using most columns; or None."""
    fits = []
    for spec in specs.values():
        found, _, problems = match_columns(spec, header)
        if not problems:
            fits.append((len(found), spec))
    return max(fits, key=lambda f: f[0])[1] if fits else None


@dataclass
class Row:
    line: int                                   # the row number in the file
    cells: list                                 # what the file had, in spec.columns order
    values: dict = field(default_factory=dict)  # checked values, ready to save
    errors: list = field(default_factory=list)  # Msg, one per problem
    species: str = None                         # records: the animal's, for the expected calving date
    mother_row: "Row" = None                    # cattle: the dam is another row of this file

    @property
    def ok(self):
        return not self.errors


def _short(text):
    return text if len(text) <= 40 else text[:37] + "..."


def _check(col, text):
    """(value, None) or (None, Msg) for one cell."""
    if text.startswith("'") and text[1:].startswith(FORMULA_START):
        text = text[1:]  # the ' that safe_cell() put in front, in a skipped-rows file sent back
    if not text:
        if col.required:
            return None, msg("{col}: required.", col=col.label)
        return (col.options[0] if col.type == "choice" else None), None
    if len(text) > MAX_TEXT:
        return None, msg("{col}: longer than {n} characters.", col=col.label, n=MAX_TEXT)
    if text.startswith("=") or (col.type not in ("number", "date") and text.startswith(FORMULA_START)):
        return None, msg("{col}: {value} starts with {char}, which a spreadsheet would run as a formula.",
                         col=col.label, value=_short(text), char=text[0])
    if col.type == "date":
        m = DATE_RE.fullmatch(text)
        try:
            d = date.fromisoformat(m.group(1)) if m else None
        except ValueError:
            d = None
        if d is None or not 1900 <= d.year <= 2100:
            return None, msg("{col}: {value} is not a date written YYYY-MM-DD.", col=col.label, value=_short(text))
        return d, None
    if col.type == "number":
        t = text.replace("₹", "").strip()
        if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t):
            t = t.replace(",", "")  # 1,200.50
        try:
            n = Decimal(t)
        except InvalidOperation:
            n = None
        if n is None or not n.is_finite() or n < 0:
            return None, msg("{col}: {value} is not a number (0 or more).", col=col.label, value=_short(text))
        if n > MAX_NUMBER:
            return None, msg("{col}: {value} is too large.", col=col.label, value=_short(text))
        return n.quantize(Decimal("0.01")), None
    if col.type == "choice":
        k = _key(text)
        for option in col.options:
            if k in (_key(option), _key(i18n.MR.get(option, option))):
                return option, None
        return None, msg("{col}: {value} is not one of the choices ({options}).", col=col.label,
                         value=_short(text), options=col.options)
    return text, None  # text, tag, newtag, mother


def validate(spec, rows, found, animals):
    """Check every row. `animals`: {tag number: {"id", "sex", "species"}} for the whole register."""
    exact = dict(animals)
    loose = defaultdict(list)
    for tag, a in animals.items():
        loose[tag.lower()].append(a)

    def animal(tag):  # GIR-1 also finds gir-1, unless two tags differ only in case
        return exact.get(tag) or (loose[tag.lower()][0] if len(loose.get(tag.lower(), ())) == 1 else None)

    results = []
    for line, cells in rows:
        raw = [cells[found[c.name]] if found.get(c.name, len(cells)) < len(cells) else "" for c in spec.columns]
        r = Row(line, raw)
        for col, text in zip(spec.columns, raw):
            value, problem = _check(col, text)
            if problem:
                r.errors.append(problem)
            else:
                r.values[col.name] = value
        tag = r.values.get("tag_no")
        if tag and spec.kind != "cattle":
            a = animal(tag)
            if a is None:
                r.errors.append(msg("Tag {tag}: no animal with this tag number in the register.", tag=tag))
            else:
                r.values["cattle_id"], r.species = a["id"], a["species"]
        results.append(r)
    if spec.kind == "cattle":
        _check_new_animals(results, animal)
    return results


def _check_new_animals(results, animal):
    lines = defaultdict(list)  # tag (any case) -> rows of the file that have it
    for r in results:
        if r.values.get("tag_no"):
            lines[r.values["tag_no"].lower()].append(r)
    for r in results:
        tag = r.values.get("tag_no")
        if not tag:
            continue
        if animal(tag):
            r.errors.append(msg("Tag {tag}: an animal with this tag number is already in the register.", tag=tag))
        same = lines[tag.lower()]
        if len(same) > 1:
            r.errors.append(msg("Tag {tag} appears more than once in this file (rows {rows}).", tag=tag,
                                rows=", ".join(str(x.line) for x in same)))
    for r in results:
        m = r.values.get("mother_tag")
        if not m:
            continue
        in_file = lines.get(m.lower(), [])
        if r.values.get("tag_no") and m.lower() == r.values["tag_no"].lower():
            r.errors.append(msg("{col}: an animal can't be its own mother.", col="Mother (dam)"))
        elif animal(m):
            if animal(m)["sex"] != "Female":
                r.errors.append(msg("{col}: {tag} is not a female.", col="Mother (dam)", tag=m))
            else:
                r.values["dam_id"] = animal(m)["id"]
        elif len(in_file) == 1:
            if in_file[0].values.get("sex") != "Female":
                r.errors.append(msg("{col}: {tag} is not a female.", col="Mother (dam)", tag=m))
            else:
                r.mother_row = in_file[0]
        elif in_file:
            r.errors.append(msg("{col}: {tag} appears more than once in this file.", col="Mother (dam)", tag=m))
        else:
            r.errors.append(msg("{col}: no animal with tag {tag} in the register or in this file.",
                                col="Mother (dam)", tag=m))
    changed = True
    while changed:  # a calf can't be linked to a mother whose own row is skipped
        changed = False
        for r in results:
            if r.ok and r.mother_row is not None and not r.mother_row.ok:
                r.errors.append(msg("{col}: the row for {tag} has problems, so it won't be imported.",
                                    col="Mother (dam)", tag=r.values["mother_tag"]))
                changed = True


def save(conn, spec, results, gestation):
    """Insert the valid rows in one transaction: all of them or, if the database refuses one, none.

    Returns how many were saved. `gestation`: {species: days}, others 283 (as app.GESTATION_DAYS).
    """
    rows = [r for r in results if r.ok]
    if not rows:
        return 0
    with conn.transaction(), conn.cursor(row_factory=tuple_row) as cur:
        if spec.kind == "cattle":
            cols = [c.name for c in spec.columns if c.name != "mother_tag"] + ["dam_id"]
            cur.executemany(f"INSERT INTO cattle ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
                            " RETURNING id", [[r.values.get(c) for c in cols] for r in rows], returning=True)
            new_id = {}
            for r in rows:
                new_id[r.line] = cur.fetchone()[0]
                cur.nextset()
            links = [(new_id[r.mother_row.line], new_id[r.line]) for r in rows if r.mother_row is not None]
            if links:
                cur.executemany("UPDATE cattle SET dam_id=%s WHERE id=%s", links)
        else:
            cols = ["cattle_id"] + [c.name for c in spec.columns if c.name != "tag_no"]
            for r in rows:
                if spec.kind == "breeding" and not r.values.get("expected_calving"):
                    r.values["expected_calving"] = r.values["date"] + timedelta(days=gestation.get(r.species, 283))
            cur.executemany(f"INSERT INTO {spec.table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                            [[r.values.get(c) for c in cols] for r in rows])
    return len(rows)


# ---------------------------------------------------------------- writing files

def safe_cell(value):
    """A CSV cell a spreadsheet will show as text, never run: a leading = + - @ gets a ' in front."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def csv_bytes(header, rows):
    """A CSV file (UTF-8 with a BOM, so Excel shows Marathi correctly), formula-safe."""
    out = io.StringIO()
    w = csv.writer(out)
    for row in [header, *rows]:
        w.writerow([safe_cell(v) for v in row])
    return out.getvalue().encode("utf-8-sig")


def sheet_title(text):
    return re.sub(r"[\[\]:*?/\\]", "", text)[:31] or "Sheet"


def _width(v):
    if isinstance(v, (date, datetime)):
        return 10
    return max((len(line) for line in str(v).splitlines()), default=0) if v is not None else 0


def _put(ws, row, col, value):
    if isinstance(value, datetime):
        value = value.replace(tzinfo=None)
    if isinstance(value, str):
        value = ILLEGAL_CHARACTERS_RE.sub("", value)
    cell = ws.cell(row=row, column=col, value=value)
    if isinstance(value, str):
        cell.data_type = "s"  # always text: a value like "=1+1" is shown, never run as a formula
    elif isinstance(value, date):
        cell.number_format = "yyyy-mm-dd"
    return cell


def _write_table(ws, header, rows, autofilter=True):
    """Bold headings, frozen top row, column widths fitted to the content."""
    bold = Font(bold=True)
    widths = [_width(h) for h in header]
    for c, h in enumerate(header, start=1):
        _put(ws, 1, c, h).font = bold
    for r, row in enumerate(rows, start=2):
        for c, v in enumerate(row, start=1):
            _put(ws, r, c, v)
            widths[c - 1] = max(widths[c - 1], _width(v))
    for c, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = min(max(w + 2, 8), 60)
    ws.freeze_panes = "A2"
    if autofilter and rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{len(rows) + 1}"


def _xlsx_bytes(wb):
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def workbook(title, header, rows):
    """An .xlsx with one sheet: the export of a page."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title(title)
    _write_table(ws, header, rows)
    return _xlsx_bytes(wb)


def _example(col, tr):
    v = col.example
    if isinstance(v, timedelta):
        return date.today() + v
    return tr(v) if col.type == "choice" and v else v


def _explain(col, tr):
    parts = []
    if col.type == "newtag":
        parts.append(tr("A new tag number, not used by another animal."))
    elif col.type == "tag":
        parts.append(tr("Tag number of an animal already in the register."))
    elif col.type == "mother":
        parts.append(tr("Tag number of the mother: an animal in the register or a female in this file."))
    elif col.type == "date":
        parts.append(tr("Date, written YYYY-MM-DD."))
    elif col.type == "number":
        parts.append(tr("Number, 0 or more, for example 12.5."))
    elif col.type == "choice":
        parts.append(tr("One of: {options}.", options=", ".join(tr(o) for o in col.options)))
        if not col.required:
            parts.append(tr("Blank means {value}.", value=tr(col.options[0])))
    elif col.examples:
        parts.append(tr("Text, for example: {examples}.", examples=", ".join(tr(x) for x in col.examples[:6])))
    else:
        parts.append(tr("Text."))
    if col.name == "expected_calving":
        parts.append(tr("Blank: worked out from the service date (283 days; 310 for buffaloes)."))
    return " ".join(parts)


def template(spec, tr):
    """The import template: the column headings, one example row, and an Instructions sheet."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title(tr(spec.title))
    _write_table(ws, [tr(c.label) for c in spec.columns], [[_example(c, tr) for c in spec.columns]],
                 autofilter=False)
    for i, col in enumerate(spec.columns, start=1):
        letter = get_column_letter(i)
        if col.type == "date":  # a date typed in Excel shows, and saves to CSV, as YYYY-MM-DD
            ws.column_dimensions[letter].width = max(ws.column_dimensions[letter].width, 13)
            for r in range(3, 502):
                ws.cell(row=r, column=i).number_format = "yyyy-mm-dd"
        if col.type == "choice":  # a drop-down list of the allowed values
            options = [tr(o) for o in col.options]
            listed = ",".join(options)
            if len(listed) <= 250 and not any("," in o or '"' in o for o in options):
                dv = DataValidation(type="list", formula1=f'"{listed}"', allow_blank=True)
                dv.error = tr("Choose one of the values in the list.")
                ws.add_data_validation(dv)
                dv.add(f"{letter}2:{letter}1001")

    help_ws = wb.create_sheet(sheet_title(tr("Instructions")))
    table = [[tr(c.label), tr("Required") if c.required else tr("Optional"), _explain(c, tr)] for c in spec.columns]
    _write_table(help_ws, [tr("Column"), tr("Required?"), tr("What to enter")], table, autofilter=False)
    notes = [tr("Row 2 of the first sheet is an example: replace it with your own rows or delete it."),
             tr("Animals are matched by their tag number."),
             tr("Dates are written YYYY-MM-DD, for example {example}. Date cells formatted by Excel are fine too.",
                example=date.today().isoformat()),
             tr("Save the file as Excel (.xlsx) or as CSV UTF-8, up to 5 MB, and upload it on the Import page. "
                "Nothing is saved until you check the preview and click Confirm import.")]
    start = len(table) + 3
    for i, line in enumerate(notes):
        _put(help_ws, start + i, 1, line)
    return _xlsx_bytes(wb)
