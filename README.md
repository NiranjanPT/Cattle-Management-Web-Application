# Gaushala: cattle management web app

A web application for running a cattle farm or gaushala: herd records, health and
breeding, a dairy shop for customers, a Hindu calendar with nakshatras, and reports.

## Run it on your computer

You need Python 3.10 or newer.

```bash
cd cattle-app
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python seed_demo.py      # optional: adds sample cattle, records and products
python app.py
```

Open http://127.0.0.1:5000 and log in.

| Login | Password | Can do |
|---|---|---|
| admin | admin123 | everything, including managing users |
| staff | staff123 | cattle, records, products, orders, reports (demo data only) |
| customer | customer123 | shop and calendar (demo data only) |

Change the admin password on the **My profile** page straight away. Customers can
create their own accounts from the login page.

The database is a single file, `cattle.db`, created next to `app.py`. Back it up by
copying that file. Set `CATTLE_DB=/path/to/file.db` to keep it somewhere else.

## Plan

### Stack
- **Python + Flask** for the server: small, widely taught, easy to read.
- **SQLite** for the database: one file, nothing to install.
- **Server-rendered HTML pages** (Jinja templates) with one CSS file: no JavaScript
  build step. Works on phones and prints cleanly; follows the system's dark mode.

### Modules
| Module | What it does |
|---|---|
| Admin and user management | Admin adds staff, admins and customers, changes roles, disables accounts, resets passwords. Everyone can edit their own profile and password. |
| Cattle records and profiles | Register of every animal: tag, name, type, breed, sex, birth date, colour, weight, source and purchase price, mother and father, status (active, sold, dead), photo, notes. Each animal has a profile page with its age, calves, 30-day milk yield and its latest records of every kind. Printable. |
| Health | Check-ups, illnesses, treatments, deworming, with vet and cost. |
| Vaccination | Vaccine given, dose, next due date. Overdue and upcoming doses show on the dashboard and calendar. |
| Feeding | Feed type, quantity and cost. |
| Breeding | AI or natural service, bull or semen ID, pregnancy status, expected calving (filled automatically: 283 days for cows, 310 for buffaloes), actual calving and calf tag. |
| Milk production | Morning and evening litres per animal. |
| Dairy shop | Product catalogue (milk, ghee, curd, paneer, manure...) with stock. Customers add to cart and place pay-on-delivery orders; stock is reduced, and returned if staff cancel the order. Staff manage products and order status. |
| Hindu calendar | Chaitra to Phalguna for any year (1900 to 2100), including adhika months. Each day shows tithi, paksha, nakshatra with pada, and the moon's rashi; Purnima, Amavasya and Ekadashi are marked. A year overview lists each month's start, Purnima and end, and a reference page lists all 27 nakshatras with their span, ruling planet, deity and symbol. Farm events (vaccines due, expected calvings) appear on the calendar days. |
| Reports and dashboard | Dashboard: herd size, today's and this month's milk, pregnancies, vaccines due, pending orders, monthly sales, a 14-day milk chart, upcoming calvings and recent health events. Reports for any date range: herd summary, milk by month and by animal, costs, feed used, shop sales, breeding; each downloadable as CSV. |

Any record form can be filled for several animals at once (for example a herd-wide
vaccination or feeding) by selecting more than one animal.

### Data model
`users`, `cattle` (with `dam_id` pointing to the mother), `health_records`,
`vaccinations`, `feedings`, `breedings`, `milk_records`, `products`, `orders`,
`order_items`. See `schema.sql`. Deleting an animal deletes its records.

### About the calendar
Months are amanta (new moon to new moon), as used in Maharashtra, Gujarat and
Karnataka. Positions of the sun and moon are calculated in `panchang.py` from
standard astronomical formulas (Meeus) with the Lahiri ayanamsa, and each day is
read at 6:00 AM IST as a stand-in for sunrise. It was checked against known dates:
adhika Shravana 2023, adhika Jyeshtha 2026, Pausha Shukla Dwadashi with Mrigashira
on 22 Jan 2024, Phalguna Purnima on 25 Mar 2024, Ashvin Amavasya on 21 Oct 2025.
On days when a tithi or nakshatra changes close to sunrise it can differ by a day
from a printed panchang for your town.

## Files
- `app.py`: all pages and logic
- `panchang.py`: Hindu calendar calculations
- `schema.sql`: database tables
- `seed_demo.py`: sample data
- `templates/`: HTML pages, `static/style.css`: styling

## Ideas for a next version
Local sunrise by town for the calendar; SMS or WhatsApp reminders for vaccines;
online payment (UPI) in the shop; Marathi and Hindi interface; daily milk
collection entry for the whole herd on one screen; hosting online.
