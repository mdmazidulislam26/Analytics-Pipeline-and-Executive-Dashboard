-- ============================================================================
-- Risk views: which open orders are most likely to ship late?
-- Rule-based and fully explainable.  The ML model (step 6) will be compared
-- against this baseline.
-- ============================================================================

DROP VIEW IF EXISTS v_open_order_risk;
DROP VIEW IF EXISTS v_unverified_ship_dates;
DROP VIEW IF EXISTS v_machine_recent_downtime;
DROP VIEW IF EXISTS v_buyer_history;

-- Buyer's historical late rate (delivered orders only)
CREATE VIEW v_buyer_history AS
SELECT buyer_id, COUNT(*) AS delivered_orders,
       1.0 * SUM(1 - on_time) / COUNT(*) AS late_rate
FROM v_order_status
WHERE status = 'Delivered'
GROUP BY buyer_id;

-- Downtime hours per machine in the 90 days before the report date
CREATE VIEW v_machine_recent_downtime AS
SELECT d.machine_id,
       SUM((julianday(d.end_time) - julianday(d.start_time)) * 24) AS recent_hours
FROM machine_downtime d, v_params p
WHERE d.start_time >= date(p.report_date, '-90 day')
GROUP BY d.machine_id;

-- Risk score per open order (0-100):
--   up to 40  schedule pressure   already past deadline = 40, due within 7 days = 20, within 14 = 10
--   up to 30  buyer history       30 x the buyer's historical late rate
--   15        its runs used a machine with recent downtime above 1.5x the fleet average
--   15        its reject rate is above 2.5%
-- Level: High >= 60, Medium >= 35, otherwise Low.
CREATE VIEW v_open_order_risk AS
WITH fleet AS (
  SELECT AVG(recent_hours) AS avg_hours FROM v_machine_recent_downtime
),
order_machine AS (
  SELECT r.order_id,
         MAX(CASE WHEN md.recent_hours > 1.5 * (SELECT avg_hours FROM fleet) THEN 1 ELSE 0 END)
           AS uses_problem_machine
  FROM production_runs r
  LEFT JOIN v_machine_recent_downtime md ON md.machine_id = r.machine_id
  GROUP BY r.order_id
),
order_quality AS (
  SELECT r.order_id, 1.0 * SUM(q.rejected_qty) / SUM(q.inspected_qty) AS reject_rate
  FROM quality_inspections q
  JOIN production_runs r ON r.run_id = q.run_id
  GROUP BY r.order_id
),
scored AS (
  SELECT s.order_id, s.buyer_name, s.unit, s.delivery_deadline, s.total_value_usd,
         CAST(julianday(s.delivery_deadline) - julianday(p.report_date) AS INTEGER) AS days_to_deadline,
         COALESCE(bh.late_rate, 0)               AS late_rate,
         COALESCE(oq.reject_rate, 0)             AS reject_rate,
         COALESCE(om.uses_problem_machine, 0)    AS uses_problem_machine
  FROM v_order_status s
  CROSS JOIN v_params p
  LEFT JOIN v_buyer_history bh ON bh.buyer_id = s.buyer_id
  LEFT JOIN order_machine om   ON om.order_id = s.order_id
  LEFT JOIN order_quality oq   ON oq.order_id = s.order_id
  WHERE s.status = 'Open'
)
SELECT order_id, buyer_name, unit, delivery_deadline, days_to_deadline,
       ROUND(100 * late_rate, 1)   AS buyer_late_pct,
       ROUND(100 * reject_rate, 2) AS reject_rate_pct,
       uses_problem_machine, ROUND(total_value_usd, 0) AS order_value_usd,
       CASE WHEN days_to_deadline < 0 THEN 40 WHEN days_to_deadline <= 7 THEN 20
            WHEN days_to_deadline <= 14 THEN 10 ELSE 0 END
       + CAST(ROUND(30 * late_rate) AS INTEGER)
       + CASE WHEN uses_problem_machine = 1 THEN 15 ELSE 0 END
       + CASE WHEN reject_rate > 0.025 THEN 15 ELSE 0 END           AS risk_score,
       CASE
         WHEN (CASE WHEN days_to_deadline < 0 THEN 40 WHEN days_to_deadline <= 7 THEN 20
                    WHEN days_to_deadline <= 14 THEN 10 ELSE 0 END
               + CAST(ROUND(30 * late_rate) AS INTEGER)
               + CASE WHEN uses_problem_machine = 1 THEN 15 ELSE 0 END
               + CASE WHEN reject_rate > 0.025 THEN 15 ELSE 0 END) >= 60 THEN 'High'
         WHEN (CASE WHEN days_to_deadline < 0 THEN 40 WHEN days_to_deadline <= 7 THEN 20
                    WHEN days_to_deadline <= 14 THEN 10 ELSE 0 END
               + CAST(ROUND(30 * late_rate) AS INTEGER)
               + CASE WHEN uses_problem_machine = 1 THEN 15 ELSE 0 END
               + CASE WHEN reject_rate > 0.025 THEN 15 ELSE 0 END) >= 35 THEN 'Medium'
         ELSE 'Low'
       END AS risk_level
FROM scored
ORDER BY risk_score DESC, delivery_deadline;

-- Orders whose shipment record had an impossible date: Logistics must confirm
CREATE VIEW v_unverified_ship_dates AS
SELECT order_id, buyer_name, unit, delivery_deadline, order_value_usd
FROM (SELECT order_id, buyer_name, unit, delivery_deadline, ROUND(total_value_usd, 0) AS order_value_usd
      FROM v_order_status WHERE status = 'Ship date unverified')
ORDER BY delivery_deadline;
