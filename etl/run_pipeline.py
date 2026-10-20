"""Run the whole pipeline with one command:   python -m etl.run_pipeline"""
import time

import pandas as pd

from . import apply_views, extract, load, report, transform
from .config import DB_PATH, QUARANTINE_DIR, REPORT_PATH, SOURCES
from .validate import DQLog


def run(verbose: bool = True) -> dict:
    t0 = time.time()
    raw = extract.read_all()
    dq = DQLog()
    src = SOURCES
    quarantine = {}

    # --- master data
    buyers = transform.clean_master(raw["buyers"], "buyer_id", dq, src["buyers"], "buyers")
    machines = transform.clean_master(raw["machines"], "machine_id", dq, src["machines"], "machines")
    buyers["payment_terms_days"] = pd.to_numeric(buyers["payment_terms_days"]).astype(int)
    machines["installed_year"] = pd.to_numeric(machines["installed_year"]).astype(int)
    buyer_ids, machine_ids = set(buyers["buyer_id"]), set(machines["machine_id"])

    # --- orders and their lines (lines can send an order to quarantine)
    orders, q_orders, meta, all_order_ids = transform.clean_orders(
        raw["orders"], buyer_ids, dq, src["orders"])
    items, q_items, broken = transform.clean_order_items(
        raw["order_items"], meta, dq, src["order_items"])
    if broken:
        mask = orders["order_id"].isin(broken)
        for oid in orders.loc[mask, "order_id"]:
            dq.add(src["orders"], "orders", oid, "order_items_unrecoverable",
                   "quarantined: order lines could not be verified")
        q_orders = pd.concat([q_orders, orders.loc[mask].assign(
            quarantine_reason="order_items_unrecoverable")], ignore_index=True)
        orders = orders.loc[~mask].reset_index(drop=True)
    valid_orders = set(orders["order_id"])

    # items of orders that did not make it are dropped (logged, never silent)
    drop_items = ~items["order_id"].isin(valid_orders)
    for _, r in items.loc[drop_items].iterrows():
        dq.add(src["order_items"], "order_items", r["item_id"], "parent_order_quarantined",
               "dropped: parent order quarantined")
    q_items = pd.concat([q_items, items.loc[drop_items].assign(
        quarantine_reason="parent_order_quarantined")], ignore_index=True)
    items = items.loc[~drop_items].reset_index(drop=True)

    # --- production, quality
    order_qty = items.groupby("order_id")["quantity"].sum().to_dict()
    runs, q_runs = transform.clean_production(
        raw["production"], all_order_ids, valid_orders, order_qty, machine_ids, dq, src["production"])
    for oid in sorted(valid_orders - set(runs["order_id"])):
        dq.add(src["production"], "production", oid, "order_without_production_runs",
               "flagged: order kept, but no production record survived cleaning; owner to confirm")
    all_run_ids = set(raw["production"]["run_id"])
    quality, q_quality = transform.clean_quality(
        raw["quality"], all_run_ids, set(runs["run_id"]), dq, src["quality"])

    # --- the rest
    downtime, q_downtime = transform.clean_downtime(raw["downtime"], machine_ids, dq, src["downtime"])
    materials, q_materials = transform.clean_materials(raw["materials"], dq, src["materials"])
    shipments, q_shipments = transform.clean_shipments(
        raw["shipments"], meta, all_order_ids, valid_orders, dq, src["shipments"])
    delay_log, q_delay = transform.clean_manual_updates(
        raw["manual_updates"], meta, valid_orders, dq, src["manual_updates"])

    clean = {"buyers": buyers, "machines": machines, "orders": orders, "order_items": items,
             "production": runs, "quality": quality, "downtime": downtime, "materials": materials,
             "shipments": shipments, "manual_updates": delay_log}
    quarantine = {"orders": q_orders, "order_items": q_items, "production": q_runs,
                  "quality": q_quality, "downtime": q_downtime, "materials": q_materials,
                  "shipments": q_shipments, "manual_updates": q_delay}

    # --- save quarantine files
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    for old in QUARANTINE_DIR.glob("*.csv"):
        old.unlink()
    for name, frame in quarantine.items():
        if len(frame):
            frame.to_csv(QUARANTINE_DIR / f"{name}_quarantine.csv", index=False)

    # --- load and report
    dq_frame = dq.frame()
    load.load(clean, dq_frame)
    apply_views.apply()
    counts = {name: {"raw": len(raw[name]), "clean": len(clean[name]),
                     "quarantined": len(quarantine.get(name, []))} for name in clean}
    report.build_report(dq_frame, counts, DB_PATH)

    if verbose:
        print(f"Pipeline finished in {time.time() - t0:.1f}s")
        print(f"{'table':<16}{'raw':>7}{'clean':>8}{'quarantined':>13}{'dups removed':>14}")
        for name, c in counts.items():
            removed = c["raw"] - c["clean"] - c["quarantined"]
            print(f"{name:<16}{c['raw']:>7}{c['clean']:>8}{c['quarantined']:>13}{removed:>14}")
        print(f"Issues logged: {len(dq_frame)}")
        print(f"Database: {DB_PATH}")
        print(f"Report:   {REPORT_PATH}")
    return {"dq": dq_frame, "counts": counts, "clean": clean, "quarantine": quarantine}


if __name__ == "__main__":
    run()
