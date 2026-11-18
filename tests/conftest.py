"""Shared test database. All data in it is synthetic.

Uses TEST_DATABASE_URL (default: local database nightwatch_test). The database is
dropped and recreated for every test, so never point this at real data.
"""
import os

import psycopg
import pytest

from nightwatch import db

TEST_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql:///nightwatch_test")


@pytest.fixture()
def conn():
    name = psycopg.conninfo.conninfo_to_dict(TEST_DSN).get("dbname", "")
    assert "test" in name, "refusing to reset a database whose name does not contain 'test'"
    admin = TEST_DSN.rsplit("/", 1)[0] + "/postgres"
    try:
        with psycopg.connect(admin, autocommit=True) as a:
            a.execute(f'DROP DATABASE IF EXISTS "{name}"')
            a.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    with db.connect(TEST_DSN) as c:
        db.apply_schema(c)
        yield c
