"""Create or refresh the SQL views:   python -m etl.apply_views"""
import sqlite3

from .config import DB_PATH, ROOT

SQL_FILES = ["views_kpi.sql", "views_risk.sql"]


def apply(db_path=DB_PATH):
    con = sqlite3.connect(db_path)
    try:
        for name in SQL_FILES:
            con.executescript((ROOT / "sql" / name).read_text(encoding="utf-8"))
        con.commit()
        return [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name")]
    finally:
        con.close()


if __name__ == "__main__":
    print("Views created:", ", ".join(apply()))
