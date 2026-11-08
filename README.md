# VrushaA2 Dairy Tech (वृषाA2 डेरीटेक प्रा. लि.)

*संस्कारातून समृद्धीकडे: from sanskar to prosperity.*

The company website and a web application for running the farm: herd records, health
and breeding, scheduled tasks, a dairy shop for customers, a Hindu calendar with nakshatras and a dashboard,
in English and Marathi.

## Run it on your computer

You need Python 3.10 or newer and a free [Neon](https://neon.tech) account (hosted PostgreSQL).

### 1. Create the database on Neon
1. Sign up at neon.tech and create a project (pick the region nearest you; for India, Singapore).
2. On the project dashboard click **Connect** and copy the connection string. It looks like
   `postgresql://USER:PASSWORD@ep-...-pooler.REGION.aws.neon.tech/neondb?sslmode=require`.
3. For running the tests, create a second **branch** (Branches > New branch) and copy its
   connection string too. The tests wipe that branch, so never give them your main one.

### 2. Install and configure
```bash
cd cattle-app
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
```
Copy `.env.example` to `.env` and fill in `DATABASE_URL`, `DATABASE_URL_TEST` (optional,
for tests) and `SECRET_KEY` (make one with
`python -c "import secrets; print(secrets.token_hex(32))"`). `.env` holds your database
password: it is listed in `.gitignore`, so never commit or share it.

### 3. Create the tables
```bash
alembic upgrade head
```
This also sets the database's time zone to Asia/Kolkata (order times and monthly totals
are counted in farm time).

### 4. Optional: add demo data
For a fresh database with sample animals, records, products and orders, run this
**before** starting the app for the first time (the app creates an `admin` account on
its first start, and the demo data needs an empty database):
```bash
python seed_demo.py
```

### 5. Run
```bash
python app.py
```

Open http://127.0.0.1:5000: the company website, with **Log in** at the top right.

| Login | Password | Can do |
|---|---|---|
| admin | admin123 | everything, including managing users |
| staff | staff123 | cattle, records, tasks, products, orders (demo data only) |
| customer | customer123 | shop and calendar (demo data only) |

Change the admin password on the **My profile** page straight away. Customers can
create their own accounts from the login page.

The data lives in your Neon database. Neon keeps a restore history (point-in-time
restore) and you can make a backup copy any time by creating a branch, or with
`pg_dump "$DATABASE_URL" > backup.sql`. Animal photos are still saved on this computer,
in `static/uploads/`; back that folder up too.

### Changing the database later
Schema changes go in a new Alembic migration: `alembic revision -m "what changed"`, write
the SQL in its `upgrade()` and `downgrade()`, then `alembic upgrade head`. Update
`schema.sql` (a readable copy of the full schema) to match.

### Tests
```bash
pytest
```
The tests use only `DATABASE_URL_TEST` and refuse to start without it or if it points at
the main database. They log in as admin, staff and customer, open every page, add an
animal, a breeding record and a milk record for several animals, place and cancel a
shop order, and check the database migrations and the demo seed. Over the internet to Neon they
take a few minutes.

## Plan

### Stack
- **Python + Flask** for the server: small, widely taught, easy to read.
- **PostgreSQL on Neon** for the database: hosted, backed up, nothing to install. The app
  talks to it with psycopg 3 through a small connection pool; Alembic manages the schema.
- **Server-rendered HTML pages** (Jinja templates) with one CSS file: no JavaScript
  build step. Works on phones and prints cleanly. White theme in the logo's brown, with
  the Mukta font (Google Fonts) for English and Devanagari.

### Modules
| Module | What it does |
|---|---|
| Website | The home page fits one laptop screen: the logo and tagline, **Order fresh dairy** (the shop, or log in first) and **See our products**, and a strip with the herd size and number of products. Each menu item has its own page: **About us** (`/about`: our values and today's panchang), **Products** (`/products`: the live list with prices), **How to order** (`/how-to-order`) and **Contact** (`/contact`; set `BRAND_ADDRESS`, `BRAND_PHONE`, `BRAND_EMAIL` in `.env` and on Vercel). Logged-in users reach it from **Website** in the menu. |
| Finding your way | Every page except the home page, the login page and the dashboard has **← Back** at the top left (to the page above it: an animal to the register, a record form to its list, an order to the orders) and breadcrumbs. The menu shows only what your role can use, marks the current page, and folds into a **☰ Menu** button on phones. Lists show 25 rows a page with search and filters at the top and the column headings kept in view; forms are two columns on wide screens with Save and Cancel always in view; long pages have **Back to top**. The dashboard has quick actions (Add cattle, Add record, Import CSV, Reports: the Excel downloads) and compact cards with **View all** links; an animal's page has tabs (Overview, Health, Vaccinations, Feeding, Breeding, Milk). Built for WCAG 2.1 AA: text 16px or larger, 4.5:1 contrast, 44px tap targets, visible keyboard focus, a **Skip to content** link, labels on every field and messages read out by screen readers. |
| English and Marathi | **English / मराठी** at the top right of every page switches the whole site, website and app, including messages, dates and calendar names; the choice is remembered in the browser. Data is stored as typed and translated for display: words from the app's own lists (types, breeds, vaccines, statuses...) from `i18n.py`; text people type (animal and people names, colours, notes, product names and descriptions, task titles) by the free MyMemory service through the `deep-translator` library. Each text is translated once, the first time a Marathi page shows it (that page takes a few seconds longer), and kept in the `text_translations` table. Machine translation can get a sentence wrong; names come out well. The text sent is names, notes and product details, never phone numbers, emails or addresses. Text typed in Marathi is shown as typed. If the service can't be reached, the text stays as typed and it tries again ten minutes later. |
| Admin and user management | Admin adds staff members (choosing their first password), changes roles, disables accounts and removes accounts (not their own; a customer with orders is kept for the order history and can be disabled instead). Customers create their own accounts, giving their address, city and pincode (6 digits); the **Users** list filters accounts by city (with how many live in each) and by pincode (a whole pincode, or its first digits for an area: 416 is all of Sangli district), and sorts them by name, city, pincode or date joined. **Customer orders** filters by the customer's city and pincode the same way, for planning deliveries. After an account is created its owner changes the password on My profile; an admin can reset a staff member's forgotten password (never a customer's or an admin's). Nobody can see a password. |
| Cattle records and profiles | Register of every animal: tag, name, type, breed, sex, birth date, colour, weight, source and purchase price, mother and father, status (active, sold, dead), photo, notes. Each animal has a profile page with its age, calves, 30-day milk yield and its latest records of every kind. Printable. |
| Health | Check-ups, illnesses, treatments, deworming, with vet and cost. |
| Vaccination | Vaccine given, dose, next due date. Overdue and upcoming doses show on the dashboard and calendar. |
| Feeding | Feed type, quantity and cost. |
| Breeding | AI or natural service, bull or semen ID, pregnancy status, expected calving (filled automatically: 283 days for cows, 310 for buffaloes), actual calving and calf tag. |
| Milk production | Morning and evening litres per animal. |
| Dairy shop | Product catalogue (milk, ghee, curd, paneer, manure...) with stock. Customers add to cart and place pay-on-delivery orders; stock is reduced, and returned if staff cancel the order. Staff manage products and order status; confirming an order needs an expected delivery date, which the customer sees on My orders and on the order page. It can instead be marked **Delivery unavailable**. The owner switches home delivery on or off at the top of **Customer orders** (admin only; off to start with), and customers see which it is on the website, the shop, the cart and their orders. New (Pending) orders show as a green count on **Customer orders** in the menu. |
| Hindu calendar | Chaitra to Phalguna for any year (1900 to 2100), including adhika months. Each day shows tithi, paksha, nakshatra with pada, the moon's rashi, sunrise time and Agnivas (whether Agni is on earth, the time for havan) with every place Agni stays that day and the exact time it moves, as it changes with the tithi; Purnima, Amavasya and Ekadashi are marked, and the Agni Nakshatra (Kathiri) period in May is outlined, with the time it begins and ends. All times are farm time on a 12-hour clock (6:31 AM; in Marathi सकाळी 6:31), and a time after midnight shows its date. A year overview lists each month's start, Purnima and end, and a reference page lists all 27 nakshatras with their span, ruling planet, deity and symbol. Farm events (vaccines due, expected calvings, scheduled tasks) appear on the calendar days, and **+** on a day schedules a task. |
| Tasks | Schedule farm work: vaccination, health or deworming, feeding, breeding/AI, milking, havan/puja or anything else, for one animal, several or the whole herd, on a date, optionally repeating (daily, weekly, monthly, yearly), assigned to a staff member. Tasks show on the Tasks page, the calendar, the dashboard (this week, with overdue ones flagged), each animal's page and as a count in the menu. **Done →** on a record-type task opens that record form already filled in; saving it adds the records and completes the task in one step, and a repeating task schedules its next date. A havan on a day when Agni is not on earth gets a warning with the next good day. |
| Import and Excel export | **Import CSV** on the cattle register and on each record page (admin and staff only) takes a .csv or .xlsx file of up to 5 MB and 5,000 rows. Rows are matched to animals by tag number. Every row is checked first (required columns, dates written YYYY-MM-DD, numbers, choices, known tags, no repeated or existing tags for new animals, a mother that is a female in the register or in the same file) and shown in a preview, green or red with the reason; nothing is saved until **Confirm import**, and then all valid rows are saved in one transaction (if the database refuses one, none are). Afterwards the skipped rows can be downloaded as a CSV with their reasons. **Download template** on the import page gives an Excel file per record type with the columns, an example row and an Instructions sheet. **Export to Excel** on the cattle register and each record page downloads what the page's filters show (all rows, not just the 500 on screen), with bold headings, a frozen top row and fitted columns. Cells that start with = + - @ are never run as formulas: refused on import, kept as text in Excel exports and prefixed with ' in CSV files. The code is in `services/bulk_io.py`. |
| Dashboard | Herd size, today's and this month's milk, pregnancies, vaccines due, pending orders, monthly sales, a 14-day milk chart with each day's litres, upcoming calvings and recent health events. |

Any record form can be filled for several animals at once (for example a herd-wide
vaccination or feeding) by selecting more than one animal.

### Data model
`users` (with `city` and `pincode`), `cattle` (with `dam_id` pointing to the mother), `health_records`,
`vaccinations`, `feedings`, `breedings`, `milk_records`, `products`, `orders`,
`order_items`, `tasks`, `task_animals` (which animals a task is for) and
`text_translations` (remembered Marathi for typed text). See
`schema.sql`. Deleting an animal deletes its records and takes it off its tasks.

### About the calendar
Months are amanta (new moon to new moon), as used in Maharashtra, Gujarat and
Karnataka. `panchang.py` gets the sun and moon positions, new moons, precession and
sunrise from the PyEphem (`ephem`) astronomy library, and uses the Lahiri ayanamsa
from its official definition (23°15′00.658″ on 21 March 1956, plus precession).
Each day is read at sunrise in Sangli; to use another town set `FARM_LOCATION`,
`FARM_LAT`, `FARM_LON` and `FARM_ELEVATION` in `.env` (and on Vercel). The tests
check it against known dates: adhika Shravana 2023, adhika Jyeshtha 2026, Pausha
Shukla Dwadashi with Mrigashira on 22 Jan 2024, Phalguna Purnima on 25 Mar 2024,
Ashvin Amavasya on 21 Oct 2025 and Mauni Amavasya on 21 Jan 2023. On days when a
tithi or nakshatra changes close to sunrise, check a local panchang for muhurta timing.

**Agnivas** follows the rule सैका तिथिर्वारयुता कृताप्ता…: (tithi counted from Shukla
Pratipada + 1 + weekday counted from Sunday) divided by 4; a remainder of 3 or 0 puts
Agni on earth (Prithvi, good for havan), 1 in the sky (Akasha) and 2 in the
underworld (Patala). **Agni Nakshatra** (Kathiri) is the period when the sun moves
from Bharani pada 3 to the end of Rohini pada 1, about 4 to 28 May.

## Files
- `app.py`: all pages and logic
- `i18n.py`: the Marathi text for every English text in the pages and messages
- `panchang.py`: Hindu calendar calculations
- `services/bulk_io.py`: reading, checking and saving imported CSV / Excel files, and writing Excel files
- `static/brand/`: the logo, from the company's PDF (full logo and the round mark, SVG and PNG)
- `migrations/`, `alembic.ini`: database schema, managed by Alembic
- `schema.sql`: readable copy of the full schema
- `seed_demo.py`: sample data
- `tests/`: automated tests
- `.env.example`: settings to copy into `.env`
- `templates/`: HTML pages (`base.html` the frame of every page, `site.html` the website's inner pages),
  `static/style.css`: styling, `static/app.js`: the menu button, tabs, Back to top (each page also works without it)

## Ideas for a next version
SMS or WhatsApp reminders for tasks and vaccines;
online payment (UPI) in the shop; a Hindi interface; daily milk
collection entry for the whole herd on one screen.
