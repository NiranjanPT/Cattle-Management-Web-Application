"""The Hindu calendar: checked against printed panchangs, Agnivas and Agni Nakshatra rules."""
from datetime import date, time, timedelta

import pytest

import panchang


@pytest.mark.parametrize("d,month,paksha,tithi,nakshatra", [
    (date(2024, 1, 22), "Pausha", "Shukla", "Dwadashi", "Mrigashira"),
    (date(2024, 3, 25), "Phalguna", "Shukla", "Purnima", None),
    (date(2025, 10, 21), "Ashvin", "Krishna", "Amavasya", None),
    (date(2023, 1, 21), "Pausha", "Krishna", "Amavasya", None),  # Mauni Amavasya: Amavasya at Sangli sunrise
])
def test_known_days(d, month, paksha, tithi, nakshatra):
    info = panchang.day_info(d)
    assert panchang.month_of(d)["name"] == month
    assert (info["paksha"], info["tithi"]) == (paksha, tithi)
    if nakshatra:
        assert info["nakshatra"] == nakshatra


@pytest.mark.parametrize("year,adhika", [(2023, ["Shravana"]), (2024, []), (2025, []), (2026, ["Jyeshtha"])])
def test_adhika_months(year, adhika):
    assert [m["name"] for m in panchang.lunar_months(year) if m["adhika"]] == adhika


def test_sunrise_is_sangli_sunrise():
    rises = [panchang.sunrise(date(2026, 1, 1) + timedelta(days=i)) for i in range(365)]
    assert all(time(5, 50) <= r.time() <= time(7, 10) for r in rises)  # Sangli: 05:57 (June) to 07:04 (Jan)
    assert min(rises, key=lambda r: r.time()).month == 6   # earliest in early June
    assert max(rises, key=lambda r: r.time()).month == 1   # latest in January


def test_lahiri_ayanamsa():
    import ephem
    assert panchang.ayanamsa(ephem.Date("1956/3/21")) == pytest.approx(23.2502, abs=1e-4)  # the definition
    assert panchang.ayanamsa(ephem.Date("2000/1/1")) == pytest.approx(23.857, abs=0.01)    # published J2000 value


SUNDAY = date(2026, 10, 25)


# (tithi from Shukla Pratipada + 1 + weekday from Sunday=1) mod 4: 3 or 0 earth, 1 sky, 2 underworld.
# Expected places worked out by hand, not with the code under test.
@pytest.mark.parametrize("tithi_index,days_after_sunday,place", [
    (1, 0, "Prithvi"),    # Shukla Pratipada, Sunday: 1 + 1 + 1 = 3
    (2, 0, "Prithvi"),    # Dwitiya, Sunday: 4 -> 0
    (3, 0, "Akasha"),     # Tritiya, Sunday: 5 -> 1
    (4, 0, "Patala"),     # Chaturthi, Sunday: 6 -> 2
    (15, 1, "Patala"),    # Purnima, Monday: 15 + 1 + 2 = 18 -> 2
    (30, 6, "Patala"),    # Amavasya, Saturday: 30 + 1 + 7 = 38 -> 2
    (11, 4, "Akasha"),    # Shukla Ekadashi, Thursday: 11 + 1 + 5 = 17 -> 1
    (11, 6, "Prithvi"),   # Shukla Ekadashi, Saturday: 11 + 1 + 7 = 19 -> 3
])
def test_agnivas_rule(tithi_index, days_after_sunday, place):
    assert SUNDAY.weekday() == 6
    a = panchang.agnivas(tithi_index, SUNDAY + timedelta(days=days_after_sunday))
    assert a["place"] == place
    assert a["good_for_havan"] == (place == "Prithvi")


def test_every_day_has_agni():
    for i in range(60):
        a = panchang.day_info(date(2026, 10, 1) + timedelta(days=i))["agni"]
        assert a["place"] in ("Prithvi", "Akasha", "Patala")
        assert a["good_for_havan"] == (a["place"] == "Prithvi")


def test_agni_nakshatra_is_early_to_late_may():
    flagged = [d for d in (date(2026, 1, 1) + timedelta(days=i) for i in range(365))
               if panchang.day_info(d)["agni_nakshatra"]]
    assert date(2026, 5, 3) <= flagged[0] <= date(2026, 5, 5)
    assert date(2026, 5, 27) <= flagged[-1] <= date(2026, 5, 29)
    assert flagged == [flagged[0] + timedelta(days=i) for i in range(len(flagged))]  # one unbroken period


# ------------------------------------------------------------------ the times Agni moves

def test_tithi_changes_fall_on_12_degree_steps():
    for i in range(90):
        d = date(2026, 1, 1) + timedelta(days=i)
        start, end = panchang.sunrise(d), panchang.sunrise(d + timedelta(days=1))
        for when, tithi in panchang.tithi_changes(d):
            el = panchang.positions(when)[2]
            assert abs((el - (tithi - 1) * 12 + 180) % 360 - 180) < 0.01  # within 20 s of the moon's motion
            assert start < when < end


def test_pratipada_begins_at_the_new_moon():
    for nm in panchang.new_moons(date(2026, 1, 1), date(2026, 12, 31)):
        nm = nm.astimezone(panchang.IST)
        d = nm.date() if nm >= panchang.sunrise(nm.date()) else nm.date() - timedelta(days=1)  # its Hindu day
        starts = [when for when, tithi in panchang.tithi_changes(d) if tithi == 1]
        assert len(starts) == 1 and abs(starts[0] - nm) < timedelta(minutes=1)


def test_agni_periods_cover_the_day_and_move_with_the_tithi():
    skipped = 0
    for i in range(365):
        d = date(2026, 1, 1) + timedelta(days=i)
        periods, changes = panchang.agni_periods(d), panchang.tithi_changes(d)
        assert periods[0]["start"] == panchang.sunrise(d)
        assert periods[-1]["end"] == panchang.sunrise(d + timedelta(days=1))
        assert all(a["end"] == b["start"] for a, b in zip(periods, periods[1:]))  # no gaps
        assert all(a["agni"]["place"] != b["agni"]["place"] for a, b in zip(periods, periods[1:]))
        assert periods[0]["agni"] == panchang.day_info(d)["agni"]  # the sunrise value, as before
        assert {p["start"] for p in periods[1:]} <= {when for when, _ in changes}  # Agni moves only with the tithi
        skipped += len(changes) == 2
    assert skipped > 5  # days with a skipped (kshaya) tithi have two changes


def test_agni_on_7_november_2026():
    # Saturday (vara 7): Krishna Trayodashi (28) -> 36 % 4 = 0, earth; Chaturdashi (29) from 10:48 -> 37 % 4 = 1, sky
    periods = panchang.agni_periods(date(2026, 11, 7))
    assert [p["agni"]["place"] for p in periods] == ["Prithvi", "Akasha"]
    assert periods[1]["start"].strftime("%H:%M") in ("10:47", "10:48", "10:49")


def test_agni_nakshatra_begins_and_ends_with_times():
    found = {}
    for i in range(45):
        d = date(2026, 4, 25) + timedelta(days=i)
        change = panchang.agni_nakshatra_change(d)
        if change:
            found[change[0]] = (d, change[1])
    (start_day, start), (end_day, end) = found["begins"], found["ends"]
    assert date(2026, 5, 3) <= start_day <= date(2026, 5, 5) and date(2026, 5, 27) <= end_day <= date(2026, 5, 29)
    assert abs(panchang.positions(start)[0] - panchang.AGNI_NAKSHATRA[0]) < 0.001
    assert abs(panchang.positions(end)[0] - panchang.AGNI_NAKSHATRA[1]) < 0.001
    assert not panchang.day_info(start_day)["agni_nakshatra"]  # it begins after that sunrise
    assert panchang.day_info(start_day + timedelta(days=1))["agni_nakshatra"]
    assert panchang.day_info(end_day)["agni_nakshatra"] and not panchang.day_info(end_day + timedelta(days=1))["agni_nakshatra"]
