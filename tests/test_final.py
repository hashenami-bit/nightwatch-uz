"""Tests for the final-evaluation script (D019). All data here is synthetic (made up for the tests)."""
import json
import shutil
from datetime import date, datetime, timedelta, timezone

import subprocess

import pytest

from nightwatch import features, final
from tests.fixtures.synthetic import store_synthetic_nights, synthetic_nights

LOCAL = timezone(timedelta(hours=5))
AFTER_CUTOFF = datetime(2026, 12, 3, 9, 0, tzinfo=LOCAL)
BEFORE_CUTOFF = datetime(2026, 12, 3, 8, 0, tzinfo=LOCAL)
# like the real collection: 12 Nov to 2 Dec without 20 and 21 Nov = 19 nights -> 14 training, 5 test
DATES = [date(2026, 11, 12) + timedelta(days=i) for i in range(21)]
DATES = [d for d in DATES if d not in (date(2026, 11, 20), date(2026, 11, 21))]
TRAINING, TEST = DATES[:14], DATES[14:]


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """Frozen file and protocol copy in a temporary folder; git answers as if HEAD is the tag."""
    protocol = tmp_path / "evaluation_protocol.md"
    shutil.copy(final.PROTOCOL_FILE, protocol)
    monkeypatch.setattr(final, "FROZEN_FILE", tmp_path / "frozen_config.json")
    monkeypatch.setattr(final, "PROTOCOL_FILE", protocol)
    answers = {("rev-parse", "HEAD"): "a" * 40, ("rev-list", "-n", "1", final.TAG): "a" * 40,
               ("status", "--porcelain", "--untracked-files=no"): "", "missing": False, "tagged_config": None}

    def fake_git(*args):
        if args[0] == "ls-files":
            if answers["missing"]:
                raise subprocess.CalledProcessError(1, "git")
            return ""
        if args[0] == "show":                       # the frozen file as committed with the tag
            return answers["tagged_config"] or final.FROZEN_FILE.read_text()
        return answers[args]
    monkeypatch.setattr(final, "git", fake_git)
    return tmp_path, answers


@pytest.fixture()
def nineteen(conn):
    store_synthetic_nights(conn, synthetic_nights(DATES, seed=5))
    return conn


def frozen(conn, paths):
    cfg = final.select(conn, AFTER_CUTOFF, build=False)
    final.freeze(conn, cfg)
    return json.loads(final.FROZEN_FILE.read_text())


# ---------- step 1 and 2 ----------

def test_training_table_is_locked_before_the_cut_off(nineteen):
    with pytest.raises(PermissionError):
        features.load(nineteen, "final_training", BEFORE_CUTOFF)


def test_training_table_stops_at_the_last_training_night(nineteen):
    table, _ = features.load(nineteen, "final_training", AFTER_CUTOFF)
    assert sorted(table["night_date"].unique()) == TRAINING


def test_select_waits_until_the_last_night_can_be_built(nineteen, paths):
    """08:15 closes the last window, but the builder builds the night only from 08:30."""
    with pytest.raises(PermissionError, match="08:30"):
        final.select(nineteen, datetime(2026, 12, 3, 8, 20, tzinfo=LOCAL), build=False)
    assert not final.FROZEN_FILE.exists()


def test_select_alone_writes_nothing(nineteen, paths):
    final.select(nineteen, AFTER_CUTOFF, build=False)
    assert nineteen.execute("SELECT count(*) FROM nights WHERE split IS NOT NULL").fetchone()[0] == 0
    assert not final.FROZEN_FILE.exists()


def test_select_never_reads_test_outcomes(nineteen, paths):
    """Wild test-night outcomes must not change the choice at all (C1 step 2)."""
    first = final.select(nineteen, AFTER_CUTOFF, build=False)
    nineteen.execute("UPDATE nights SET target_min_c = -45 WHERE night_date = ANY(%s)", (TEST,))
    nineteen.commit()
    second = final.select(nineteen, AFTER_CUTOFF, build=False)
    assert first["cv"] == second["cv"] and first["selected"] == second["selected"]
    assert first["training_table_sha256"] == second["training_table_sha256"]
    assert (first["n_nights"], first["k_test"]) == (19, 5)
    assert first["training_nights"] == TRAINING and first["test_nights"] == TEST


def test_select_writes_the_split_and_section_f(nineteen, paths):
    cfg = frozen(nineteen, paths)
    split = dict(nineteen.execute("SELECT DISTINCT night_date, split FROM nights"))
    assert all(split[d] == "train" for d in TRAINING) and all(split[d] == "test" for d in TEST)
    text = final.PROTOCOL_FILE.read_text()
    assert "| N complete calendar nights, k test nights | N = 19, k = 5 |" in text
    assert f"| Selected model and settings | {cfg['selected']['label']} |" in text
    assert "Status: FROZEN on 03 Dec 2026" in text


def test_select_runs_once_only(nineteen, paths):
    frozen(nineteen, paths)
    with pytest.raises(RuntimeError, match="once only"):
        final.select(nineteen, AFTER_CUTOFF, build=False)


def test_a_different_split_is_refused(nineteen):
    final.set_split(nineteen, TRAINING, TEST)
    with pytest.raises(RuntimeError, match="different split"):
        final.set_split(nineteen, TRAINING[:-1], [TRAINING[-1]] + TEST)


# ---------- step 3 and 4 ----------

def test_evaluate_stores_predictions_once_and_writes_results(nineteen, paths):
    tmp, _ = paths
    cfg = frozen(nineteen, paths)
    res = final.evaluate(nineteen, AFTER_CUTOFF, tmp / "out")

    count, purposes, models = nineteen.execute(
        """SELECT count(*), array_agg(DISTINCT purpose), count(DISTINCT model_name)
             FROM predictions""").fetchone()
    assert count == 4 * 5 * 10 and purposes == ["final_evaluation"] and models == 4
    assert nineteen.execute("SELECT count(*) FROM model_runs").fetchone()[0] == 4
    assert res["n_test_nights"] == 5 and res["selected"]["name"] == cfg["selected"]["name"]
    assert list(res["q1"]["d"].index) == TEST
    assert res["q1"]["dm"]["p_value"] is not None            # 5 nights, 4 neighbour pairs
    assert (tmp / "out" / "report.md").exists() and (tmp / "out" / "results.json").exists()
    assert len(list((tmp / "out" / "figures").glob("*.png"))) == 8
    report = (tmp / "out" / "report.md").read_text()
    assert "not issued on the original evenings" in report and "exploratory" in report

    with pytest.raises(RuntimeError, match="once only"):
        final.evaluate(nineteen, AFTER_CUTOFF, tmp / "again")


@pytest.mark.parametrize("change, message", [
    ({("rev-list", "-n", "1", final.TAG): "b" * 40}, "is not the protocol-frozen commit"),
    ({("status", "--porcelain", "--untracked-files=no"): " M src/nightwatch/models.py"}, "differ from"),
    ({"missing": True}, "not in the tagged commit"),
    ({"tagged_config": "{}"}, "differs from the tagged commit"),
])
def test_evaluate_needs_the_tagged_clean_commit(nineteen, paths, change, message):
    _, answers = paths
    frozen(nineteen, paths)
    answers.update(change)
    with pytest.raises(RuntimeError, match=message):
        final.evaluate(nineteen, AFTER_CUTOFF, paths[0] / "out")
    assert nineteen.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0


def test_evaluate_refuses_changed_training_data(nineteen, paths):
    frozen(nineteen, paths)
    nineteen.execute("UPDATE nights SET target_min_c = target_min_c + 1 WHERE night_date = %s", (TRAINING[3],))
    nineteen.commit()
    with pytest.raises(RuntimeError, match="training data changed"):
        final.evaluate(nineteen, AFTER_CUTOFF, paths[0] / "out")


def test_evaluate_refuses_a_changed_split(nineteen, paths):
    frozen(nineteen, paths)
    nineteen.execute("UPDATE nights SET split = 'train' WHERE night_date = %s", (TEST[0],))
    nineteen.commit()
    with pytest.raises(RuntimeError, match="does not match"):
        final.evaluate(nineteen, AFTER_CUTOFF, paths[0] / "out")


def test_evaluate_refuses_a_night_outside_the_frozen_split(nineteen, paths):
    """A night that became complete after the freeze must stop the evaluation, not be ignored."""
    frozen(nineteen, paths)
    store_synthetic_nights(nineteen, synthetic_nights([date(2026, 11, 20)], seed=9))
    with pytest.raises(RuntimeError, match="not the frozen training"):
        final.evaluate(nineteen, AFTER_CUTOFF, paths[0] / "out")


def test_a_crash_after_storing_can_be_recovered_with_report(nineteen, paths, monkeypatch):
    tmp, _ = paths
    frozen(nineteen, paths)
    real_publish = final.publish
    monkeypatch.setattr(final, "publish", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("lost")))
    with pytest.raises(ConnectionError):
        final.evaluate(nineteen, AFTER_CUTOFF, tmp / "out")
    stored = nineteen.execute("SELECT count(*) FROM predictions").fetchone()[0]
    assert stored == 200                                   # the predictions were kept
    monkeypatch.setattr(final, "publish", real_publish)
    res = final.report(nineteen, AFTER_CUTOFF, tmp / "out")
    assert (tmp / "out" / "report.md").exists() and res["n_test_nights"] == 5
    assert nineteen.execute("SELECT count(*) FROM predictions").fetchone()[0] == stored


def test_report_numbers_equal_the_stored_values(nineteen, paths):
    tmp, _ = paths
    cfg = frozen(nineteen, paths)
    res = final.evaluate(nineteen, AFTER_CUTOFF, tmp / "out")
    sel = cfg["selected"]["label"]
    mae = nineteen.execute(
        """SELECT avg(abs(p.predicted_min_c - n.target_min_c)) FROM predictions p JOIN nights n USING (night_id)
            WHERE p.model_name = %s""", (sel,)).fetchone()[0]
    assert abs(float(mae) - res["metrics"][cfg["selected"]["name"]]["mae"]) < 1e-9


def test_report_before_evaluate_is_refused(nineteen, paths):
    frozen(nineteen, paths)
    with pytest.raises(RuntimeError, match="run evaluate first"):
        final.report(nineteen, AFTER_CUTOFF, paths[0] / "out")


def test_the_command_refuses_a_test_database(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql:///nightwatch_test")
    assert final.main(["select"]) == 1
    assert "test database" in capsys.readouterr().err


# ---------- practice ----------

def test_practice_uses_development_nights_and_writes_nothing(nineteen, tmp_path):
    res = final.practice(nineteen, BEFORE_CUTOFF, tmp_path / "practice")
    assert res["n_test_nights"] == final.PRACTICE_TEST_NIGHTS
    assert set(res["q1"]["d"].index) == set(DATES[9:13])          # nights 10-13 of the first 13
    assert nineteen.execute("SELECT count(*) FROM predictions").fetchone()[0] == 0
    assert nineteen.execute("SELECT count(*) FROM nights WHERE split IS NOT NULL").fetchone()[0] == 0
    assert "PRACTICE RUN" in (tmp_path / "practice" / "report.md").read_text()


def test_practice_never_reads_later_nights(nineteen, tmp_path):
    first = final.practice(nineteen, BEFORE_CUTOFF, tmp_path / "a")
    nineteen.execute("UPDATE nights SET target_min_c = -45 WHERE night_date = ANY(%s)", (DATES[13:],))
    nineteen.commit()
    second = final.practice(nineteen, BEFORE_CUTOFF, tmp_path / "b")
    assert first["metrics"] == second["metrics"]
