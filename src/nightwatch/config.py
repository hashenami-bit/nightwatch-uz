"""Fixed settings for the NightWatch UZ collector (proposal v2 + v3)."""
from datetime import timedelta

API_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo variable name -> our column name.
VARIABLES = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "relative_humidity_pct",
    "dew_point_2m": "dew_point_c",
    "wind_speed_10m": "wind_speed_kmh",
    "cloud_cover": "cloud_cover_pct",
    "precipitation": "precipitation_mm",
    "pressure_msl": "pressure_hpa",
}

PAST_HOURS = 4          # hour H from a run at H+1:07, and the 3-hour trend before the snapshot
FORECAST_HOURS = 24     # provider forecast saved for the next 24 hours
LIVE_WINDOW = timedelta(minutes=75)   # live value for hour H: received in [H, H + 75 min]

# Schedule slots in UTC (v3 section 6). Tashkent = UTC+5, so 12:10 UTC = 17:10 local.
HOURLY_MINUTES = (7, 37)
EXTRA_SLOTS_UTC = ((12, 10), (12, 25), (12, 40))

REQUEST_TIMEOUT_S = 30
MAX_ATTEMPTS = 2
RETRY_WAIT_S = 10

# Coordinates must match the seed rows in db/schema.sql.
LOCATIONS = [
    (1, "Tashkent", 41.2995, 69.2401),
    (2, "Samarkand", 39.6542, 66.9597),
    (3, "Bukhara", 39.7747, 64.4286),
    (4, "Fergana", 40.3864, 71.7864),
    (5, "Namangan", 40.9983, 71.6726),
    (6, "Andijan", 40.7821, 72.3442),
    (7, "Urgench", 41.5500, 60.6333),
    (8, "Termez", 37.2242, 67.2783),
    (9, "Qarshi", 38.8606, 65.7891),
    (10, "Nukus", 42.4600, 59.6000),
]
