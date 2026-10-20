"""Hindu lunar calendar (amanta, Chaitra to Phalguna) with tithi and nakshatra.

Positions use truncated formulas from Jean Meeus, "Astronomical Algorithms"
(sun ~0.01 deg, moon ~0.1 deg) with the Lahiri ayanamsa, which is enough to
name the tithi and nakshatra of a day. Values are taken at 6:00 AM IST as a
stand-in for local sunrise, so a day where a tithi or nakshatra changes near
sunrise can differ from a printed panchang.
"""
import math
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

IST = timezone(timedelta(hours=5, minutes=30))

MONTHS = [
    ("Chaitra", "चैत्र"), ("Vaishakha", "वैशाख"), ("Jyeshtha", "ज्येष्ठ"),
    ("Ashadha", "आषाढ"), ("Shravana", "श्रावण"), ("Bhadrapada", "भाद्रपद"),
    ("Ashvin", "आश्विन"), ("Kartika", "कार्तिक"), ("Margashirsha", "मार्गशीर्ष"),
    ("Pausha", "पौष"), ("Magha", "माघ"), ("Phalguna", "फाल्गुन"),
]

TITHIS = ["Pratipada", "Dwitiya", "Tritiya", "Chaturthi", "Panchami",
          "Shashthi", "Saptami", "Ashtami", "Navami", "Dashami", "Ekadashi",
          "Dwadashi", "Trayodashi", "Chaturdashi"]

WEEKDAYS = ["Somavara", "Mangalavara", "Budhavara", "Guruvara",
            "Shukravara", "Shanivara", "Ravivara"]  # Monday first, like date.weekday()

RASHIS = ["Mesha", "Vrishabha", "Mithuna", "Karka", "Simha", "Kanya",
          "Tula", "Vrishchika", "Dhanu", "Makara", "Kumbha", "Meena"]

# name, Devanagari, ruling planet, deity, symbol
NAKSHATRAS = [
    ("Ashwini", "अश्विनी", "Ketu", "Ashwini Kumaras", "Horse's head"),
    ("Bharani", "भरणी", "Venus", "Yama", "Yoni"),
    ("Krittika", "कृत्तिका", "Sun", "Agni", "Razor / flame"),
    ("Rohini", "रोहिणी", "Moon", "Brahma (Prajapati)", "Chariot"),
    ("Mrigashira", "मृगशीर्ष", "Mars", "Soma", "Deer's head"),
    ("Ardra", "आर्द्रा", "Rahu", "Rudra", "Teardrop"),
    ("Punarvasu", "पुनर्वसु", "Jupiter", "Aditi", "Quiver of arrows"),
    ("Pushya", "पुष्य", "Saturn", "Brihaspati", "Cow's udder"),
    ("Ashlesha", "आश्लेषा", "Mercury", "Nagas", "Coiled serpent"),
    ("Magha", "मघा", "Ketu", "Pitris", "Royal throne"),
    ("Purva Phalguni", "पूर्वा फाल्गुनी", "Venus", "Bhaga", "Front legs of a bed"),
    ("Uttara Phalguni", "उत्तरा फाल्गुनी", "Sun", "Aryaman", "Back legs of a bed"),
    ("Hasta", "हस्त", "Moon", "Savitr", "Hand"),
    ("Chitra", "चित्रा", "Mars", "Vishvakarma", "Bright jewel"),
    ("Swati", "स्वाती", "Rahu", "Vayu", "Young shoot"),
    ("Vishakha", "विशाखा", "Jupiter", "Indra and Agni", "Triumphal arch"),
    ("Anuradha", "अनुराधा", "Saturn", "Mitra", "Lotus"),
    ("Jyeshtha", "ज्येष्ठा", "Mercury", "Indra", "Earring"),
    ("Mula", "मूल", "Ketu", "Nirriti", "Bunch of roots"),
    ("Purva Ashadha", "पूर्वाषाढा", "Venus", "Apas", "Winnowing fan"),
    ("Uttara Ashadha", "उत्तराषाढा", "Sun", "Vishvedevas", "Elephant tusk"),
    ("Shravana", "श्रवण", "Moon", "Vishnu", "Ear"),
    ("Dhanishta", "धनिष्ठा", "Mars", "Eight Vasus", "Drum"),
    ("Shatabhisha", "शतभिषा", "Rahu", "Varuna", "Empty circle"),
    ("Purva Bhadrapada", "पूर्वा भाद्रपदा", "Jupiter", "Aja Ekapada", "Front of a funeral cot"),
    ("Uttara Bhadrapada", "उत्तरा भाद्रपदा", "Saturn", "Ahirbudhnya", "Back of a funeral cot"),
    ("Revati", "रेवती", "Mercury", "Pushan", "Fish"),
]
NAK_SPAN = 360 / 27

# Moon longitude terms (Meeus table 47.A): D, M, M', F, coefficient in 1e-6 deg
_MOON_TERMS = [
    (0, 0, 1, 0, 6288774), (2, 0, -1, 0, 1274027), (2, 0, 0, 0, 658314),
    (0, 0, 2, 0, 213618), (0, 1, 0, 0, -185116), (0, 0, 0, 2, -114332),
    (2, 0, -2, 0, 58793), (2, -1, -1, 0, 57066), (2, 0, 1, 0, 53322),
    (2, -1, 0, 0, 45758), (0, 1, -1, 0, -40923), (1, 0, 0, 0, -34720),
    (0, 1, 1, 0, -30383), (2, 0, 0, -2, 15327), (0, 0, 1, 2, -12528),
    (0, 0, 1, -2, 10980), (4, 0, -1, 0, 10675), (0, 0, 3, 0, 10034),
    (4, 0, -2, 0, 8548), (2, 1, -1, 0, -7888), (2, 1, 0, 0, -6766),
    (1, 0, -1, 0, -5163), (1, 1, 0, 0, 4987), (2, -1, 1, 0, 4036),
    (2, 0, 2, 0, 3994), (4, 0, 0, 0, 3861), (2, 0, -3, 0, 3665),
    (0, 1, -2, 0, -2689), (2, 0, -1, 2, -2602), (2, -1, -2, 0, 2390),
    (1, 0, 1, 0, -2348), (2, -2, 0, 0, 2236), (0, 1, 2, 0, -2120),
    (0, 2, 0, 0, -2069), (2, -2, -1, 0, 2048), (2, 0, 1, -2, -1773),
    (2, 0, 0, 2, -1595), (4, -1, -1, 0, 1215), (0, 0, 2, 2, -1110),
]


def _jd(dt):
    """Julian Ephemeris Day for an aware datetime (adds ~69 s for TT-UTC)."""
    ts = dt.astimezone(timezone.utc).timestamp() + 69
    return ts / 86400 + 2440587.5


def _sun_moon(jd):
    T = (jd - 2451545.0) / 36525
    r = math.radians
    M = r(357.5291092 + 35999.0502909 * T)
    L0 = 280.46646 + 36000.76983 * T
    C = ((1.914602 - 0.004817 * T) * math.sin(M) + (0.019993 - 0.000101 * T) * math.sin(2 * M)
         + 0.000289 * math.sin(3 * M))
    omega = r(125.04 - 1934.136 * T)
    sun = L0 + C - 0.00569 - 0.00478 * math.sin(omega)

    Lp = 218.3164477 + 481267.88123421 * T
    D = r(297.8501921 + 445267.1114034 * T)
    Mp = r(134.9633964 + 477198.8675055 * T)
    F = r(93.2720950 + 483202.0175233 * T)
    E = 1 - 0.002516 * T
    s = 0.0
    for d, m, mp, f, c in _MOON_TERMS:
        s += c * E ** abs(m) * math.sin(d * D + m * M + mp * Mp + f * F)
    A1 = r(119.75 + 131.849 * T)
    A2 = r(53.09 + 479264.290 * T)
    s += 3958 * math.sin(A1) + 1962 * math.sin(r(Lp) - F) + 318 * math.sin(A2)
    moon = Lp + s / 1e6 - 0.00478 * math.sin(omega)
    return sun % 360, moon % 360


def ayanamsa(jd):
    """Lahiri ayanamsa, linear approximation (23.853 deg at J2000)."""
    return 23.853 + (jd - 2451545.0) / 365.25 * (50.2888 / 3600)


def positions(dt):
    """Sidereal sun and moon longitude (degrees) and their elongation."""
    jd = _jd(dt)
    sun, moon = _sun_moon(jd)
    ay = ayanamsa(jd)
    return (sun - ay) % 360, (moon - ay) % 360, (moon - sun) % 360


def _elong(dt):
    return positions(dt)[2]


def new_moons(start, end):
    """New-moon instants (aware UTC datetimes) between two dates."""
    out = []
    t = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    stop = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    prev = _elong(t)
    step = timedelta(hours=12)
    while t < stop:
        t2 = t + step
        e2 = _elong(t2)
        if e2 < prev:  # wrapped from ~360 to ~0: conjunction inside the step
            lo, hi = t, t2
            for _ in range(30):
                mid = lo + (hi - lo) / 2
                if _elong(mid) > 180:
                    lo = mid
                else:
                    hi = mid
            out.append(hi)
        t, prev = t2, e2
    return out


@lru_cache(maxsize=16)
def lunar_months(shaka_start_year):
    """Months of the year starting at Chaitra in Gregorian year `shaka_start_year`.

    Returns a list of dicts: index (0=Chaitra), name, devanagari, adhika, start, end
    (start/end are IST dates; end is the day of the closing new moon).
    """
    y = shaka_start_year
    nms = new_moons(date(y, 2, 1), date(y + 1, 5, 1))
    months = []
    for a, b in zip(nms, nms[1:]):
        ra = int(positions(a)[0] // 30)
        rb = int(positions(b)[0] // 30)
        idx = (ra + 1) % 12
        months.append({"index": idx, "adhika": ra == rb, "start_dt": a, "end_dt": b})
    # keep from the first Chaitra through the next Phalguna
    first = next(i for i, m in enumerate(months) if m["index"] == 0 and not m["adhika"]
                 and m["start_dt"].year == y)
    # an adhika Chaitra comes right before the nija one
    if first > 0 and months[first - 1]["index"] == 0 and months[first - 1]["adhika"]:
        first -= 1
    result = []
    for m in months[first:]:
        if result and m["index"] == 0 and not m["adhika"]:
            break
        if m["index"] == 0 and m["adhika"] and result and result[-1]["index"] == 11:
            break
        name, dev = MONTHS[m["index"]]
        result.append({
            "index": m["index"], "name": name, "devanagari": dev, "adhika": m["adhika"],
            "start_dt": m["start_dt"], "end_dt": m["end_dt"],
            "start": m["start_dt"].astimezone(IST).date(),
            "end": m["end_dt"].astimezone(IST).date(),
        })
    return result


def day_info(d):
    """Panchang values for a civil date, taken at 6:00 AM IST."""
    dt = datetime(d.year, d.month, d.day, 6, 0, tzinfo=IST)
    sun, moon, el = positions(dt)
    t = int(el // 12)  # 0..29
    paksha = "Shukla" if t < 15 else "Krishna"
    n = t % 15
    if t == 14:
        tithi = "Purnima"
    elif t == 29:
        tithi = "Amavasya"
    else:
        tithi = TITHIS[n]
    nk = int(moon // NAK_SPAN)
    pada = int((moon % NAK_SPAN) // (NAK_SPAN / 4)) + 1
    return {
        "date": d, "weekday": WEEKDAYS[d.weekday()], "tithi": tithi,
        "tithi_num": n + 1, "paksha": paksha,
        "nakshatra": NAKSHATRAS[nk][0], "nakshatra_dev": NAKSHATRAS[nk][1],
        "nakshatra_index": nk, "pada": pada,
        "moon_rashi": RASHIS[int(moon // 30)], "sun_rashi": RASHIS[int(sun // 30)],
    }


def _six_am(d):
    return datetime(d.year, d.month, d.day, 6, 0, tzinfo=IST)


def year_for(d):
    """Gregorian year whose Chaitra starts the Hindu year containing date d."""
    months = lunar_months(d.year)
    return d.year if _six_am(d) >= months[0]["start_dt"] else d.year - 1


def month_of(d):
    """The lunar month containing date d (judged at 6:00 AM IST)."""
    t = _six_am(d)
    for m in lunar_months(year_for(d)):
        if m["start_dt"] <= t < m["end_dt"]:
            return m
    return None


def month_days(m):
    """Civil dates whose 6:00 AM IST falls inside lunar month m."""
    d = m["start"]
    out = []
    while _six_am(d) < m["end_dt"]:
        if _six_am(d) >= m["start_dt"]:
            out.append(d)
        d += timedelta(days=1)
    return out
