"""Load: rebuild the SQLite database from schema.sql and insert the clean tables."""
import sqlite3

import pandas as pd

from .config import DB_PATH, SCHEMA_PATH

# (DB table, key in the `clean` dict) in foreign-key dependency order
LOAD_ORDER = [
    ("buyers", "buyers"),
    ("machines", "machines"),
    ("orders", "orders"),
    ("order_items", "order_items"),
    ("production_runs", "production"),
    ("quality_inspections", "quality"),
    ("machine_downtime", "downtime"),
    ("raw_material_purchases", "materials"),
    ("shipments", "shipments"),
    ("delay_log", "manual_updates"),
]


def _rows(df: pd.DataFrame):
    clean = df.astype(object).where(df.notna(), None)
    return [tuple(row) for row in clean.itertuples(index=False, name=None)]


def load(clean: dict, dq_frame: pd.DataFrame, db_path=DB_PATH, schema_path=SCHEMA_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(schema_path.read_text(encoding="utf-8"))
        con.execute("PRAGMA foreign_keys = ON")
        for table, key in LOAD_ORDER:
            df = clean[key]
            cols = ", ".join(df.columns)
            marks = ", ".join("?" for _ in df.columns)
            con.executemany(f"INSERT INTO {table} ({cols}) VALUES ({marks})", _rows(df))
        if len(dq_frame):
            cols = ["source_file", "table_name", "record_key", "issue_type", "action_taken"]
            con.executemany(
                "INSERT INTO dq_issues (source_file, table_name, record_key, issue_type, action_taken) "
                "VALUES (?, ?, ?, ?, ?)", _rows(dq_frame[cols]))
        con.commit()
    finally:
        con.close()
    return db_path
