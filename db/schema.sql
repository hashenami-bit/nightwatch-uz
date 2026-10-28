-- NightWatch UZ database schema (PostgreSQL 15+)
-- Follows proposal v2 section 7 with the v3 amendments (section 7 of v3).
-- All timestamps are stored as timestamptz (UTC). Local time is Asia/Tashkent (UTC+5).

-- Ten fixed monitored points. A point is one latitude/longitude, not a whole region.
CREATE TABLE IF NOT EXISTS locations (
    location_id  smallint PRIMARY KEY,
    name         text NOT NULL UNIQUE,
    latitude     numeric(7,4) NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude    numeric(7,4) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    elevation_m  numeric(6,1),
    timezone     text NOT NULL DEFAULT 'Asia/Tashkent'
);

-- Audit trail: one row per collector run, including failed and delayed runs.
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id               bigserial PRIMARY KEY,
    trigger              text NOT NULL CHECK (trigger IN ('schedule', 'backup', 'manual', 'test')),
    scheduled_for        timestamptz,             -- the schedule slot this run belongs to
    request_sent_at      timestamptz NOT NULL,
    response_received_at timestamptz,             -- NULL when no valid response arrived
    finished_at          timestamptz,
    delay_seconds        integer,                 -- request_sent_at - scheduled_for
    request_url          text NOT NULL,
    http_status          smallint,
    attempts             smallint NOT NULL DEFAULT 1 CHECK (attempts >= 1),
    points_ok            smallint NOT NULL DEFAULT 0 CHECK (points_ok BETWEEN 0 AND 10),
    points_failed        smallint NOT NULL DEFAULT 0 CHECK (points_failed BETWEEN 0 AND 10),
    error_message        text,
    code_version         text NOT NULL,
    is_backfill          boolean NOT NULL DEFAULT false,
    CHECK (response_received_at IS NULL OR response_received_at >= request_sent_at)
);
CREATE INDEX IF NOT EXISTS ix_runs_received ON collection_runs (response_received_at);

-- Raw provider answer for every successful run (provenance).
CREATE TABLE IF NOT EXISTS raw_responses (
    run_id          bigint PRIMARY KEY REFERENCES collection_runs (run_id),
    payload_sha256  char(64) NOT NULL,
    payload         jsonb NOT NULL
);

-- Hourly model values. A value is "live" when it was received between its valid
-- time H and H + 75 minutes (v3 section 3). The earliest live capture wins.
CREATE TABLE IF NOT EXISTS observations (
    obs_id                bigserial PRIMARY KEY,
    location_id           smallint NOT NULL REFERENCES locations (location_id),
    run_id                bigint NOT NULL REFERENCES collection_runs (run_id),
    observed_at           timestamptz NOT NULL,   -- valid time of the hourly value
    response_received_at  timestamptz NOT NULL,
    is_live               boolean NOT NULL,
    is_backfill           boolean NOT NULL DEFAULT false,
    temperature_c         numeric(5,2) NOT NULL CHECK (temperature_c BETWEEN -50 AND 60),
    relative_humidity_pct numeric(5,2) CHECK (relative_humidity_pct BETWEEN 0 AND 100),
    dew_point_c           numeric(5,2) CHECK (dew_point_c BETWEEN -70 AND 40),
    wind_speed_kmh        numeric(6,2) CHECK (wind_speed_kmh BETWEEN 0 AND 250),
    cloud_cover_pct       numeric(5,2) CHECK (cloud_cover_pct BETWEEN 0 AND 100),
    precipitation_mm      numeric(6,2) CHECK (precipitation_mm BETWEEN 0 AND 500),
    pressure_hpa          numeric(6,1) CHECK (pressure_hpa BETWEEN 850 AND 1100),
    payload_sha256        char(64) NOT NULL,
    UNIQUE (location_id, observed_at),
    CHECK (NOT (is_live AND is_backfill)),
    CHECK (NOT is_live OR (response_received_at >= observed_at
                           AND response_received_at <= observed_at + interval '75 minutes'))
);

-- Current-conditions block of each response. The 18:00 feature snapshot is built
-- from the latest row received between 16:00 and 18:00 local (v3 section 3).
CREATE TABLE IF NOT EXISTS current_conditions (
    current_id            bigserial PRIMARY KEY,
    location_id           smallint NOT NULL REFERENCES locations (location_id),
    run_id                bigint NOT NULL REFERENCES collection_runs (run_id),
    valid_time            timestamptz NOT NULL,
    response_received_at  timestamptz NOT NULL,
    temperature_c         numeric(5,2) NOT NULL CHECK (temperature_c BETWEEN -50 AND 60),
    relative_humidity_pct numeric(5,2) CHECK (relative_humidity_pct BETWEEN 0 AND 100),
    dew_point_c           numeric(5,2) CHECK (dew_point_c BETWEEN -70 AND 40),
    wind_speed_kmh        numeric(6,2) CHECK (wind_speed_kmh BETWEEN 0 AND 250),
    cloud_cover_pct       numeric(5,2) CHECK (cloud_cover_pct BETWEEN 0 AND 100),
    precipitation_mm      numeric(6,2) CHECK (precipitation_mm BETWEEN 0 AND 500),
    pressure_hpa          numeric(6,1) CHECK (pressure_hpa BETWEEN 850 AND 1100),
    UNIQUE (location_id, run_id)
);
CREATE INDEX IF NOT EXISTS ix_current_received ON current_conditions (location_id, response_received_at);

-- Provider forecast as retrieved. retrieved_at is when MY collector received it.
-- provider_model_init_time is filled only when the provider states it; never guessed.
CREATE TABLE IF NOT EXISTS provider_forecasts (
    forecast_id              bigserial PRIMARY KEY,
    location_id              smallint NOT NULL REFERENCES locations (location_id),
    run_id                   bigint NOT NULL REFERENCES collection_runs (run_id),
    retrieved_at             timestamptz NOT NULL,
    provider_model_init_time timestamptz,
    init_time_source         text,
    target_time              timestamptz NOT NULL,
    forecast_temp_c          numeric(5,2) NOT NULL CHECK (forecast_temp_c BETWEEN -50 AND 60),
    UNIQUE (location_id, run_id, target_time),
    CHECK (target_time > retrieved_at),
    CHECK (provider_model_init_time IS NULL OR init_time_source IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_forecasts_target ON provider_forecasts (location_id, target_time);

-- One row per location-night, built later by the pipeline.
CREATE TABLE IF NOT EXISTS nights (
    night_id                bigserial PRIMARY KEY,
    location_id             smallint NOT NULL REFERENCES locations (location_id),
    night_date              date NOT NULL,          -- day D; the night runs 20:00 D to 07:00 D+1
    features_cutoff_utc     timestamptz NOT NULL,   -- 18:00 local on day D
    snapshot_run_id         bigint REFERENCES collection_runs (run_id),
    snapshot_received_at    timestamptz,
    -- daytime features: live values for day D received before 18:00 (v3 section 3)
    day_max_temp_c          numeric(5,2),           -- 07:00-16:00, at least 7 of 10 hours
    morning_min_temp_c      numeric(5,2),           -- 00:00-07:00, at least 6 of 8 hours
    afternoon_cloud_pct     numeric(5,2) CHECK (afternoon_cloud_pct BETWEEN 0 AND 100),  -- 12:00-16:00, at least 4 of 5
    target_min_c            numeric(5,2),
    n_live_hours            smallint NOT NULL CHECK (n_live_hours BETWEEN 0 AND 12),
    is_complete             boolean NOT NULL,
    calendar_night_complete boolean,
    split                   text CHECK (split IN ('train', 'test')),   -- no validation split (v3)
    UNIQUE (location_id, night_date),
    CHECK (NOT is_complete OR (n_live_hours = 12 AND target_min_c IS NOT NULL
                               AND snapshot_run_id IS NOT NULL
                               AND day_max_temp_c IS NOT NULL AND morning_min_temp_c IS NOT NULL
                               AND afternoon_cloud_pct IS NOT NULL))
);

-- Small user input: a low temperature a user measured. No personal data.
CREATE TABLE IF NOT EXISTS field_reports (
    report_id       bigserial PRIMARY KEY,
    location_id     smallint NOT NULL REFERENCES locations (location_id),
    reported_at     timestamptz NOT NULL DEFAULT now(),
    night_date      date NOT NULL,
    measured_low_c  numeric(5,2) NOT NULL CHECK (measured_low_c BETWEEN -50 AND 60),
    method          text
);

CREATE TABLE IF NOT EXISTS data_quality_log (
    issue_id     bigserial PRIMARY KEY,
    run_id       bigint REFERENCES collection_runs (run_id),
    location_id  smallint REFERENCES locations (location_id),
    rule_name    text NOT NULL,
    action       text NOT NULL CHECK (action IN ('rejected', 'flagged', 'corrected')),
    detail       text,
    logged_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_runs (
    model_run_id          bigserial PRIMARY KEY,
    created_at            timestamptz NOT NULL DEFAULT now(),
    git_commit            text NOT NULL,
    data_snapshot_sha256  char(64) NOT NULL,
    model_name            text NOT NULL,
    parameters            jsonb NOT NULL,
    metrics               jsonb
);

-- Provisional dashboard predictions are kept apart from the final evaluation (v3 section 8).
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id     bigserial PRIMARY KEY,
    night_id          bigint NOT NULL REFERENCES nights (night_id),
    model_name        text NOT NULL,
    protocol_version  text NOT NULL,
    purpose           text NOT NULL CHECK (purpose IN ('provisional_dashboard', 'final_evaluation')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    predicted_min_c   numeric(5,2) NOT NULL,
    UNIQUE (night_id, model_name, purpose, protocol_version)
);

-- The ten monitored points (fixed before collection; city-centre coordinates).
INSERT INTO locations (location_id, name, latitude, longitude) VALUES
    (1,  'Tashkent',  41.2995, 69.2401),
    (2,  'Samarkand', 39.6542, 66.9597),
    (3,  'Bukhara',   39.7747, 64.4286),
    (4,  'Fergana',   40.3864, 71.7864),
    (5,  'Namangan',  40.9983, 71.6726),
    (6,  'Andijan',   40.7821, 72.3442),
    (7,  'Urgench',   41.5500, 60.6333),
    (8,  'Termez',    37.2242, 67.2783),
    (9,  'Qarshi',    38.8606, 65.7891),
    (10, 'Nukus',     42.4600, 59.6000)
ON CONFLICT (location_id) DO NOTHING;
