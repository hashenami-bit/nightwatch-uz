"""Value checks applied before anything is written to the database."""

# Same limits as the CHECK constraints in db/schema.sql.
RANGES = {
    "temperature_c": (-50, 60),
    "relative_humidity_pct": (0, 100),
    "dew_point_c": (-70, 40),
    "wind_speed_kmh": (0, 250),
    "cloud_cover_pct": (0, 100),
    "precipitation_mm": (0, 500),
    "pressure_hpa": (850, 1100),
}
REQUIRED = ("temperature_c",)
DEW_POINT_TOLERANCE_C = 0.5


def check_row(row: dict) -> list[str]:
    """Return a list of problems; an empty list means the row is valid."""
    problems = []
    for col in REQUIRED:
        if row.get(col) is None:
            problems.append(f"{col} missing")
    for col, (low, high) in RANGES.items():
        value = row.get(col)
        if value is None:
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            problems.append(f"{col} not a number: {value!r}")
        elif not low <= value <= high:
            problems.append(f"{col}={value} outside {low}..{high}")
    temp, dew = row.get("temperature_c"), row.get("dew_point_c")
    if isinstance(temp, (int, float)) and isinstance(dew, (int, float)):
        if dew > temp + DEW_POINT_TOLERANCE_C:
            problems.append(f"dew point {dew} above temperature {temp}")
    return problems
