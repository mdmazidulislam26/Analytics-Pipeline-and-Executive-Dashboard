"""Tests for the SQL KPI and risk views.  Run:  python -m pytest -q"""
import sqlite3

import pandas as pd
import pytest

from etl.config import DB_PATH
from etl.run_pipeline import run

VIEWS = ["v_executive_summary", "v_order_status", "v_otd_by_buyer", "v_otd_monthly",
         "v_reject_by_machine", "v_reject_by_defect", "v_downtime_by_machine",
         "v_downtime_by_reason", "v_material_price_monthly", "v_revenue_monthly",
         "v_delay_reasons", "v_open_order_risk", "v_unverified_ship_dates"]


@pytest.fixture(scope="module")
def con():
    run(verbose=False)
    connection = sqlite3.connect(DB_PATH)
    yield connection
    connection.close()


@pytest.mark.parametrize("view", VIEWS)
def test_view_is_queryable_and_not_empty(con, view):
    assert con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0] > 0


def test_otd_matches_independent_pandas_calculation(con):
    df = pd.read_sql("SELECT o.delivery_deadline AS dl, s.actual_ship_date AS sd "
                     "FROM orders o JOIN shipments s ON s.order_id = o.order_id", con)
    expected = round(100 * (df["sd"] <= df["dl"]).mean(), 1)
    got = con.execute("SELECT otd_pct FROM v_executive_summary").fetchone()[0]
    assert got == expected


def test_every_order_has_exactly_one_status(con):
    total = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    rows = con.execute("SELECT COUNT(*), COUNT(DISTINCT order_id) FROM v_order_status").fetchone()
    assert rows == (total, total)
    parts = con.execute("SELECT delivered_orders + open_orders + ship_date_unverified, total_orders "
                        "FROM v_executive_summary").fetchone()
    assert parts[0] == parts[1]


def test_unverified_ship_dates_match_quarantined_shipments(con):
    n_view = con.execute("SELECT COUNT(*) FROM v_unverified_ship_dates").fetchone()[0]
    n_dq = con.execute("SELECT COUNT(*) FROM dq_issues "
                       "WHERE issue_type = 'ship_date_before_order_date'").fetchone()[0]
    assert n_view == n_dq


def test_risk_scores_are_bounded_and_levels_consistent(con):
    rows = con.execute("SELECT risk_score, risk_level FROM v_open_order_risk").fetchall()
    assert rows
    for score, level in rows:
        assert 0 <= score <= 100
        assert level == ("High" if score >= 60 else "Medium" if score >= 35 else "Low")


def test_problem_machines_surface_in_the_kpis(con):
    worst_downtime = con.execute("SELECT machine_id FROM v_downtime_by_machine LIMIT 2").fetchall()
    worst_rejects = con.execute("SELECT machine_id FROM v_reject_by_machine LIMIT 2").fetchall()
    assert {r[0] for r in worst_downtime} == {"ZM-04", "MT-02"}
    assert {r[0] for r in worst_rejects} == {"ZM-04", "MT-02"}
