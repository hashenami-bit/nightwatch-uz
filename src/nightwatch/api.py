"""Small API for the dashboard data (proposal v3 section 9; DECISIONS.md D018).

Run:  DATABASE_URL=... uv run uvicorn nightwatch.api:app --port 8000
Docs: http://localhost:8000/docs

GET  /health          data health (counts only)
GET  /night           the night shown on the dashboard: prediction, range, category per point
GET  /field-reports   the newest field reports
POST /field-reports   add a field report (the "add a new record" flow)
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from . import board, db

app = FastAPI(title="NightWatch UZ API", version="1.0",
              description=f"{board.NOTE} {board.WORDING} {board.CREDIT}")

MODEL_TTL_S = 3600
_model_lock = threading.Lock()
_model_cache: dict = {}


def get_conn():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not set")
    with db.connect(dsn) as conn:
        yield conn


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def provisional_model(conn):
    """The provisional model, trained again at most once an hour."""
    with _model_lock:
        if "model" not in _model_cache or time.monotonic() - _model_cache["at"] > MODEL_TTL_S:
            _model_cache.update(model=board.train_provisional(conn), at=time.monotonic())
        return _model_cache["model"]


class FieldReportIn(BaseModel):
    location_id: int = Field(ge=1, le=10)
    night_date: date
    measured_low_c: float = Field(ge=board.REPORT_LIMITS[0], le=board.REPORT_LIMITS[1])
    method: Literal["thermometer", "weather station", "other"]


@app.get("/health")
def health(conn=Depends(get_conn), now: datetime = Depends(now_utc)):
    return board.data_health(conn, now)


@app.get("/night")
def night(conn=Depends(get_conn), now: datetime = Depends(now_utc)):
    model = provisional_model(conn)
    night_date = board.current_night(now)
    return {
        "night_date": night_date,
        "label": board.night_label(night_date, now),
        "model": model.label if model else None,
        "development_nights": [str(d) for d in model.nights] if model else [],
        "note": board.NOTE,
        "wording": board.WORDING,
        "points": board.predict_night(conn, night_date, model, now),
    }


@app.get("/field-reports")
def list_reports(limit: int = Query(20, ge=1, le=200), conn=Depends(get_conn)):
    return board.field_reports(conn, limit)


@app.post("/field-reports", status_code=201)
def add_report(report: FieldReportIn, conn=Depends(get_conn), now: datetime = Depends(now_utc)):
    try:
        return board.add_field_report(conn, report.location_id, report.night_date,
                                      report.measured_low_c, report.method, now)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
