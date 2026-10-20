# Tex Fasteners Executive Intelligence System

Portfolio project: a data pipeline, database and reporting layer that gives a
Senior Vice President one reliable view of delivery, quality, machine and
material performance for a zipper and metal trims manufacturer.

**All data is simulated.** Buyer names are fictional. No real company data is used.

## Business problem
Data sits in separate CSV/Excel/Google Sheets files owned by different teams.
Reports are built by hand, contain errors, and arrive after problems have
already hurt delivery or margin.

## Status
- [x] Step 1: SQLite schema and synthetic raw data generator
- [x] Step 2: Python ETL, validation and Data Quality Report
- [ ] Step 3: SQL KPI views
- [ ] Step 4: Excel auto-report
- [ ] Step 5: Power BI dashboard
- [ ] Step 6: Delay forecast model and AI weekly summary
- [ ] Step 7: SVP 3-slide brief

## Run
    pip install -r requirements.txt
    python generate_data.py          # writes data/raw/*.csv|xlsx and data/_answer_key/
    python -m etl.run_pipeline       # builds data/tex_fasteners.db + reports/data_quality_report.html
    python -m pytest -q              # proves every planted error is caught

## How the ETL treats bad data
| Outcome | Rule |
|---|---|
| Auto-corrected | Only when the fix can be verified another way. Example: a missing unit price is recovered from the order total; a negative quantity is flipped only if the lines then add up to the order total. |
| Duplicate removed | Exact duplicate rows, first occurrence kept. |
| Quarantined | Cannot be verified (e.g. deadline before order date, missing buyer). Saved to `data/quarantine/` for the owning team. Never guessed. |
| Dropped | Healthy row whose parent record was quarantined. Logged, never silent. |
| Flagged | Kept, but a human should confirm (e.g. order with no surviving production record). |

Every decision is written to the `dq_issues` table and summarised in the Data Quality Report.

## Raw files (data/raw)
| File | Source type | Content |
|---|---|---|
| buyers.csv, machines.csv | CSV | Master data |
| orders.csv | CSV | Orders and deadlines |
| order_items.xlsx | Excel | Product lines per order |
| production.csv | CSV | Production runs per machine |
| quality.csv | CSV | Inspections and rejects |
| machine_downtime.csv | CSV | Machine stoppages |
| raw_materials.xlsx | Excel | Brass, zinc, tape, nickel purchases |
| shipments.csv | CSV | Actual ship dates |
| manual_delay_updates.csv | Google Sheet export | Free-text delay reasons, messy dates |

The raw files contain deliberately injected errors (duplicates, impossible
dates, negative quantities, inconsistent labels, price typos, orphan keys).
`data/_answer_key/injected_errors.csv` lists them so the ETL can be tested.
