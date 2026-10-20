-- ============================================================================
-- KPI views for the SVP dashboard.  Rebuilt from scratch on every pipeline run.
-- All data is SIMULATED.
-- ============================================================================

DROP VIEW IF EXISTS v_params;
DROP VIEW IF EXISTS v_order_status;
DROP VIEW IF EXISTS v_otd_by_buyer;
DROP VIEW IF EXISTS v_otd_monthly;
DROP VIEW IF EXISTS v_reject_by_machine;
DROP VIEW IF EXISTS v_reject_by_defect;
DROP VIEW IF EXISTS v_downtime_by_machine;
DROP VIEW IF EXISTS v_downtime_by_reason;
DROP VIEW IF EXISTS v_material_price_monthly;
DROP VIEW IF EXISTS v_revenue_monthly;
DROP VIEW IF EXISTS v_delay_reasons;
DROP VIEW IF EXISTS v_executive_summary;

-- Data extraction date. Change this one value when new data is loaded.
CREATE VIEW v_params AS SELECT '2026-09-30' AS report_date;

-- One row per order with its delivery outcome.
--   Delivered            shipment recorded
--   Ship date unverified shipment row was quarantined (impossible date), Logistics must confirm
--   Open                 not shipped yet
CREATE VIEW v_order_status AS
SELECT o.order_id, o.buyer_id, b.buyer_name, o.unit, o.order_date, o.delivery_deadline,
       o.total_value_usd, s.actual_ship_date,
       CASE WHEN s.order_id IS NOT NULL
            THEN CAST(julianday(s.actual_ship_date) - julianday(o.delivery_deadline) AS INTEGER)
       END AS delay_days,
       CASE
         WHEN s.order_id IS NOT NULL THEN 'Delivered'
         WHEN EXISTS (SELECT 1 FROM dq_issues d
                      WHERE d.issue_type = 'ship_date_before_order_date'
                        AND d.record_key = o.order_id) THEN 'Ship date unverified'
         ELSE 'Open'
       END AS status,
       CASE WHEN s.order_id IS NOT NULL
            THEN CASE WHEN s.actual_ship_date <= o.delivery_deadline THEN 1 ELSE 0 END
       END AS on_time,
       strftime('%Y-%m', o.delivery_deadline) AS deadline_month
FROM orders o
JOIN buyers b ON b.buyer_id = o.buyer_id
LEFT JOIN shipments s ON s.order_id = o.order_id;

-- On-time delivery by buyer (worst first)
CREATE VIEW v_otd_by_buyer AS
SELECT buyer_id, buyer_name,
       COUNT(*)                                             AS delivered_orders,
       SUM(on_time)                                         AS on_time_orders,
       ROUND(100.0 * SUM(on_time) / COUNT(*), 1)            AS otd_pct,
       ROUND(AVG(CASE WHEN delay_days > 0 THEN delay_days END), 1) AS avg_days_late_when_late,
       ROUND(SUM(total_value_usd), 0)                       AS delivered_value_usd
FROM v_order_status
WHERE status = 'Delivered'
GROUP BY buyer_id, buyer_name
ORDER BY otd_pct;

-- On-time delivery by deadline month and unit (trend)
CREATE VIEW v_otd_monthly AS
SELECT deadline_month, unit,
       COUNT(*)                                  AS delivered_orders,
       ROUND(100.0 * SUM(on_time) / COUNT(*), 1) AS otd_pct
FROM v_order_status
WHERE status = 'Delivered'
GROUP BY deadline_month, unit
ORDER BY deadline_month, unit;

-- Reject rate by machine
CREATE VIEW v_reject_by_machine AS
SELECT r.machine_id, m.unit,
       SUM(q.inspected_qty) AS inspected_qty,
       SUM(q.rejected_qty)  AS rejected_qty,
       ROUND(100.0 * SUM(q.rejected_qty) / SUM(q.inspected_qty), 2) AS reject_rate_pct
FROM quality_inspections q
JOIN production_runs r ON r.run_id = q.run_id
JOIN machines m        ON m.machine_id = r.machine_id
GROUP BY r.machine_id, m.unit
ORDER BY reject_rate_pct DESC;

-- Reject quantity by defect type and unit
CREATE VIEW v_reject_by_defect AS
SELECT m.unit, q.defect_type,
       SUM(q.rejected_qty) AS rejected_qty,
       ROUND(100.0 * SUM(q.rejected_qty)
             / SUM(SUM(q.rejected_qty)) OVER (PARTITION BY m.unit), 1) AS share_of_unit_rejects_pct
FROM quality_inspections q
JOIN production_runs r ON r.run_id = q.run_id
JOIN machines m        ON m.machine_id = r.machine_id
WHERE q.defect_type <> 'None'
GROUP BY m.unit, q.defect_type
ORDER BY m.unit, rejected_qty DESC;

-- Downtime hours by machine
CREATE VIEW v_downtime_by_machine AS
SELECT d.machine_id, m.unit,
       COUNT(*) AS events,
       ROUND(SUM((julianday(d.end_time) - julianday(d.start_time)) * 24), 0) AS downtime_hours,
       ROUND(AVG((julianday(d.end_time) - julianday(d.start_time)) * 24), 1) AS avg_hours_per_event,
       ROUND(100.0 * SUM(julianday(d.end_time) - julianday(d.start_time))
             / SUM(SUM(julianday(d.end_time) - julianday(d.start_time))) OVER (), 1) AS share_of_total_pct
FROM machine_downtime d
JOIN machines m ON m.machine_id = d.machine_id
GROUP BY d.machine_id, m.unit
ORDER BY downtime_hours DESC;

-- Downtime hours by cause
CREATE VIEW v_downtime_by_reason AS
SELECT reason, COUNT(*) AS events,
       ROUND(SUM((julianday(end_time) - julianday(start_time)) * 24), 0) AS downtime_hours
FROM machine_downtime
GROUP BY reason
ORDER BY downtime_hours DESC;

-- Raw material price trend: quantity-weighted USD/kg per month and month-on-month change
CREATE VIEW v_material_price_monthly AS
WITH monthly AS (
  SELECT material, strftime('%Y-%m', purchase_date) AS month,
         SUM(qty_kg) AS qty_kg,
         SUM(qty_kg * price_per_kg_usd) / SUM(qty_kg) AS avg_price
  FROM raw_material_purchases
  GROUP BY material, strftime('%Y-%m', purchase_date)
)
SELECT material, month, ROUND(qty_kg, 0) AS qty_kg, ROUND(avg_price, 2) AS avg_price_usd_kg,
       ROUND(100.0 * (avg_price / LAG(avg_price) OVER (PARTITION BY material ORDER BY month) - 1), 1)
         AS mom_change_pct
FROM monthly
ORDER BY material, month;

-- Order intake by order month, unit
CREATE VIEW v_revenue_monthly AS
SELECT strftime('%Y-%m', order_date) AS order_month, unit,
       COUNT(*) AS orders, ROUND(SUM(total_value_usd), 0) AS order_value_usd
FROM orders
GROUP BY strftime('%Y-%m', order_date), unit
ORDER BY order_month, unit;

-- Why late orders were late (from the cleaned manual Google Sheet)
CREATE VIEW v_delay_reasons AS
SELECT delay_reason_category, COUNT(*) AS late_orders,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_pct
FROM delay_log
GROUP BY delay_reason_category
ORDER BY late_orders DESC;

-- One-row headline numbers for the top of the dashboard
CREATE VIEW v_executive_summary AS
SELECT
  (SELECT report_date FROM v_params)                                              AS report_date,
  (SELECT COUNT(*) FROM orders)                                                   AS total_orders,
  (SELECT COUNT(*) FROM v_order_status WHERE status = 'Delivered')                AS delivered_orders,
  (SELECT ROUND(100.0 * SUM(on_time) / COUNT(*), 1) FROM v_order_status
     WHERE status = 'Delivered')                                                  AS otd_pct,
  (SELECT ROUND(AVG(delay_days), 1) FROM v_order_status
     WHERE status = 'Delivered' AND delay_days > 0)                               AS avg_days_late_when_late,
  (SELECT COUNT(*) FROM v_order_status WHERE status = 'Open')                     AS open_orders,
  (SELECT COUNT(*) FROM v_order_status s, v_params p
     WHERE s.status = 'Open' AND s.delivery_deadline < p.report_date)             AS open_orders_already_late,
  (SELECT COUNT(*) FROM v_order_status WHERE status = 'Ship date unverified')     AS ship_date_unverified,
  (SELECT ROUND(SUM(total_value_usd), 0) FROM v_order_status
     WHERE status = 'Delivered')                                                  AS delivered_value_usd,
  (SELECT ROUND(100.0 * SUM(rejected_qty) / SUM(inspected_qty), 2)
     FROM quality_inspections)                                                    AS overall_reject_rate_pct,
  (SELECT ROUND(SUM((julianday(end_time) - julianday(start_time)) * 24), 0)
     FROM machine_downtime)                                                       AS total_downtime_hours;
