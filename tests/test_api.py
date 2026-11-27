"""Tests for the API and a smoke test of the Streamlit page (D018). Synthetic data only."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nightwatch import api
from tests.conftest import TEST_DSN
from tests.test_board import DAY, store_dev_nights
from tests.test_nights import fixed_code_version, seed_day  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 12, 21, 0, tzinfo=timezone(timedelta(hours=5)))   # 21:00 Tashkent, 12 Nov


@pytest.fixture()
def client(conn):
    api.app.dependency_overrides[api.get_conn] = lambda: conn
    api.app.dependency_overrides[api.now_utc] = lambda: NOW
    api._model_cache.clear()
    yield TestClient(api.app)
    api.app.dependency_overrides.clear()
    api._model_cache.clear()


def test_new_field_report_appears_in_the_list(client):
    assert client.get("/field-reports").json() == []
    body = {"location_id": 2, "night_date": "2026-11-12", "measured_low_c": 1.5, "method": "thermometer"}
    response = client.post("/field-reports", json=body)
    assert response.status_code == 201 and response.json()["point"] == "Samarkand"
    listed = client.get("/field-reports").json()
    assert len(listed) == 1 and listed[0]["measured_low_c"] == 1.5


@pytest.mark.parametrize("change", [{"location_id": 11}, {"measured_low_c": 80}, {"method": "guess"},
                                    {"night_date": "2026-12-25"}])
def test_bad_field_report_is_refused(client, change):
    body = {"location_id": 2, "night_date": "2026-11-12", "measured_low_c": 1.5, "method": "thermometer"}
    assert client.post("/field-reports", json=body | change).status_code == 422
    assert client.get("/field-reports").json() == []


def test_night_and_health_endpoints(client, conn):
    store_dev_nights(conn)
    seed_day(conn)
    night = client.get("/night").json()
    assert night["night_date"] == str(DAY) and night["label"].startswith("Tonight")
    assert len(night["points"]) == 10 and all(p["category"] for p in night["points"])
    assert "not a frost warning" in night["note"]
    health = client.get("/health").json()
    assert health["needed_nights"] == 18


def test_dashboard_page_runs(conn, monkeypatch):
    """The Streamlit page renders on an empty test database without an error."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("DATABASE_URL", TEST_DSN)
    page = Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    at = AppTest.from_file(str(page), default_timeout=60).run()
    assert not at.exception
    assert any("not a frost warning" in m.value for m in at.markdown)
