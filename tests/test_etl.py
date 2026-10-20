"""
ETL tests.  Run from the project root:   python -m pytest -q

The generator writes data/_answer_key/injected_errors.csv, a list of every
error it deliberately planted in the raw files. These tests prove the pipeline
finds all of them, loses no rows silently, and leaves a clean, consistent database.
"""
import sqlite3

import pandas as pd
import pytest

from etl.config import DB_PATH, KEY_PATH
from etl.run_pipeline import run


@pytest.fixture(scope="module")
def result():
    return run(verbose=False)


@pytest.fixture(scope="module")
def answer_key():
    return pd.read_csv(KEY_PATH, dtype=str)


def test_every_injected_error_is_detected(result, answer_key):
    dq = result["dq"]
    detected = set(zip(dq["source_file"], dq["issue_type"], dq["record_key"]))
    individual = answer_key[answer_key["record_key"].str.match(r"^[A-Z]+-\d+$")]
    missed = [(r.file, r.issue_type, r.record_key) for r in individual.itertuples()
              if (r.file, r.issue_type, r.record_key) not in detected]
    assert not missed, f"{len(missed)} injected errors were not detected, e.g. {missed[:5]}"
    assert len(individual) == len(answer_key) - 1  # all but the one aggregate unit-label row


def test_messy_unit_labels_all_standardized(result):
    dq = result["dq"]
    n = ((dq["source_file"] == "orders.csv") & (dq["issue_type"] == "inconsistent_unit_label")).sum()
    assert n == 60


def test_no_rows_silently_lost(result):
    dq = result["dq"]
    for table, c in result["counts"].items():
        removed = c["raw"] - c["clean"] - c["quarantined"]
        dup_logged = ((dq["table_name"] == table) &
                      dq["issue_type"].isin(["duplicate_row", "duplicate_key"])).sum()
        assert removed == dup_logged, f"{table}: {removed} rows unaccounted vs {dup_logged} duplicates logged"


def test_database_integrity(result):
    con = sqlite3.connect(DB_PATH)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.execute("PRAGMA foreign_keys = ON")
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()


def test_order_lines_add_up_to_order_totals(result):
    con = sqlite3.connect(DB_PATH)
    rows = con.execute(
        "SELECT o.order_id, o.total_value_usd, SUM(i.quantity * i.unit_price_usd) "
        "FROM orders o JOIN order_items i USING (order_id) GROUP BY o.order_id").fetchall()
    con.close()
    bad = [r for r in rows if abs(r[1] - r[2]) > 0.05]
    assert not bad, f"{len(bad)} orders do not reconcile, e.g. {bad[:3]}"


def test_every_clean_order_has_items_and_no_silent_gaps(result):
    con = sqlite3.connect(DB_PATH)
    no_items = con.execute("SELECT COUNT(*) FROM orders WHERE order_id NOT IN "
                           "(SELECT order_id FROM order_items)").fetchone()[0]
    no_runs = {r[0] for r in con.execute("SELECT order_id FROM orders WHERE order_id NOT IN "
                                         "(SELECT order_id FROM production_runs)")}
    con.close()
    assert no_items == 0
    dq = result["dq"]
    flagged = set(dq.loc[dq["issue_type"] == "order_without_production_runs", "record_key"])
    assert no_runs == flagged, "orders without production runs must all be flagged, none hidden"


def test_clean_values_are_standardized(result):
    con = sqlite3.connect(DB_PATH)
    units = {r[0] for r in con.execute("SELECT DISTINCT unit FROM orders")}
    cats = {r[0] for r in con.execute("SELECT DISTINCT delay_reason_category FROM delay_log")}
    con.close()
    assert units == {"Zipper", "Metal Trims"}
    assert cats <= {"Machine breakdown", "Raw material delay", "Buyer spec change",
                    "Power outage", "Quality rework", "Other"}


def test_pipeline_is_repeatable(result):
    again = run(verbose=False)
    assert again["counts"] == result["counts"]
    assert len(again["dq"]) == len(result["dq"])
