"""Hindu lunar calendar (amanta, Chaitra to Phalguna) with tithi, nakshatra and Agni.

Astronomy comes from the `ephem` library (PyEphem): apparent sun and moon positions,
new moons, precession and local sunrise. Each civil day's panchang is read at sunrise
at the farm (Sangli by default; set FARM_LOCATION, FARM_LAT, FARM_LON and
FARM_ELEVATION to change it). Sidereal positions use the Lahiri (Chitrapaksha)
ayanamsa from its official definition: 23 deg 15' 00.658" on 21 March 1956, plus
precession since then as computed by ephem.
"""
import math
import os
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import ephem

IST = timezone(timedelta(hours=5, minutes=30))

LOCATION = os.environ.get("FARM_LOCATION", "Sangli")
LAT = os.environ.get("FARM_LAT", "16.8524")
LON = os.environ.get("FARM_LON", "74.5815")
ELEVATION = float(os.environ.get("FARM_ELEVATION", "549"))  # metres

LAHIRI_1956 = 23 + 15 / 60 + 0.658 / 3600  # ayanamsa at the Lahiri epoch below
LAHIRI_EPOCH = "1956/3/21"

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
PADA_SPAN = NAK_SPAN / 4

# Agnivas: सैका तिथिर्वारयुता कृताप्ता शेषे गुणेऽभ्रे भुवि वह्निवासः ।
#          सौख्याय होमे शशियुग्मशेषे प्राणार्थनाशौ दिवि भूतले च ॥
# (tithi from Shukla Pratipada + 1 + weekday from Sunday) mod 4:
# 3 or 0 -> earth (good for homa), 1 -> sky (harm to life), 2 -> underworld (loss of wealth).
AGNIVAS = {
    0: ("Prithvi", "पृथ्वी", True, "Agni on earth: good for havan"),
    3: ("Prithvi", "पृथ्वी", True, "Agni on earth: good for havan"),
    1: ("Akasha", "आकाश", False, "Agni in the sky: havan not advised (prana-nasha)"),
    2: ("Patala", "पाताल", False, "Agni in the underworld: havan not advised (artha-nasha)"),
}
# Agni Nakshatra (Kathiri): the sun from Bharani pada 3 to the end of Rohini pada 1.
AGNI_NAKSHATRA = (1 * NAK_SPAN + 2 * PADA_SPAN, 3 * NAK_SPAN + 1 * PADA_SPAN)  # 20 deg to 43 deg 20'


def _ephem_date(dt):
    return ephem.Date(dt.astimezone(timezone.utc).replace(tzinfo=None))


def _from_ephem(d):
    return ephem.Date(d).datetime().replace(tzinfo=timezone.utc)


def _longitude(body, when):
    """Apparent geocentric ecliptic longitude (degrees, equinox of date)."""
    body.compute(when, epoch=when)
    eq = ephem.Equatorial(body.g_ra, body.g_dec, epoch=when)
    return math.degrees(ephem.Ecliptic(eq).lon)


def ayanamsa(when):
    """Lahiri ayanamsa (degrees) at an ephem date or aware datetime."""
    if isinstance(when, datetime):
        when = _ephem_date(when)
    start = ephem.Equatorial(ephem.Ecliptic(0, 0, epoch=LAHIRI_EPOCH), epoch=LAHIRI_EPOCH)
    precession = math.degrees(ephem.Ecliptic(start, epoch=when).lon)
    return LAHIRI_1956 + precession


def positions(dt):
    """Sidereal sun and moon longitude (degrees) and their elongation, at an aware datetime."""
    when = _ephem_date(dt)
    sun = _longitude(ephem.Sun(), when)
    moon = _longitude(ephem.Moon(), when)
    ay = ayanamsa(when)
    return (sun - ay) % 360, (moon - ay) % 360, (moon - sun) % 360


def new_moons(start, end):
    """New-moon instants (aware UTC datetimes) between two dates."""
    out = []
    t = ephem.Date(datetime(start.year, start.month, start.day))
    stop = ephem.Date(datetime(end.year, end.month, end.day))
    while True:
        t = ephem.next_new_moon(t)
        if t >= stop:
            return out
        out.append(_from_ephem(t))


@lru_cache(maxsize=4096)
def sunrise(d):
    """Sunrise (aware IST datetime) at the farm on civil date d: upper limb, standard refraction."""
    obs = ephem.Observer()
    obs.lat, obs.lon, obs.elevation = LAT, LON, ELEVATION
    obs.pressure = 0          # refraction is in the horizon value instead
    obs.horizon = "-0:34"
    obs.date = _ephem_date(datetime(d.year, d.month, d.day, tzinfo=IST))
    try:
        return _from_ephem(obs.next_rising(ephem.Sun(), use_center=False)).astimezone(IST)
    except (ephem.AlwaysUpError, ephem.NeverUpError):  # not possible in India; polar fallback
        return datetime(d.year, d.month, d.day, 6, 0, tzinfo=IST)


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


def agnivas(tithi_index, d):
    """Where Agni resides on date d. tithi_index: 1 (Shukla Pratipada) to 30 (Amavasya)."""
    vara = (d.weekday() + 1) % 7 + 1  # Sunday = 1 ... Saturday = 7
    place, dev, good, note = AGNIVAS[(tithi_index + 1 + vara) % 4]
    return {"place": place, "devanagari": dev, "good_for_havan": good, "note": note}


@lru_cache(maxsize=4096)
def day_info(d):
    """Panchang values for a civil date, taken at sunrise at the farm."""
    rise = sunrise(d)
    sun, moon, el = positions(rise)
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
    pada = int((moon % NAK_SPAN) // PADA_SPAN) + 1
    return {
        "date": d, "sunrise": rise, "weekday": WEEKDAYS[d.weekday()], "tithi": tithi,
        "tithi_num": n + 1, "tithi_index": t + 1, "paksha": paksha,
        "nakshatra": NAKSHATRAS[nk][0], "nakshatra_dev": NAKSHATRAS[nk][1],
        "nakshatra_index": nk, "pada": pada,
        "moon_rashi": RASHIS[int(moon // 30)], "sun_rashi": RASHIS[int(sun // 30)],
        "agni": agnivas(t + 1, d),
        "agni_nakshatra": AGNI_NAKSHATRA[0] <= sun < AGNI_NAKSHATRA[1],
    }


def _crossing(angle, target, t0, t1):
    """The moment between t0 and t1 when angle(t) (degrees, increasing) reaches `target`, to within 20 seconds.

    Bisection; the caller makes sure the angle is short of the target at t0 and past it at t1.
    """
    def reached(t):
        return (angle(t) - target) % 360 < 180

    lo, hi = t0, t1
    while hi - lo > timedelta(seconds=20):
        mid = lo + (hi - lo) / 2
        if reached(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _hindu_day(d):
    """A Hindu day runs from sunrise on d to the next sunrise."""
    return sunrise(d), sunrise(d + timedelta(days=1))


@lru_cache(maxsize=4096)
def tithi_changes(d):
    """The tithis that begin between sunrise on d and the next sunrise: [(time IST, tithi index 1..30)].

    Usually one; none when a tithi lasts the whole day; two when one is skipped (kshaya).
    """
    start, end = _hindu_day(d)
    k, last = int(positions(start)[2] // 12), int(positions(end)[2] // 12)
    out = []
    while k != last:
        k = (k + 1) % 30
        when = _crossing(lambda t: positions(t)[2], k * 12, start, end)
        out.append((when.astimezone(IST), k + 1))
    return out


@lru_cache(maxsize=4096)
def agni_periods(d):
    """Where Agni is through the day, from sunrise on d to the next sunrise, with the times it moves.

    Agnivas comes from the tithi and the weekday; the weekday holds until the next sunrise, so Agni moves
    only when the tithi changes. [{"start", "end", "agni"}] (IST), joined where Agni stays in one place.
    """
    start, end = _hindu_day(d)
    edges = [(start, day_info(d)["tithi_index"])] + list(tithi_changes(d))
    periods = []
    for i, (when, tithi) in enumerate(edges):
        until = edges[i + 1][0] if i + 1 < len(edges) else end
        agni = agnivas(tithi, d)
        if periods and periods[-1]["agni"]["place"] == agni["place"]:
            periods[-1]["end"] = until
        else:
            periods.append({"start": when, "end": until, "agni": agni})
    return periods


@lru_cache(maxsize=4096)
def agni_nakshatra_change(d):
    """("begins" or "ends", time IST) when the sun enters or leaves Agni Nakshatra between sunrise on d and
    the next sunrise; None on other days."""
    start, end = _hindu_day(d)
    s0, s1 = positions(start)[0], positions(end)[0]
    for kind, edge in (("begins", AGNI_NAKSHATRA[0]), ("ends", AGNI_NAKSHATRA[1])):
        if s0 < edge <= s1:
            return kind, _crossing(lambda t: positions(t)[0], edge, start, end).astimezone(IST)
    return None


def year_for(d):
    """Gregorian year whose Chaitra starts the Hindu year containing date d."""
    months = lunar_months(d.year)
    return d.year if sunrise(d) >= months[0]["start_dt"] else d.year - 1


def month_of(d):
    """The lunar month containing date d (judged at sunrise)."""
    t = sunrise(d)
    for m in lunar_months(year_for(d)):
        if m["start_dt"] <= t < m["end_dt"]:
            return m
    return None


def month_days(m):
    """Civil dates whose sunrise falls inside lunar month m."""
    d = m["start"]
    out = []
    while sunrise(d) < m["end_dt"]:
        if sunrise(d) >= m["start_dt"]:
            out.append(d)
        d += timedelta(days=1)
    return out
