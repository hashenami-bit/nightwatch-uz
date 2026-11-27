"""NightWatch UZ dashboard (proposal v3 section 9; DECISIONS.md D018).

Run:  uv run streamlit run app/dashboard.py
Optional: NIGHTWATCH_SHOW_TIME="2026-11-27T21:00+05:00" shows the page as at that time (demos).
The database address comes from the DATABASE_URL environment variable or from
.streamlit/secrets.toml (DATABASE_URL = "..."). That file is git-ignored: never commit it.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import psycopg
import streamlit as st

from nightwatch import board, config, db

st.set_page_config(page_title="NightWatch UZ", page_icon="🌙", layout="wide")

INK, MUTED, RULE = "#17233B", "#5B6577", "#C3C9D4"
CATEGORY_COLORS = {"Low": "#0ca30c", "Medium": "#fab219", "High": "#d03b3b"}   # status palette
CATEGORY_TEXT = {"Low": "Low (above 8 °C)", "Medium": "Medium (5 to 8 °C)",
                 "High": "High: alert (5 °C or below)"}

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
html, body, [class*="st-"], .stMarkdown, button, input { font-family: 'Atkinson Hyperlegible', system-ui, sans-serif; }
.nw-band { background: #22314F; color: #F4F6F9; border-radius: 6px; padding: 1.4rem 1.6rem 1.2rem; margin-bottom: 1rem; }
.nw-band h1 { color: #F4F6F9; font-size: 2.1rem; line-height: 1.15; margin: 0 0 .35rem; font-weight: 700; padding: 0; }
.nw-band p { margin: 0; color: #C9D2E3; font-size: 1.05rem; max-width: 70ch; }
.nw-note { border-left: 4px solid #22314F; background: #E6EAF0; padding: .7rem 1rem; border-radius: 0 6px 6px 0;
           color: #17233B; max-width: 80ch; margin-bottom: 1.2rem; }
.nw-small { color: #5B6577; font-size: .92rem; max-width: 80ch; }
@media (max-width: 640px) { .nw-band h1 { font-size: 1.5rem; } .nw-band { padding: 1rem 1.1rem; } }
</style>
""", unsafe_allow_html=True)


def database_url() -> str | None:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    try:
        return st.secrets.get("DATABASE_URL")
    except Exception:          # no secrets file
        return None


@st.cache_resource(ttl=3600, show_spinner="Training the provisional model on the first 13 nights…")
def provisional_model(dsn: str):
    with db.connect(dsn) as conn:
        return board.train_provisional(conn)


def local(ts) -> str:
    if ts is None or pd.isna(ts):
        return "none yet"
    return pd.Timestamp(ts).tz_convert(board.TZ).strftime("%d %b %H:%M")


def minutes_ago(ts, now) -> str:
    if ts is None:
        return "none yet"
    minutes = int((now - ts).total_seconds() // 60)
    return f"{minutes} min ago" if minutes < 120 else f"{minutes // 60} h ago"


DSN = database_url()
if not DSN:
    st.error("The dashboard has no database address. Set DATABASE_URL, or put "
             'DATABASE_URL = "..." in .streamlit/secrets.toml (git-ignored), then reload.')
    st.stop()

now = datetime.now(timezone.utc)
SHOW_TIME = os.environ.get("NIGHTWATCH_SHOW_TIME")      # optional, e.g. "2026-11-27T21:00+05:00"
if SHOW_TIME:
    try:
        shown_at = datetime.fromisoformat(SHOW_TIME)
    except ValueError:
        st.error(f"NIGHTWATCH_SHOW_TIME is not a date and time: {SHOW_TIME!r}. "
                 "Use the form 2026-11-27T21:00+05:00.")
        st.stop()
    if shown_at.tzinfo is None:                          # no offset given: read it as Tashkent time
        shown_at = shown_at.replace(tzinfo=board.TZ)
    now = shown_at.astimezone(timezone.utc)
    st.warning(f"Showing the page as it was at {local(now)} Tashkent time (NIGHTWATCH_SHOW_TIME). "
               "Nothing is written to the database in this mode, except field reports you add.")
WRITE = not SHOW_TIME            # only the live page, with the real clock, stores predictions

try:
    model = provisional_model(DSN)
    with db.connect(DSN) as conn:
        night_date = board.current_night(now)
        points = pd.DataFrame(board.predict_night(conn, night_date, model, now, write=WRITE))
        readings = board.latest_readings(conn, now)
        health = board.data_health(conn, now)
        reports = board.field_reports(conn, limit=20)
        latest_report = board.latest_report_per_point(conn)
except psycopg.Error:
    st.error("Cannot reach the database right now. Check the internet connection and DATABASE_URL, "
             "then reload the page.")
    st.stop()

# ---------- headline ----------
predicted = points.dropna(subset=["predicted_min_c"]) if len(points) else points
n_high = int((predicted["category"] == "High").sum()) if len(predicted) else 0
if len(predicted) == 0:
    summary = "No predictions yet for this night. The reasons are in the table below."
elif n_high == 0:
    summary = f"No point is at High. The coldest predicted minimum is {predicted['predicted_min_c'].min():.1f} °C."
else:
    names = ", ".join(predicted.loc[predicted["category"] == "High", "point"])
    summary = f"{n_high} of {len(predicted)} points are at High (5 °C or below): {names}."

st.markdown(f"""<div class="nw-band"><h1>{html.escape(board.night_label(night_date, now))}</h1>
<p>{html.escape(summary)}</p></div>""", unsafe_allow_html=True)
st.markdown(f'<div class="nw-note">{board.NOTE}</div>', unsafe_allow_html=True)

# ---------- the chart: predicted minimum and range per point ----------
st.subheader("Predicted night minimum by point")
if len(predicted):
    data = predicted.assign(category_text=predicted["category"].map(CATEGORY_TEXT))
    order = list(data.sort_values("predicted_min_c")["point"])
    y = alt.Y("point:N", sort=order, title=None, axis=alt.Axis(labelFontSize=13, labelColor=INK,
                                                                ticks=False, domain=False))
    color = alt.Color("category:N", title="Category",
                      scale=alt.Scale(domain=list(CATEGORY_COLORS), range=list(CATEGORY_COLORS.values())),
                      legend=alt.Legend(orient="top", labelExpr=
                                        "datum.label == 'High' ? 'High: alert, 5 °C or below' : "
                                        "datum.label == 'Medium' ? 'Medium, 5 to 8 °C' : 'Low, above 8 °C'"))
    tooltip = [alt.Tooltip("point:N", title="Point"), alt.Tooltip("category_text:N", title="Category"),
               alt.Tooltip("predicted_min_c:Q", title="Predicted minimum °C", format=".1f"),
               alt.Tooltip("range_low_c:Q", title="Range from °C", format=".1f"),
               alt.Tooltip("range_high_c:Q", title="Range to °C", format=".1f"),
               alt.Tooltip("provider_forecast_min_c:Q", title="Provider forecast °C", format=".1f")]
    values = pd.concat([data["range_low_c"], data["range_high_c"], data["predicted_min_c"],
                        data["provider_forecast_min_c"], pd.Series([4.0, 9.0])]).dropna()
    scale = alt.Scale(domain=[float(values.min()) - 1, float(values.max()) + 1], nice=False, zero=False)
    axis = alt.Axis(grid=True, gridColor="#E1E5EC", labelColor=MUTED, titleColor=MUTED, tickCount=8)
    x = alt.X("predicted_min_c:Q", title="°C", scale=scale, axis=axis)
    base = alt.Chart(data)
    limits = alt.Chart(pd.DataFrame({"c": [5.0, 8.0], "label": ["5 °C: alert limit", "8 °C"]}))
    chart = alt.layer(
        limits.mark_rule(color=RULE, strokeDash=[4, 3], strokeWidth=1.5).encode(x=alt.X("c:Q", scale=scale)),
        limits.mark_text(align="left", dx=4, dy=-6, color=MUTED, fontSize=12).encode(
            x=alt.X("c:Q", scale=scale), text="label:N", y=alt.value(0)),
        base.mark_rule(strokeWidth=4, strokeCap="round", opacity=0.45).encode(
            x=alt.X("range_low_c:Q", scale=scale), x2="range_high_c:Q", y=y, color=color, tooltip=tooltip),
        base.mark_point(shape="diamond", filled=False, size=110, strokeWidth=2, color=INK).encode(
            x=alt.X("provider_forecast_min_c:Q", scale=scale), y=y, tooltip=tooltip),
        base.mark_circle(size=150, opacity=1, stroke="white", strokeWidth=2).encode(
            x=x, y=y, color=color, tooltip=tooltip),
    ).properties(height=36 * len(data) + 40).configure(
        font="Atkinson Hyperlegible").configure_view(stroke=None).configure_legend(
        labelFontSize=13, titleFontSize=13, labelColor=INK, titleColor=INK)
    st.altair_chart(chart, width="stretch")
    st.markdown(
        f'<p class="nw-small">Dot: predicted minimum. Bar: the range where 80% of the model\'s errors '
        f'fell on the {len(model.nights) if model else 0} development nights. Hollow diamond: the provider\'s '
        f'own forecast minimum for the same 12 night hours. Model: {html.escape(model.label) if model else "none"}.</p>',
        unsafe_allow_html=True)
else:
    st.info("Nothing to draw yet. A prediction appears at 18:10 Tashkent time, after the 18:00 snapshot.")

# ---------- the table: every point, also the ones with no prediction ----------
rows = []
for p in points.to_dict("records"):
    r = readings.get(p["location_id"], {})
    report = latest_report.get(p["location_id"])
    predicted_ok = p["predicted_min_c"] is not None and not pd.isna(p["predicted_min_c"])
    rows.append({
        "Point": p["point"],
        "Category": CATEGORY_TEXT[p["category"]] if predicted_ok else f"No prediction: {p['reason']}",
        "Predicted min °C": p["predicted_min_c"] if predicted_ok else None,
        "Range °C": (f"{p['range_low_c']:.1f} to {p['range_high_c']:.1f}"
                     if predicted_ok and p["range_low_c"] is not None else ""),
        "Provider forecast min °C": p["provider_forecast_min_c"],
        "Latest reading": (f"{float(r['temperature_c']):.1f} °C at {local(r['response_received_at'])}"
                           if r.get("temperature_c") is not None else "none"),
        "Latest field report": (f"{report['measured_low_c']:.1f} °C, night of {report['night_date']:%d %b}"
                                if report else ""),
    })
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
             column_config={"Predicted min °C": st.column_config.NumberColumn(format="%.1f"),
                            "Provider forecast min °C": st.column_config.NumberColumn(format="%.1f")})
st.markdown(f'<p class="nw-small">{board.WORDING} No accuracy figure is shown for nights after the first '
            f'13 complete nights.</p>', unsafe_allow_html=True)

# ---------- data health and field reports ----------
left, right = st.columns([1, 1], gap="large")
with left:
    st.subheader("Data health")
    a, b = st.columns(2)
    a.metric("Last successful collection", minutes_ago(health["last_successful_collection"], now),
             help=f"All 10 points stored. Time: {local(health['last_successful_collection'])} Tashkent.")
    b.metric("Slots with data, last 24 h",
             f"{health['slots_with_data_last_24h']} of {health['slots_last_24h']}",
             help="The timer starts the collector 51 times a day.")
    c, d = st.columns(2)
    c.metric("Complete nights", f"{health['complete_calendar_nights']} of {health['needed_nights']} needed")
    d.metric("Nights still to collect", health["nights_left"],
             help=f"Most possible: {health['most_possible_nights']} complete nights. "
                  f"Last included night: {health['last_included_night']:%d %b %Y}.")
    if health["collection_closed"]:
        st.caption("Collection has ended (final collection 3 Dec 2026, 08:15 Tashkent).")

with right:
    st.subheader("Add a field report")
    st.caption("A low temperature you measured yourself. No names or contact details. "
               "Field reports are shown here but never used in the evaluation.")
    if "saved" in st.session_state:
        st.success(st.session_state.pop("saved"))
    with st.form("field_report", clear_on_submit=True):
        names = {loc: name for loc, name, *_ in config.LOCATIONS}
        location_id = st.selectbox("Point", list(names), format_func=names.get)
        today = now.astimezone(board.TZ).date()
        night = st.date_input("Night of", value=min(night_date, today), min_value=config.FIRST_NIGHT,
                              max_value=today, format="DD/MM/YYYY")
        low = st.number_input("Lowest temperature you measured (°C)", min_value=board.REPORT_LIMITS[0],
                              max_value=board.REPORT_LIMITS[1], value=5.0, step=0.1, format="%.1f")
        method = st.selectbox("How you measured it", board.METHODS)
        if st.form_submit_button("Save field report", type="primary"):
            try:
                with db.connect(DSN) as conn:
                    saved = board.add_field_report(conn, location_id, night, low, method, now)
                st.session_state["saved"] = (f"Field report saved: {saved['point']}, night of "
                                             f"{saved['night_date']:%d %b}, {saved['measured_low_c']:.1f} °C.")
                st.rerun()
            except ValueError as exc:
                st.error(f"Not saved: {exc}.")
            except psycopg.Error:
                st.error("Not saved: the database cannot be reached right now. Try again in a minute.")

st.subheader("Latest field reports")
if reports:
    st.dataframe(pd.DataFrame([{"Point": r["point"], "Night of": r["night_date"],
                                "Measured low °C": r["measured_low_c"], "Method": r["method"],
                                "Sent": local(r["reported_at"])} for r in reports]),
                 hide_index=True, width="stretch",
                 column_config={"Measured low °C": st.column_config.NumberColumn(format="%.1f")})
else:
    st.caption("No field reports yet. Use the form above to add the first one.")

st.markdown(f'<p class="nw-small">{board.CREDIT} Times are Tashkent time (UTC+5).</p>',
            unsafe_allow_html=True)
