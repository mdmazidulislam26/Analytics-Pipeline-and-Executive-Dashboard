"""
Synthetic data generator - Tex Fasteners Executive Intelligence System.

ALL DATA IS SIMULATED. Buyer names are fictional. Nothing here is real company data.

What it produces (data/raw/):
    buyers.csv, machines.csv, orders.csv, order_items.xlsx, production.csv,
    quality.csv, machine_downtime.csv, raw_materials.xlsx, shipments.csv,
    manual_delay_updates.csv   (stand-in for a team Google Sheet export)

Raw files contain deliberately injected errors (duplicates, impossible dates,
messy labels, ...). The exact list is written to data/_answer_key/injected_errors.csv
so the ETL step can be tested against it.

Run:  python generate_data.py
"""
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
rng = np.random.default_rng(SEED)

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
KEY = ROOT / "data" / "_answer_key"
RAW.mkdir(parents=True, exist_ok=True)
KEY.mkdir(parents=True, exist_ok=True)

START = date(2025, 1, 1)
LAST_ORDER = date(2026, 8, 31)
CUTOFF = date(2026, 9, 30)  # data extraction date: later shipments are still "open"
N_ORDERS = 1200
BASE_DELAY = -6.5  # tuning constant: controls overall on-time rate

# ---------------------------------------------------------------- reference data
# id, name, country, payment terms, typical delay bias in days (hidden signal)
BUYERS = [
    ("B001", "Northwind Apparel", "Sweden", 60, -1.0),
    ("B002", "Harbor & Pine", "USA", 45, 0.5),
    ("B003", "Veltra Denim Co", "Spain", 60, 2.5),
    ("B004", "Kestrel Sportswear", "Germany", 30, -0.5),
    ("B005", "Aurora Fashion Group", "United Kingdom", 60, 1.0),
    ("B006", "Brightloom Kids", "Netherlands", 45, 0.0),
    ("B007", "Maple Row Outerwear", "Canada", 60, 3.5),
    ("B008", "Solace Basics", "Denmark", 30, -1.5),
]
BUYER_WEIGHTS = np.array([0.18, 0.16, 0.14, 0.13, 0.12, 0.11, 0.09, 0.07])

# product: (price_min, price_max, (qty_min, qty_max))
PRODUCTS = {
    "Zipper": {
        "Metal Zipper": (0.12, 0.35, (5_000, 80_000)),
        "Nylon Zipper": (0.04, 0.15, (10_000, 200_000)),
        "Slider": (0.02, 0.08, (20_000, 300_000)),
        "Puller": (0.015, 0.06, (20_000, 300_000)),
    },
    "Metal Trims": {
        "Metal Button": (0.05, 0.40, (10_000, 250_000)),
        "Snap Button": (0.04, 0.25, (10_000, 300_000)),
        "Rivet": (0.02, 0.10, (20_000, 300_000)),
    },
}
SIZES = {"Zipper": ["#3", "#5", "#8", "#10"], "Metal Trims": ["14L", "18L", "20L", "24L"]}
COLORS = ["Black", "Navy", "Antique Brass", "Gun Metal", "Silver", "White", "Khaki", "Red"]
FINISHES = ["Polished", "Matte", "Antique", "Nickel-free plating", "Painted"]

MACHINES = {
    "Zipper": [f"ZM-0{i}" for i in range(1, 7)],
    "Metal Trims": [f"MT-0{i}" for i in range(1, 5)],
}
# hidden signals: ZM-04 and MT-02 are the "problem machines"
DOWNTIME_RATE = {"ZM-01": 1.0, "ZM-02": 1.0, "ZM-03": 1.1, "ZM-04": 2.2, "ZM-05": 1.0,
                 "ZM-06": 0.9, "MT-01": 1.0, "MT-02": 2.0, "MT-03": 1.0, "MT-04": 0.9}
REJECT_RATE = {"ZM-04": 0.035, "MT-02": 0.032}
DEFAULT_REJECT = {"Zipper": 0.014, "Metal Trims": 0.012}

DEFECTS = {
    "Zipper": ["Teeth misalignment", "Slider jamming", "Tape shrinkage", "Color mismatch", "Puller crack"],
    "Metal Trims": ["Plating defect", "Dimension error", "Color mismatch", "Burr on edge", "Weak snap"],
}
DOWNTIME_REASONS = (
    ["Die/needle breakage", "Power outage", "Material shortage", "Scheduled maintenance",
     "Mold change", "Operator absence", "Electrical fault"],
    [0.22, 0.12, 0.14, 0.18, 0.14, 0.08, 0.12],
)

MATERIALS = {  # base price USD/kg, monthly drift, qty range, suppliers
    "Brass": (8.2, 0.006, (500, 3000), ["Meghna Metals", "Eastern Alloys Ltd"]),
    "Zinc": (3.0, 0.002, (800, 4000), ["Meghna Metals", "Apex Zinc Trading"]),
    "Polyester Tape": (3.6, 0.001, (300, 2000), ["Dhaka Textile Inputs", "SilkRoute Yarns"]),
    "Nickel": (16.5, 0.004, (50, 300), ["Eastern Alloys Ltd", "Apex Zinc Trading"]),
}

error_log = []


def log_error(file, issue, key, detail=""):
    error_log.append({"file": file, "issue_type": issue, "record_key": key, "detail": detail})


def pick(df_len, n, exclude=None):
    """Pick n distinct row positions, avoiding already-used ones."""
    exclude = exclude if exclude is not None else set()
    pool = np.array([i for i in range(df_len) if i not in exclude])
    chosen = rng.choice(pool, size=n, replace=False)
    exclude.update(int(c) for c in chosen)
    return [int(c) for c in chosen]


# ---------------------------------------------------------------- master data
buyers_df = pd.DataFrame(
    [(b[0], b[1], b[2], b[3]) for b in BUYERS],
    columns=["buyer_id", "buyer_name", "country", "payment_terms_days"],
)
machine_rows = []
for unit, ids in MACHINES.items():
    for m in ids:
        machine_rows.append((m, unit, "Zipper line" if unit == "Zipper" else "Button press",
                             int(rng.integers(2012, 2024))))
machines_df = pd.DataFrame(machine_rows, columns=["machine_id", "unit", "machine_type", "installed_year"])

# ---------------------------------------------------------------- orders and items
span = (LAST_ORDER - START).days
raw_orders = []
for _ in range(N_ORDERS):
    b = BUYERS[int(rng.choice(len(BUYERS), p=BUYER_WEIGHTS))]
    unit = "Zipper" if rng.random() < 0.65 else "Metal Trims"
    od = START + timedelta(days=int(rng.integers(0, span + 1)))
    raw_orders.append((od, b, unit))
raw_orders.sort(key=lambda x: x[0])

orders_meta, item_rows = [], []
item_no = 0
for n, (od, b, unit) in enumerate(raw_orders, start=1):
    oid = f"ORD-{n:05d}"
    dl = od + timedelta(days=int(rng.integers(30, 76)))
    names = list(PRODUCTS[unit])
    chosen = rng.choice(names, size=int(rng.integers(1, 4)), replace=False)
    total_val, total_qty = 0.0, 0
    for p in chosen:
        pmin, pmax, (qmin, qmax) = PRODUCTS[unit][str(p)]
        qty = int(round(rng.uniform(qmin, qmax), -2))
        price = round(float(rng.uniform(pmin, pmax)), 3)
        item_no += 1
        item_rows.append((f"ITM-{item_no:06d}", oid, str(p), str(rng.choice(SIZES[unit])),
                          str(rng.choice(COLORS)), str(rng.choice(FINISHES)), qty, price))
        total_val += qty * price
        total_qty += qty
    orders_meta.append({"order_id": oid, "buyer": b, "unit": unit, "od": od, "dl": dl,
                        "total_value": round(total_val, 2), "total_qty": total_qty})

# ---------------------------------------------------------------- production and quality
run_rows, qc_rows = [], []
run_no = 0
for o in orders_meta:
    n_runs = int(rng.integers(1, 4))
    start = o["od"] + timedelta(days=7)
    end = max(start + timedelta(days=3), o["dl"] - timedelta(days=4))
    days = (end - start).days
    dates = sorted(start + timedelta(days=int(rng.integers(0, days + 1))) for _ in range(n_runs))
    o["runs"] = []
    for d in dates:
        run_no += 1
        machine = MACHINES[o["unit"]][int(rng.integers(len(MACHINES[o["unit"]])))]
        qty = int(o["total_qty"] / n_runs * rng.uniform(0.9, 1.1))
        rid = f"RUN-{run_no:06d}"
        run_rows.append((rid, o["order_id"], d, machine, str(rng.choice(["A", "B", "C"])), qty))
        inspected = max(50, int(qty * rng.uniform(0.06, 0.12)))
        rate = REJECT_RATE.get(machine, DEFAULT_REJECT[o["unit"]]) * rng.uniform(0.6, 1.5)
        rejected = int(rng.binomial(inspected, min(rate, 0.3)))
        defect = str(rng.choice(DEFECTS[o["unit"]])) if rejected > 0 else "None"
        qc_rows.append((f"QC-{run_no:06d}", rid, inspected, rejected, defect))
        o["runs"].append((d, machine, inspected, rejected))

# ---------------------------------------------------------------- machine downtime
downtime_rows = []
dt_by_machine = {}
dt_no = 0
total_days = (CUTOFF - START).days
reasons, weights = DOWNTIME_REASONS
for machine, rate in DOWNTIME_RATE.items():
    n_events = int(rng.poisson(90 * rate))
    events = []
    for _ in range(n_events):
        s = datetime.combine(START, datetime.min.time()) + timedelta(
            days=int(rng.integers(0, total_days)), hours=int(rng.integers(0, 24)),
            minutes=int(rng.integers(0, 60)))
        hours = float(np.clip(rng.lognormal(0.7, 0.8), 0.3, 14))
        e = s + timedelta(hours=hours)
        dt_no += 1
        downtime_rows.append((f"DT-{dt_no:05d}", machine, s, e,
                              str(rng.choice(reasons, p=weights))))
        events.append((s.date(), hours))
    dt_by_machine[machine] = events

# ---------------------------------------------------------------- delivery outcome (hidden model)
ship_rows, manual_rows = [], []
ship_no = 0
LATE_REASONS = ["Machine breakdown", "machine broke down", "Raw material delay", "RM late",
                "Buyer changed spec", "Spec change", "Power cut", "QC rework", "Rework after QC"]
for o in orders_meta:
    first = min(r[0] for r in o["runs"])
    last = max(r[0] for r in o["runs"]) + timedelta(days=5)
    machines_used = {r[1] for r in o["runs"]}
    dt_hours = [sum(h for d, h in dt_by_machine[m] if first <= d <= last) for m in machines_used]
    avg_dt = float(np.mean(dt_hours))
    insp = sum(r[2] for r in o["runs"])
    rej_rate = sum(r[3] for r in o["runs"]) / insp
    season = 2.0 if o["dl"].month in (3, 4, 9, 10, 11) else 0.0
    mu = (BASE_DELAY + o["buyer"][4] + (0.5 if o["unit"] == "Metal Trims" else 0.0)
          + 0.12 * avg_dt + 60 * (rej_rate - 0.013) + season
          + 0.8 * np.log10(o["total_qty"] / 50_000))
    delay = int(round(rng.normal(mu, 4.0)))
    ship_date = max(o["dl"] + timedelta(days=delay), o["od"] + timedelta(days=10))
    o["delay_days"] = (ship_date - o["dl"]).days
    if ship_date > CUTOFF:
        o["open"] = True
        continue
    o["open"] = False
    ship_no += 1
    status = "Partial Shipment" if rng.random() < 0.03 else "Shipped"
    ship_rows.append((f"SHP-{ship_no:05d}", o["order_id"], ship_date, status))
    if o["delay_days"] > 0 and rng.random() < 0.75:
        eta = o["dl"] + timedelta(days=int(o["delay_days"] * rng.uniform(0.6, 1.1)))
        fmt = int(rng.integers(0, 4))
        eta_txt = [eta.isoformat(), eta.strftime("%d/%m/%Y"), eta.strftime("%d-%b-%y"),
                   eta.strftime("%b %d")][fmt]
        manual_rows.append((o["order_id"], eta_txt, str(rng.choice(LATE_REASONS)),
                            str(rng.choice(["Planning", "Merchandising", "Production", "Logistics"]))))

# ---------------------------------------------------------------- raw material purchases
purchase_rows = []
pur_no = 0
months = pd.period_range("2025-01", "2026-09", freq="M")
for material, (base, drift, (qmin, qmax), suppliers) in MATERIALS.items():
    price = base
    for per in months:
        price *= 1 + drift + float(rng.normal(0, 0.02))
        for _ in range(int(rng.integers(4, 7))):
            pur_no += 1
            day = date(per.year, per.month, int(rng.integers(1, 28)))
            purchase_rows.append((f"PUR-{pur_no:05d}", material, str(rng.choice(suppliers)), day,
                                  round(float(rng.uniform(qmin, qmax)), 1),
                                  round(price * float(rng.uniform(0.97, 1.03)), 2)))

# ---------------------------------------------------------------- build dataframes
orders_df = pd.DataFrame(
    [(o["order_id"], o["buyer"][0], o["unit"], o["od"].isoformat(), o["dl"].isoformat(), o["total_value"])
     for o in orders_meta],
    columns=["order_id", "buyer_id", "unit", "order_date", "delivery_deadline", "total_value_usd"])
items_df = pd.DataFrame(item_rows, columns=["item_id", "order_id", "product_type", "size", "color",
                                            "finish", "quantity", "unit_price_usd"])
prod_df = pd.DataFrame(run_rows, columns=["run_id", "order_id", "run_date", "machine_id", "shift",
                                          "produced_qty"])
prod_df["run_date"] = prod_df["run_date"].map(lambda d: d.isoformat())
qc_df = pd.DataFrame(qc_rows, columns=["inspection_id", "run_id", "inspected_qty", "rejected_qty",
                                       "defect_type"])
dt_df = pd.DataFrame(downtime_rows, columns=["downtime_id", "machine_id", "start_time", "end_time", "reason"])
mat_df = pd.DataFrame(purchase_rows, columns=["purchase_id", "material", "supplier", "purchase_date",
                                              "qty_kg", "price_per_kg_usd"])
mat_df["purchase_date"] = mat_df["purchase_date"].map(lambda d: d.isoformat())
ship_df = pd.DataFrame(ship_rows, columns=["shipment_id", "order_id", "actual_ship_date", "status"])
ship_df["actual_ship_date"] = ship_df["actual_ship_date"].map(lambda d: d.isoformat())
manual_df = pd.DataFrame(manual_rows, columns=["order_id", "revised_eta", "delay_reason", "updated_by"])

# ---------------------------------------------------------------- inject errors
# orders.csv ----------------------------------------------------------------
used = set()
idx = pick(len(orders_df), 10, used)  # deadline before order date
for i in idx:
    od = date.fromisoformat(orders_df.at[i, "order_date"])
    orders_df.at[i, "delivery_deadline"] = (od - timedelta(days=int(rng.integers(1, 20)))).isoformat()
    log_error("orders.csv", "deadline_before_order_date", orders_df.at[i, "order_id"])
idx = pick(len(orders_df), 8, used)  # missing buyer
for i in idx:
    log_error("orders.csv", "missing_buyer_id", orders_df.at[i, "order_id"])
    orders_df.at[i, "buyer_id"] = np.nan
idx = pick(len(orders_df), 12, used)  # different date format
for i in idx:
    od = date.fromisoformat(orders_df.at[i, "order_date"])
    orders_df.at[i, "order_date"] = od.strftime("%d-%m-%Y")
    log_error("orders.csv", "date_format_dd-mm-yyyy", orders_df.at[i, "order_id"])
idx = pick(len(orders_df), 60, used)  # messy unit labels
variants = {"Zipper": ["zipper", "ZIPPER ", "Zippers"],
            "Metal Trims": ["Metal trims", "metal_trim", "METAL TRIMS"]}
for i in idx:
    u = orders_df.at[i, "unit"]
    orders_df.at[i, "unit"] = str(rng.choice(variants[u]))
log_error("orders.csv", "inconsistent_unit_label", "60 rows", "case/spelling variants")
dup_idx = pick(len(orders_df), 15, set())  # exact duplicates
dups = orders_df.iloc[dup_idx].copy()
for oid in dups["order_id"]:
    log_error("orders.csv", "duplicate_row", oid)
orders_df = pd.concat([orders_df, dups], ignore_index=True).sample(frac=1, random_state=SEED).reset_index(drop=True)

# order_items.xlsx ----------------------------------------------------------
used = set()
for i in pick(len(items_df), 5, used):
    items_df.at[i, "quantity"] = -int(items_df.at[i, "quantity"])
    log_error("order_items.xlsx", "negative_quantity", items_df.at[i, "item_id"])
for i in pick(len(items_df), 6, used):
    items_df.at[i, "unit_price_usd"] = np.nan
    log_error("order_items.xlsx", "missing_unit_price", items_df.at[i, "item_id"])

# production.csv ------------------------------------------------------------
used = set()
for i in pick(len(prod_df), 8, used):
    prod_df.at[i, "produced_qty"] = -abs(int(prod_df.at[i, "produced_qty"]))
    log_error("production.csv", "negative_produced_qty", prod_df.at[i, "run_id"])
for i in pick(len(prod_df), 10, used):
    prod_df.at[i, "order_id"] = f"ORD-{int(rng.integers(90000, 99999))}"
    log_error("production.csv", "orphan_order_id", prod_df.at[i, "run_id"])

# quality.csv ---------------------------------------------------------------
for i in pick(len(qc_df), 12, set()):
    qc_df.at[i, "rejected_qty"] = int(qc_df.at[i, "inspected_qty"]) + int(rng.integers(1, 50))
    log_error("quality.csv", "rejected_exceeds_inspected", qc_df.at[i, "inspection_id"])

# machine_downtime.csv ------------------------------------------------------
for i in pick(len(dt_df), 6, set()):
    s, e = dt_df.at[i, "start_time"], dt_df.at[i, "end_time"]
    dt_df.at[i, "start_time"], dt_df.at[i, "end_time"] = e, s
    log_error("machine_downtime.csv", "end_before_start", dt_df.at[i, "downtime_id"])

# raw_materials.xlsx --------------------------------------------------------
for i in pick(len(mat_df), 5, set()):
    mat_df.at[i, "price_per_kg_usd"] = round(float(mat_df.at[i, "price_per_kg_usd"]) * 100, 2)
    log_error("raw_materials.xlsx", "price_outlier_x100", mat_df.at[i, "purchase_id"])

# shipments.csv -------------------------------------------------------------
od_lookup = {o["order_id"]: o["od"] for o in orders_meta}
for i in pick(len(ship_df), 8, set()):
    oid = ship_df.at[i, "order_id"]
    ship_df.at[i, "actual_ship_date"] = (od_lookup[oid] - timedelta(days=5)).isoformat()
    log_error("shipments.csv", "ship_date_before_order_date", oid)

# ---------------------------------------------------------------- write files
buyers_df.to_csv(RAW / "buyers.csv", index=False)
machines_df.to_csv(RAW / "machines.csv", index=False)
orders_df.to_csv(RAW / "orders.csv", index=False)
items_df.to_excel(RAW / "order_items.xlsx", index=False)
prod_df.to_csv(RAW / "production.csv", index=False)
qc_df.to_csv(RAW / "quality.csv", index=False)
dt_df.to_csv(RAW / "machine_downtime.csv", index=False)
mat_df.to_excel(RAW / "raw_materials.xlsx", index=False)
ship_df.to_csv(RAW / "shipments.csv", index=False)
manual_df.to_csv(RAW / "manual_delay_updates.csv", index=False)
pd.DataFrame(error_log).to_csv(KEY / "injected_errors.csv", index=False)

# ---------------------------------------------------------------- sanity summary
shipped = [o for o in orders_meta if not o["open"]]
otd = np.mean([o["delay_days"] <= 0 for o in shipped])
print(f"Orders: {len(orders_meta)} (shipped {len(shipped)}, open {len(orders_meta) - len(shipped)})")
print(f"Overall on-time delivery: {otd:.1%}")
by_buyer = {}
for o in shipped:
    by_buyer.setdefault(o["buyer"][1], []).append(o["delay_days"] <= 0)
for name, v in sorted(by_buyer.items(), key=lambda kv: np.mean(kv[1])):
    print(f"  {name:<22} OTD {np.mean(v):.1%}  (n={len(v)})")
print("Downtime hours by machine:")
for m, ev in dt_by_machine.items():
    print(f"  {m}: {sum(h for _, h in ev):7.0f} h  ({len(ev)} events)")
print(f"Rows: items {len(items_df)}, production {len(prod_df)}, quality {len(qc_df)}, "
      f"downtime {len(dt_df)}, materials {len(mat_df)}, shipments {len(ship_df)}, manual {len(manual_df)}")
print(f"Injected error records logged: {len(error_log)}")
