"""Fill an empty database with sample cattle, records, products and users.

Run once:  python seed_demo.py
"""
import random
from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import app, ex, q

random.seed(7)
T = date.today()


def d(days_ago):
    return (T - timedelta(days=days_ago)).isoformat()


with app.app_context():
    if q("SELECT 1 FROM cattle LIMIT 1"):
        raise SystemExit("Database already has cattle; demo data not added.")

    for u, name, role in [("staff", "Ramesh Patil", "staff"), ("customer", "Sunita Deshmukh", "customer")]:
        ex("INSERT INTO users (username, password_hash, full_name, phone, address, role) VALUES (?,?,?,?,?,?)",
           (u, generate_password_hash(u + "123"), name, "98220 00000", "Kothrud, Pune", role))

    herd = [("GIR-001", "Kamdhenu", "Cow", "Gir", "Female", 2100, "Red with white patches"),
            ("GIR-002", "Gauri", "Cow", "Gir", "Female", 1700, "Deep red"),
            ("SAH-003", "Lakshmi", "Cow", "Sahiwal", "Female", 1900, "Reddish brown"),
            ("KHL-004", "Nandi", "Bull", "Khillar", "Male", 2400, "Grey white"),
            ("MUR-005", "Kali", "Buffalo", "Murrah", "Female", 2000, "Jet black"),
            ("DEO-006", "Sundari", "Cow", "Deoni", "Female", 1300, "Black and white"),
            ("RS-007", "Ganga", "Cow", "Red Sindhi", "Female", 2600, "Dark red")]
    ids = {}
    for tag, name, sp, breed, sex, age, col in herd:
        ids[tag] = ex("INSERT INTO cattle (tag_no, name, species, breed, sex, dob, color, weight_kg, source)"
                      " VALUES (?,?,?,?,?,?,?,?,?)",
                      (tag, name, sp, breed, sex, d(age), col, random.randint(320, 520),
                       random.choice(["Born on farm", "Purchased"])))
    ids["GIR-008"] = ex("INSERT INTO cattle (tag_no, name, species, breed, sex, dob, color, weight_kg, dam_id, sire)"
                        " VALUES ('GIR-008','Chhoti','Calf','Gir','Female',?,'Red',85,?, 'Gir semen GS-118')",
                        (d(120), ids["GIR-001"]))

    milkers = ["GIR-001", "GIR-002", "SAH-003", "MUR-005", "RS-007"]
    for day in range(60):
        for tag in milkers:
            base = {"MUR-005": 5.0, "SAH-003": 4.5}.get(tag, 4.0)
            for sess in ("Morning", "Evening"):
                ex("INSERT INTO milk_records (cattle_id, date, session, litres) VALUES (?,?,?,?)",
                   (ids[tag], d(day), sess, round(base + random.uniform(-0.8, 0.8), 1)))

    for tag in ids:
        ex("INSERT INTO vaccinations (cattle_id, date_given, vaccine, dose, next_due, given_by, cost)"
           " VALUES (?,?,?,?,?,?,?)", (ids[tag], d(170), "FMD (Foot and Mouth)", "2 ml", d(-10), "Dr. Kulkarni", 40))
        ex("INSERT INTO vaccinations (cattle_id, date_given, vaccine, dose, next_due, given_by, cost)"
           " VALUES (?,?,?,?,?,?,?)", (ids[tag], d(300), "HS (Haemorrhagic Septicaemia)", "5 ml", d(-65),
                                      "Dr. Kulkarni", 30))
    ex("INSERT INTO vaccinations (cattle_id, date_given, vaccine, dose, next_due, given_by, cost)"
       " VALUES (?,?,?,?,?,?,?)", (ids["KHL-004"], d(380), "Lumpy Skin Disease", "1 ml", d(15), "Dr. Kulkarni", 60))

    ex("INSERT INTO health_records (cattle_id, date, kind, diagnosis, treatment, vet_name, cost) VALUES"
       " (?,?,?,?,?,?,?)", (ids["GIR-002"], d(12), "Illness", "Mild mastitis, front left quarter",
                            "Intramammary antibiotic, 3 days", "Dr. Kulkarni", 650))
    ex("INSERT INTO health_records (cattle_id, date, kind, diagnosis, treatment, vet_name, cost) VALUES"
       " (?,?,?,?,?,?,?)", (ids["KHL-004"], d(40), "Deworming", "Routine", "Albendazole bolus", "Ramesh Patil", 120))
    ex("INSERT INTO health_records (cattle_id, date, kind, diagnosis, treatment, vet_name, cost) VALUES"
       " (?,?,?,?,?,?,?)", (ids["GIR-008"], d(5), "Check-up", "Healthy, gaining weight", "", "Dr. Kulkarni", 200))

    for day in range(0, 60, 7):
        for tag in ids:
            ex("INSERT INTO feedings (cattle_id, date, feed_type, quantity_kg, cost) VALUES (?,?,?,?,?)",
               (ids[tag], d(day), "Green fodder", 175, 350))
            ex("INSERT INTO feedings (cattle_id, date, feed_type, quantity_kg, cost) VALUES (?,?,?,?,?)",
               (ids[tag], d(day), "Concentrate", 14, 420))

    ex("INSERT INTO breedings (cattle_id, date, method, bull_or_semen, technician, pregnancy, expected_calving)"
       " VALUES (?,?,?,?,?,?,?)", (ids["GIR-002"], d(200), "Artificial insemination", "Gir semen GS-204",
                                   "AI technician, Baramati", "Confirmed",
                                   (T - timedelta(days=200) + timedelta(days=283)).isoformat()))
    ex("INSERT INTO breedings (cattle_id, date, method, bull_or_semen, technician, pregnancy, expected_calving)"
       " VALUES (?,?,?,?,?,?,?)", (ids["MUR-005"], d(250), "Natural service", "Murrah bull (neighbour)",
                                   "", "Confirmed", (T - timedelta(days=250) + timedelta(days=310)).isoformat()))
    ex("INSERT INTO breedings (cattle_id, date, method, bull_or_semen, technician, pregnancy, expected_calving,"
       " actual_calving, calf_tag) VALUES (?,?,?,?,?,?,?,?,?)",
       (ids["GIR-001"], d(403), "Artificial insemination", "Gir semen GS-118", "AI technician, Baramati",
        "Confirmed", d(120), d(120), "GIR-008"))
    ex("INSERT INTO breedings (cattle_id, date, method, bull_or_semen, pregnancy, expected_calving)"
       " VALUES (?,?,?,?,?,?)", (ids["DEO-006"], d(20), "Artificial insemination", "Deoni semen DN-31", "Pending",
                                 (T - timedelta(days=20) + timedelta(days=283)).isoformat()))

    products = [("A2 Gir cow milk", "Milk", "Fresh desi cow milk, delivered in glass bottles every morning.", 90, "litre", 40),
                ("Buffalo milk", "Milk", "Rich Murrah buffalo milk, high fat.", 75, "litre", 25),
                ("Bilona cow ghee", "Ghee", "Hand-churned ghee made from curd by the traditional bilona method.", 1450, "500 g jar", 18),
                ("Fresh curd (dahi)", "Curd & Buttermilk", "Set overnight in clay pots.", 60, "500 g", 30),
                ("Masala chhaas", "Curd & Buttermilk", "Spiced buttermilk with jeera and coriander.", 25, "bottle", 50),
                ("Malai paneer", "Paneer", "Soft paneer made the same morning.", 110, "250 g", 12),
                ("White butter (loni)", "Butter", "Unsalted homemade butter.", 160, "250 g", 4),
                ("Vermicompost", "Organic manure", "Composted cow dung for kitchen gardens.", 15, "kg", 300)]
    for p in products:
        ex("INSERT INTO products (name, category, description, price, unit, stock) VALUES (?,?,?,?,?,?)", p)

    cust = q("SELECT id FROM users WHERE username='customer'", one=True)["id"]
    oid = ex("INSERT INTO orders (user_id, total, phone, address, status, created_at) VALUES (?,?,?,?,?,?)",
             (cust, 1630, "98220 00000", "Kothrud, Pune", "Delivered", d(3) + " 08:15:00"))
    ex("INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (?,1,'A2 Gir cow milk',90,2)", (oid,))
    ex("INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (?,3,'Bilona cow ghee',1450,1)", (oid,))
    oid = ex("INSERT INTO orders (user_id, total, phone, address) VALUES (?,?,?,?)",
             (cust, 230, "98220 00000", "Kothrud, Pune"))
    ex("INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (?,6,'Malai paneer',110,1)", (oid,))
    ex("INSERT INTO order_items (order_id, product_id, name, price, qty) VALUES (?,4,'Fresh curd (dahi)',60,2)", (oid,))
    print("Demo data added. Logins: admin/admin123, staff/staff123, customer/customer123")
