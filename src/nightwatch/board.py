"""Data for the dashboard and the API (proposal v3 section 9; DECISIONS.md D018).

Shared by `app/dashboard.py` (Streamlit) and `nightwatch.api` (FastAPI). No web code here.

What the dashboard shows for each monitored point:
- the latest reading (the newest current-conditions block);
- the predicted night minimum with a range, from the PROVISIONAL model;
- the Low / Medium / High category of proposal v3 section 5 (alerts.category);
- the provider forecast minimum for the same 12 night hours;
- the newest field report.
And a data-health panel: last successful collection, slots with data in the last 24 hours, and
complete calendar nights so far (counts only).

The rules from the approval (protocol C1, `docs/evaluation_protocol.md`):
- The provisional model is chosen and fitted on the first 13 complete calendar nights only
  (`features.load(conn, "development")`). Later nights' outcomes are never read.
- Its predictions are stored with purpose `provisional_dashboard` and are never used in the
  evaluation.
- No accuracy figure is shown for any night after the first 13. The range comes from the
  cross-validation errors on the development nights.
- The dashboard never reads a night minimum (`target_min_c`) of the night it shows.

Night D can be predicted from 18:10 Tashkent on day D: its features need data received by 18:00
(the cut-off), and the 10 minutes let the last snapshot run finish saving. The live page then writes a
features-only placeholder row for night D (no outcome, `is_complete` false). The nights builder
replaces it with the real row after the night has ended, by its own rules (D010, D018).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import alerts, config, cv, features, models, nights, slots

TZ = nights.TZ
PURPOSE = "provisional_dashboard"
PROTOCOL_VERSION = "provisional-dev13"
AFTER_CUTOFF = timedelta(minutes=10)
RANGE_QUANTILES = (0.10, 0.90)       # the range holds 80% of the development CV errors
NEEDED_NIGHTS = 18                   # minimum complete calendar nights (approval, D005)
SLOTS_PER_DAY = 51

NOTE = ("These are weather-model values for one fixed point, not measurements of a farm, "
        "and this is not a frost warning.")
CREDIT = "Weather data by Open-Meteo.com, licence CC BY 4.0."
WORDING = ("Predictions on this page come from a provisional model and are stored as "
           "provisional_dashboard. They are never used in the evaluation.")

METHODS = ("thermometer", "weather station", "other")   # no free text, so no personal data
REPORT_LIMITS = (-50.0, 60.0)                           # same range check as the database

FEATURE_NAMES = {
    "snap_temp_c": "snapshot temperature", "snap_dewpoint_gap_c": "snapshot dew point",
    "snap_rh_pct": "snapshot humidity", "snap_wind_kmh": "snapshot wind",
    "snap_cloud_pct": "snapshot cloud cover", "snap_trend_3h_c": "3-hour trend",
    "day_max_temp_c": "day maximum", "morning_min_temp_c": "morning minimum",
    "afternoon_cloud_pct": "afternoon cloud cover",
}
NAMES = {loc: name for loc, name, *_ in config.LOCATIONS}


# ---------- which night ----------

def current_night(now: datetime) -> date:
    """The newest night whose features are ready: night D from 18:10 Tashkent on day D.

    After the data cut-off it stays on the last included night (2 Dec 2026, D013).
    """
    day = now.astimezone(TZ).date()
    if now < nights.windows(day).cutoff + AFTER_CUTOFF:
        day -= timedelta(days=1)
    return min(day, nights.last_included_night())


def night_label(night_date: date, now: datetime) -> str:
    w = nights.windows(night_date)
    span = f"{night_date:%d %b} 20:00 to {night_date + timedelta(days=1):%d %b} 07:00"
    return f"Tonight ({span})" if now < w.night[-1] else f"Last night ({span}, ended)"


# ---------- the provisional model ----------

@dataclass
class ProvisionalModel:
    name: str
    params: dict
    model: object
    cv_mae: float
    range_low: float        # add to the prediction: lower end of the range
    range_high: float       # add to the prediction: upper end of the range
    nights: list[date]

    @property
    def label(self) -> str:
        setting = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name} ({setting})" if setting else self.name


def train_provisional(conn) -> ProvisionalModel | None:
    """Choose and fit the model on the development nights only. None if there are too few."""
    table, _ = features.load(conn, "development")
    dates = sorted(table["night_date"].unique())
    if len(dates) <= cv.N_FOLDS * cv.FOLD_NIGHTS:          # cross-validation needs more nights
        return None
    results = cv.run_grid(table)
    chosen = cv.choose(results)
    errors = chosen.predictions["y"] - chosen.predictions["pred"]        # outcome minus prediction
    low, high = (float(np.quantile(errors, q)) for q in RANGE_QUANTILES)
    model = models.make(chosen.name, chosen.params).fit(table[features.FEATURES],
                                                        table[features.TARGET])
    return ProvisionalModel(chosen.name, dict(chosen.params), model, chosen.cv_mae, low, high, dates)


# ---------- the night shown ----------

PLACEHOLDER_COLUMNS = ("location_id", "night_date", "features_cutoff_utc", "snapshot_run_id",
                       "snapshot_received_at", "snapshot_trend_3h_c", "day_max_temp_c",
                       "morning_min_temp_c", "afternoon_cloud_pct", "n_forecast_hours",
                       "provider_forecast_min_c")

# Same columns as features.QUERY, but the outcome is never selected.
NIGHT_QUERY = """
SELECT n.night_id, n.night_date, n.location_id, n.features_cutoff_utc,
       c.response_received_at AS snapshot_received_at,
       c.temperature_c, c.dew_point_c, c.relative_humidity_pct, c.wind_speed_kmh, c.cloud_cover_pct,
       n.snapshot_trend_3h_c, n.day_max_temp_c, n.morning_min_temp_c, n.afternoon_cloud_pct,
       n.provider_forecast_min_c, NULL::numeric AS target_min_c
  FROM nights n
  LEFT JOIN current_conditions c ON c.location_id = n.location_id AND c.run_id = n.snapshot_run_id
 WHERE n.night_date = %s
 ORDER BY n.location_id
"""


def ensure_night_rows(conn, night_date: date, now: datetime) -> bool:
    """Write a features-only placeholder row per point, unless the night already has its rows.

    Only for a counted night whose features are ready by the real clock (`now`), so a demo time or
    an early call never writes a row. The night's own temperatures are never read
    (`build_night(features_only=True)`). The outcome columns stay empty and the row is never
    complete. An existing row (for example the builder's) is never changed. Nothing is written to
    the quality log. Returns True if rows were written.
    """
    if not (config.FIRST_NIGHT <= night_date <= nights.last_included_night()):
        return False
    if now < nights.windows(night_date).cutoff + AFTER_CUTOFF:
        return False
    have = conn.execute("SELECT count(*) FROM nights WHERE night_date = %s", (night_date,)).fetchone()[0]
    if have >= len(config.LOCATIONS):
        return False
    for row in nights.build_night(conn, night_date, features_only=True):
        conn.execute(
            f"""INSERT INTO nights ({", ".join(PLACEHOLDER_COLUMNS)}, target_min_c, n_live_hours,
                                    is_complete, calendar_night_complete)
                VALUES ({", ".join(["%s"] * len(PLACEHOLDER_COLUMNS))}, NULL, 0, false, false)
                ON CONFLICT (location_id, night_date) DO NOTHING""",
            [row[c] for c in PLACEHOLDER_COLUMNS])
    conn.commit()
    return True


def missing_reason(row) -> str:
    if pd.isna(row["snap_temp_c"]):
        return "no snapshot received 16:00-18:00"
    gaps = [FEATURE_NAMES[c] for c in features.NUMERIC if pd.isna(row[c])]
    return "missing " + ", ".join(gaps)


def predict_night(conn, night_date: date, model: ProvisionalModel | None, now: datetime,
                  write: bool = True) -> list[dict]:
    """One entry per point for the night: prediction, range, category and provider forecast.

    write=True (the live page and the API, with the real clock): write the placeholder rows if
    needed and store new predictions once (purpose provisional_dashboard); the stored value is shown.
    write=False (demo time): nothing is written; a stored prediction is shown if there is one,
    otherwise the model's prediction is worked out and shown without storing it.
    """
    if write:
        ensure_night_rows(conn, night_date, now)
    raw = pd.DataFrame(conn.execute(NIGHT_QUERY, (night_date,)).fetchall(), columns=features.RAW_COLUMNS)
    for column in ("features_cutoff_utc", "snapshot_received_at"):     # a missing snapshot -> NaT
        raw[column] = pd.to_datetime(raw[column], utc=True)
    frame = features.feature_frame(raw)
    ready = frame[features.NUMERIC].notna().all(axis=1)

    fresh = {}
    if model is not None and ready.any():
        rows = frame[ready]
        fresh = {int(i): round(float(v), 2)
                 for i, v in zip(rows["night_id"], model.model.predict(rows[features.FEATURES]))}
    if write and fresh:
        for night_id, value in fresh.items():
            conn.execute(
                """INSERT INTO predictions (night_id, model_name, protocol_version, purpose, predicted_min_c)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (night_id, model_name, purpose, protocol_version) DO NOTHING""",
                (night_id, model.label, PROTOCOL_VERSION, PURPOSE, value))
        conn.commit()

    shown = {night_id: (float(value), name) for night_id, value, name in conn.execute(
        """SELECT DISTINCT ON (p.night_id) p.night_id, p.predicted_min_c, p.model_name
             FROM predictions p JOIN nights n USING (night_id)
            WHERE n.night_date = %s AND p.purpose = %s AND p.protocol_version = %s
            ORDER BY p.night_id, p.created_at DESC""",
        (night_date, PURPOSE, PROTOCOL_VERSION))}
    for night_id, value in fresh.items():
        shown.setdefault(night_id, (value, model.label))

    out = []
    for index, row in frame.iterrows():
        entry = {"location_id": int(row["location_id"]), "point": NAMES[int(row["location_id"])],
                 "night_date": night_date, "predicted_min_c": None, "range_low_c": None,
                 "range_high_c": None, "category": None, "model": None,
                 "provider_forecast_min_c": None if pd.isna(row[features.PROVIDER])
                 else round(float(row[features.PROVIDER]), 1),
                 "reason": None}
        if int(row["night_id"]) in shown:
            value, name = shown[int(row["night_id"])]
            value = round(value, 1)               # the category follows the value the page shows
            entry.update(predicted_min_c=value, category=alerts.category(value), model=name)
            if model is not None and name == model.label:
                entry.update(range_low_c=round(value + model.range_low, 1),
                             range_high_c=round(value + model.range_high, 1))
        elif not ready[index]:
            entry["reason"] = missing_reason(row)
        else:
            entry["reason"] = "the provisional model is not ready yet"
        out.append(entry)
    return out


def latest_readings(conn, now: datetime) -> dict[int, dict]:
    """The newest current-conditions block per point, received by `now`."""
    cur = conn.execute(
        """SELECT DISTINCT ON (location_id) location_id, valid_time, response_received_at,
                  temperature_c, relative_humidity_pct, wind_speed_kmh, cloud_cover_pct
             FROM current_conditions
            WHERE response_received_at <= %s
            ORDER BY location_id, response_received_at DESC, run_id DESC""", (now,))
    names = [c.name for c in cur.description]
    return {row[0]: dict(zip(names, row)) for row in cur.fetchall()}


# ---------- data health (counts only, no temperatures) ----------

def data_health(conn, now: datetime) -> dict:
    last_ok, last_run = conn.execute(
        """SELECT max(response_received_at) FILTER (WHERE points_ok = %s), max(request_sent_at)
             FROM collection_runs WHERE trigger <> 'test' AND request_sent_at <= %s""",
        (len(config.LOCATIONS), now)).fetchone()

    end = min(now - slots.GRACE, config.FINAL_COLLECTION_UTC)     # no slots after the cut-off
    expected = slots.expected_slots(end - timedelta(hours=24), end)
    with_data = {s for (s,) in conn.execute(
        """SELECT DISTINCT scheduled_for FROM collection_runs
            WHERE points_ok > 0 AND scheduled_for BETWEEN %s AND %s""",
        (expected[0] if expected else end, end))}
    data_slots = sum(1 for s in expected if s in with_data)

    complete = features.complete_calendar_nights(conn)
    last = nights.last_included_night()
    done = nights.finished_nights(now)
    next_night = done[-1] + timedelta(days=1) if done else config.FIRST_NIGHT
    left = max(0, (last - next_night).days + 1)
    return {
        "last_successful_collection": last_ok,
        "last_run_started": last_run,
        "slots_last_24h": len(expected),
        "slots_with_data_last_24h": data_slots,
        "complete_calendar_nights": len(complete),
        "needed_nights": NEEDED_NIGHTS,
        "nights_left": left,
        "most_possible_nights": len(complete) + left,
        "last_included_night": last,
        "collection_closed": now >= config.FINAL_COLLECTION_UTC,
    }


# ---------- field reports (the "add a new record" input; never used in the evaluation) ----------

def validate_report(location_id, night_date, measured_low_c, method, today: date) -> list[str]:
    errors = []
    if location_id not in NAMES:
        errors.append("choose one of the ten monitored points")
    if not isinstance(night_date, date) or not (config.FIRST_NIGHT <= night_date <= today):
        errors.append(f"the night must be between {config.FIRST_NIGHT} and today")
    try:
        value = float(measured_low_c)
        if not np.isfinite(value) or not (REPORT_LIMITS[0] <= value <= REPORT_LIMITS[1]):
            errors.append(f"the low temperature must be between {REPORT_LIMITS[0]:.0f} and "
                          f"{REPORT_LIMITS[1]:.0f} °C")
    except (TypeError, ValueError):
        errors.append("the low temperature must be a number")
    if method not in METHODS:
        errors.append("choose a method from the list")
    return errors


def add_field_report(conn, location_id: int, night_date: date, measured_low_c: float, method: str,
                     now: datetime | None = None) -> dict:
    """Validate and store one field report. Raises ValueError with the reasons if it is not valid."""
    now = now or datetime.now(timezone.utc)
    errors = validate_report(location_id, night_date, measured_low_c, method, now.astimezone(TZ).date())
    if errors:
        raise ValueError("; ".join(errors))
    report_id, reported_at = conn.execute(
        """INSERT INTO field_reports (location_id, night_date, measured_low_c, method)
           VALUES (%s, %s, %s, %s) RETURNING report_id, reported_at""",
        (location_id, night_date, round(float(measured_low_c), 1), method)).fetchone()
    conn.commit()
    return {"report_id": report_id, "location_id": location_id, "point": NAMES[location_id],
            "night_date": night_date, "measured_low_c": round(float(measured_low_c), 1),
            "method": method, "reported_at": reported_at}


def latest_report_per_point(conn) -> dict[int, dict]:
    """The newest field report of each point (any age)."""
    cur = conn.execute(
        """SELECT DISTINCT ON (location_id) location_id, night_date, measured_low_c, reported_at
             FROM field_reports ORDER BY location_id, reported_at DESC, report_id DESC""")
    return {loc: {"night_date": d, "measured_low_c": float(v), "reported_at": t}
            for loc, d, v, t in cur.fetchall()}


def field_reports(conn, limit: int = 20) -> list[dict]:
    cur = conn.execute(
        """SELECT report_id, location_id, night_date, measured_low_c, method, reported_at
             FROM field_reports ORDER BY reported_at DESC, report_id DESC LIMIT %s""",
        (limit,))
    names = [c.name for c in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    for r in rows:
        r["point"] = NAMES.get(r["location_id"], str(r["location_id"]))
        r["measured_low_c"] = float(r["measured_low_c"])
    return rows
