-- Tex Fasteners Executive Intelligence System
-- Clean database schema (SQLite). All data in this project is SIMULATED.

PRAGMA foreign_keys = ON;

-- ---------- Master data ----------
CREATE TABLE IF NOT EXISTS buyers (
    buyer_id            TEXT PRIMARY KEY,
    buyer_name          TEXT NOT NULL UNIQUE,
    country             TEXT NOT NULL,
    payment_terms_days  INTEGER NOT NULL CHECK (payment_terms_days > 0)
);

CREATE TABLE IF NOT EXISTS machines (
    machine_id    TEXT PRIMARY KEY,
    unit          TEXT NOT NULL CHECK (unit IN ('Zipper', 'Metal Trims')),
    machine_type  TEXT NOT NULL,
    installed_year INTEGER NOT NULL
);

-- ---------- Sales ----------
CREATE TABLE IF NOT EXISTS orders (
    order_id           TEXT PRIMARY KEY,
    buyer_id           TEXT NOT NULL REFERENCES buyers (buyer_id),
    unit               TEXT NOT NULL CHECK (unit IN ('Zipper', 'Metal Trims')),
    order_date         DATE NOT NULL,
    delivery_deadline  DATE NOT NULL,
    total_value_usd    REAL NOT NULL CHECK (total_value_usd >= 0),
    CHECK (delivery_deadline >= order_date)
);

CREATE TABLE IF NOT EXISTS order_items (
    item_id         TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL REFERENCES orders (order_id),
    product_type    TEXT NOT NULL,
    size            TEXT,
    color           TEXT,
    finish          TEXT,
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_usd  REAL NOT NULL CHECK (unit_price_usd >= 0)
);

-- ---------- Production and quality ----------
CREATE TABLE IF NOT EXISTS production_runs (
    run_id        TEXT PRIMARY KEY,
    order_id      TEXT NOT NULL REFERENCES orders (order_id),
    run_date      DATE NOT NULL,
    machine_id    TEXT NOT NULL REFERENCES machines (machine_id),
    shift         TEXT NOT NULL CHECK (shift IN ('A', 'B', 'C')),
    produced_qty  INTEGER NOT NULL CHECK (produced_qty >= 0)
);

CREATE TABLE IF NOT EXISTS quality_inspections (
    inspection_id  TEXT PRIMARY KEY,
    run_id         TEXT NOT NULL REFERENCES production_runs (run_id),
    inspected_qty  INTEGER NOT NULL CHECK (inspected_qty > 0),
    rejected_qty   INTEGER NOT NULL CHECK (rejected_qty >= 0),
    defect_type    TEXT NOT NULL,
    CHECK (rejected_qty <= inspected_qty)
);

CREATE TABLE IF NOT EXISTS machine_downtime (
    downtime_id  TEXT PRIMARY KEY,
    machine_id   TEXT NOT NULL REFERENCES machines (machine_id),
    start_time   DATETIME NOT NULL,
    end_time     DATETIME NOT NULL,
    reason       TEXT NOT NULL,
    CHECK (end_time > start_time)
);

-- ---------- Purchasing ----------
CREATE TABLE IF NOT EXISTS raw_material_purchases (
    purchase_id        TEXT PRIMARY KEY,
    material           TEXT NOT NULL,
    supplier           TEXT NOT NULL,
    purchase_date      DATE NOT NULL,
    qty_kg             REAL NOT NULL CHECK (qty_kg > 0),
    price_per_kg_usd   REAL NOT NULL CHECK (price_per_kg_usd > 0)
);

-- ---------- Logistics ----------
CREATE TABLE IF NOT EXISTS shipments (
    shipment_id       TEXT PRIMARY KEY,
    order_id          TEXT NOT NULL UNIQUE REFERENCES orders (order_id),
    actual_ship_date  DATE NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('Shipped', 'Partial Shipment'))
);

-- Cleaned version of the team's manual Google Sheet
CREATE TABLE IF NOT EXISTS delay_log (
    order_id               TEXT PRIMARY KEY REFERENCES orders (order_id),
    revised_eta            DATE,
    delay_reason_category  TEXT NOT NULL,
    updated_by             TEXT
);

-- ---------- Data quality audit trail (feeds the Data Quality Report) ----------
CREATE TABLE IF NOT EXISTS dq_issues (
    issue_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file   TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    record_key    TEXT,
    issue_type    TEXT NOT NULL,
    action_taken  TEXT NOT NULL,
    detected_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------- Indexes ----------
CREATE INDEX IF NOT EXISTS idx_orders_buyer      ON orders (buyer_id);
CREATE INDEX IF NOT EXISTS idx_orders_order_date ON orders (order_date);
CREATE INDEX IF NOT EXISTS idx_orders_deadline   ON orders (delivery_deadline);
CREATE INDEX IF NOT EXISTS idx_items_order       ON order_items (order_id);
CREATE INDEX IF NOT EXISTS idx_runs_order        ON production_runs (order_id);
CREATE INDEX IF NOT EXISTS idx_runs_machine_date ON production_runs (machine_id, run_date);
CREATE INDEX IF NOT EXISTS idx_quality_run       ON quality_inspections (run_id);
CREATE INDEX IF NOT EXISTS idx_downtime_machine  ON machine_downtime (machine_id, start_time);
CREATE INDEX IF NOT EXISTS idx_materials_date    ON raw_material_purchases (material, purchase_date);
