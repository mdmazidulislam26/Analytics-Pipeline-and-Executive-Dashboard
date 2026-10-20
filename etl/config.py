"""Paths and business rules for the ETL pipeline."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "tex_fasteners.db"
SCHEMA_PATH = ROOT / "schema.sql"
QUARANTINE_DIR = ROOT / "data" / "quarantine"
REPORT_PATH = ROOT / "reports" / "data_quality_report.html"
KEY_PATH = ROOT / "data" / "_answer_key" / "injected_errors.csv"

# logical table name -> raw source file
SOURCES = {
    "buyers": "buyers.csv",
    "machines": "machines.csv",
    "orders": "orders.csv",
    "order_items": "order_items.xlsx",
    "production": "production.csv",
    "quality": "quality.csv",
    "downtime": "machine_downtime.csv",
    "materials": "raw_materials.xlsx",
    "shipments": "shipments.csv",
    "manual_updates": "manual_delay_updates.csv",
}

# which team owns each source (used in the Data Quality Report)
OWNERS = {
    "buyers.csv": "Commercial",
    "machines.csv": "Maintenance",
    "orders.csv": "Sales / Merchandising",
    "order_items.xlsx": "Merchandising",
    "production.csv": "Production",
    "quality.csv": "Quality Control",
    "machine_downtime.csv": "Maintenance",
    "raw_materials.xlsx": "Purchasing",
    "shipments.csv": "Logistics",
    "manual_delay_updates.csv": "Planning",
}

VALID_SHIFTS = {"A", "B", "C"}
VALID_SHIP_STATUS = {"Shipped", "Partial Shipment"}

# Cleaning tolerances
RECON_TOLERANCE_USD = 0.05        # item lines must add up to the order total
PRICE_PLAUSIBLE_RANGE = (0.005, 1.0)   # USD per piece, for recovered unit prices
QTY_VERIFY_BAND = (0.8, 1.2)      # a sign-corrected run qty must be within this band of expected
PRICE_OUTLIER_RATIO = 5.0         # raw material price vs material median
MAX_DOWNTIME_HOURS = 24           # swapped start/end only accepted if the gap is below this

# Free-text delay reasons (from the team's Google Sheet) -> standard categories
REASON_MAP = {
    "machine breakdown": "Machine breakdown",
    "machine broke down": "Machine breakdown",
    "raw material delay": "Raw material delay",
    "rm late": "Raw material delay",
    "buyer changed spec": "Buyer spec change",
    "spec change": "Buyer spec change",
    "power cut": "Power outage",
    "qc rework": "Quality rework",
    "rework after qc": "Quality rework",
}
