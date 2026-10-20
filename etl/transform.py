"""Transform: clean each table, log every issue, quarantine what cannot be fixed.

Design rules
1. Row-level problems are always detected and logged first, even when the row
   later turns out to belong to a quarantined parent. Nothing is hidden.
2. A value is only auto-corrected when it can be verified another way
   (e.g. a missing unit price is recovered from the order total).
3. Anything that cannot be verified goes to quarantine, never guessed.
"""
import pandas as pd

from .config import (MAX_DOWNTIME_HOURS, PRICE_OUTLIER_RATIO, PRICE_PLAUSIBLE_RANGE,
                     QTY_VERIFY_BAND, RECON_TOLERANCE_USD, REASON_MAP, VALID_SHIFTS,
                     VALID_SHIP_STATUS)
from .validate import (canon_unit, is_blank, parse_date, parse_datetime, parse_month_day)

QUARANTINE_NOTE = "quarantined: cannot be verified automatically, owner to confirm"


def _q(quarantine, row, reason):
    entry = dict(row)
    entry["quarantine_reason"] = reason
    quarantine.append(entry)


def _frame(rows, columns):
    return pd.DataFrame(rows, columns=columns)


# ------------------------------------------------------------------ master data
def clean_master(df, key, dq, src, table):
    df = df.copy()
    for col in df.columns:
        df[col] = df[col].astype("object").map(lambda v: v.strip() if isinstance(v, str) else v)
    dup = df.duplicated(key, keep="first")
    for k in df.loc[dup, key]:
        dq.add(src, table, k, "duplicate_key", "removed: duplicate master record")
    return df.loc[~dup].reset_index(drop=True)


# ------------------------------------------------------------------ orders
ORDER_COLS = ["order_id", "buyer_id", "unit", "order_date", "delivery_deadline", "total_value_usd"]


def clean_orders(df, buyer_ids, dq, src):
    """Returns (clean_df, quarantine_df, meta) where meta maps order_id -> parsed dates/total
    for every order that could be parsed (even if it was quarantined for another reason)."""
    dup = df.duplicated(keep="first")
    for oid in df.loc[dup, "order_id"]:
        dq.add(src, "orders", oid, "duplicate_row", "removed: exact duplicate, first occurrence kept")
    df = df.loc[~dup]

    clean, quarantine, meta, seen = [], [], {}, set()
    for _, r in df.iterrows():
        oid = r["order_id"]
        issues = []

        unit = canon_unit(r["unit"])
        if unit is None:
            issues.append("invalid_unit")
        elif r["unit"] != unit:
            dq.add(src, "orders", oid, "inconsistent_unit_label",
                   f"corrected: standardized '{r['unit']}' to '{unit}'")

        od, f_od = parse_date(r["order_date"])
        dl, f_dl = parse_date(r["delivery_deadline"])
        for value, fmt, label in ((od, f_od, "order_date"), (dl, f_dl, "delivery_deadline")):
            if value is None:
                issues.append(f"invalid_{label}")
            elif fmt != "iso":
                dq.add(src, "orders", oid, f"date_format_{fmt}", "corrected: converted to ISO date")

        buyer = None if is_blank(r["buyer_id"]) else str(r["buyer_id"]).strip()
        if buyer is None:
            issues.append("missing_buyer_id")
        elif buyer not in buyer_ids:
            issues.append("unknown_buyer_id")

        if od and dl and dl < od:
            issues.append("deadline_before_order_date")

        total = pd.to_numeric(r["total_value_usd"], errors="coerce")
        if pd.isna(total) or total < 0:
            issues.append("invalid_total_value")

        if oid in seen:
            issues.append("duplicate_key")
        seen.add(oid)

        if od and dl and not pd.isna(total) and total >= 0:
            meta[oid] = {"od": od, "dl": dl, "total": float(total)}

        for issue in issues:
            dq.add(src, "orders", oid, issue, QUARANTINE_NOTE)
        if issues:
            _q(quarantine, r.to_dict(), "; ".join(issues))
        else:
            clean.append({"order_id": oid, "buyer_id": buyer, "unit": unit,
                          "order_date": od.isoformat(), "delivery_deadline": dl.isoformat(),
                          "total_value_usd": round(float(total), 2)})
    all_ids = set(df["order_id"])
    return _frame(clean, ORDER_COLS), pd.DataFrame(quarantine), meta, all_ids


# ------------------------------------------------------------------ order items
ITEM_COLS = ["item_id", "order_id", "product_type", "size", "color", "finish",
             "quantity", "unit_price_usd"]


def clean_order_items(df, order_meta, dq, src):
    """Returns (clean_df, quarantine_df, broken_order_ids)."""
    df = df.copy()
    df["qty"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["price"] = pd.to_numeric(df["unit_price_usd"], errors="coerce")
    clean, quarantine, broken = [], [], set()

    dup = df.duplicated("item_id", keep="first")
    for _, r in df.loc[dup].iterrows():
        dq.add(src, "order_items", r["item_id"], "duplicate_key", "removed: duplicate item id")
    df = df.loc[~dup]

    for oid, group in df.groupby("order_id", sort=False):
        if oid not in order_meta:
            for _, r in group.iterrows():
                dq.add(src, "order_items", r["item_id"], "orphan_order_id",
                       "quarantined: order not found")
                _q(quarantine, r[["item_id", "order_id", "product_type", "size", "color", "finish",
                                  "quantity", "unit_price_usd"]].to_dict(), "orphan_order_id")
            continue

        total = order_meta[oid]["total"]
        items = []
        for _, r in group.iterrows():
            qty, price = r["qty"], r["price"]
            items.append({
                "raw": r, "qty": None if pd.isna(qty) else abs(float(qty)),
                "neg": bool(pd.notna(qty) and qty < 0),
                "bad_qty": bool(pd.isna(qty) or qty == 0),
                "price": None if pd.isna(price) else float(price),
                "miss": bool(pd.isna(price)),
            })

        n_missing = sum(i["miss"] for i in items)
        ok = not any(i["bad_qty"] for i in items) and n_missing <= 1
        if ok and n_missing == 1:
            known = sum(i["qty"] * i["price"] for i in items if not i["miss"])
            target = next(i for i in items if i["miss"])
            recovered = (total - known) / target["qty"]
            lo, hi = PRICE_PLAUSIBLE_RANGE
            if lo <= recovered <= hi:
                target["price"] = round(recovered, 3)
            else:
                ok = False
        elif ok and any(i["neg"] for i in items):
            known = sum(i["qty"] * i["price"] for i in items)
            ok = abs(known - total) <= RECON_TOLERANCE_USD

        for i in items:
            key = i["raw"]["item_id"]
            if i["bad_qty"] and not i["neg"]:
                dq.add(src, "order_items", key, "invalid_quantity", QUARANTINE_NOTE)
            if i["neg"]:
                dq.add(src, "order_items", key, "negative_quantity",
                       "corrected: sign flipped, verified against order total" if ok else QUARANTINE_NOTE)
            if i["miss"]:
                dq.add(src, "order_items", key, "missing_unit_price",
                       f"corrected: recovered {i['price']} from order total" if ok else QUARANTINE_NOTE)

        for i in items:
            r = i["raw"]
            if ok:
                clean.append({"item_id": r["item_id"], "order_id": oid,
                              "product_type": r["product_type"], "size": r["size"],
                              "color": r["color"], "finish": r["finish"],
                              "quantity": int(i["qty"]), "unit_price_usd": float(i["price"])})
            else:
                broken.add(oid)
                _q(quarantine, r[["item_id", "order_id", "product_type", "size", "color", "finish",
                                  "quantity", "unit_price_usd"]].to_dict(), "unrecoverable order lines")
    return _frame(clean, ITEM_COLS), pd.DataFrame(quarantine), broken


# ------------------------------------------------------------------ production
RUN_COLS = ["run_id", "order_id", "run_date", "machine_id", "shift", "produced_qty"]


def clean_production(df, all_order_ids, valid_orders, order_qty, machine_ids, dq, src):
    df = df.copy()
    df["qty"] = pd.to_numeric(df["produced_qty"], errors="coerce")
    clean, quarantine = [], []
    raw_cols = ["run_id", "order_id", "run_date", "machine_id", "shift", "produced_qty"]

    dup = df.duplicated("run_id", keep="first")
    for rid in df.loc[dup, "run_id"]:
        dq.add(src, "production", rid, "duplicate_key", "removed: duplicate run id")
    df = df.loc[~dup]

    orphan = ~df["order_id"].isin(all_order_ids)
    for _, r in df.loc[orphan].iterrows():
        dq.add(src, "production", r["run_id"], "orphan_order_id", "quarantined: order not found")
        _q(quarantine, r[raw_cols].to_dict(), "orphan_order_id")
    df = df.loc[~orphan].copy()
    df["n_runs"] = df.groupby("order_id")["run_id"].transform("count")

    for _, r in df.iterrows():
        rid, oid = r["run_id"], r["order_id"]
        issues, qty = [], r["qty"]
        d, _fmt = parse_date(r["run_date"])
        if d is None:
            issues.append("invalid_run_date")
        if str(r["machine_id"]).strip() not in machine_ids:
            issues.append("unknown_machine")
        if str(r["shift"]).strip() not in VALID_SHIFTS:
            issues.append("invalid_shift")

        if pd.isna(qty):
            issues.append("invalid_produced_qty")
        elif qty < 0:
            expected = order_qty.get(oid)
            expected = expected / r["n_runs"] if expected else None
            lo, hi = QTY_VERIFY_BAND
            if expected and lo <= abs(qty) / expected <= hi:
                qty = abs(qty)
                dq.add(src, "production", rid, "negative_produced_qty",
                       "corrected: sign flipped, verified against order quantity")
            else:
                issues.append("negative_produced_qty")

        for issue in issues:
            dq.add(src, "production", rid, issue, QUARANTINE_NOTE)
        if issues:
            _q(quarantine, r[raw_cols].to_dict(), "; ".join(issues))
            continue
        if oid not in valid_orders:
            dq.add(src, "production", rid, "parent_order_quarantined",
                   "dropped: parent order quarantined")
            _q(quarantine, r[raw_cols].to_dict(), "parent_order_quarantined")
            continue
        clean.append({"run_id": rid, "order_id": oid, "run_date": d.isoformat(),
                      "machine_id": str(r["machine_id"]).strip(), "shift": str(r["shift"]).strip(),
                      "produced_qty": int(qty)})
    return _frame(clean, RUN_COLS), pd.DataFrame(quarantine)


# ------------------------------------------------------------------ quality
QC_COLS = ["inspection_id", "run_id", "inspected_qty", "rejected_qty", "defect_type"]


def clean_quality(df, all_run_ids, valid_runs, dq, src):
    df = df.copy()
    df["insp"] = pd.to_numeric(df["inspected_qty"], errors="coerce")
    df["rej"] = pd.to_numeric(df["rejected_qty"], errors="coerce")
    clean, quarantine = [], []
    raw_cols = QC_COLS

    dup = df.duplicated("inspection_id", keep="first")
    for k in df.loc[dup, "inspection_id"]:
        dq.add(src, "quality", k, "duplicate_key", "removed: duplicate inspection id")
    df = df.loc[~dup]

    for _, r in df.iterrows():
        key, rid = r["inspection_id"], r["run_id"]
        issues = []
        if pd.isna(r["insp"]) or r["insp"] <= 0 or pd.isna(r["rej"]) or r["rej"] < 0:
            issues.append("invalid_quantity")
        elif r["rej"] > r["insp"]:
            issues.append("rejected_exceeds_inspected")
        if rid not in all_run_ids:
            issues.append("orphan_run_id")

        for issue in issues:
            dq.add(src, "quality", key, issue, QUARANTINE_NOTE)
        if issues:
            _q(quarantine, r[raw_cols].to_dict(), "; ".join(issues))
            continue
        if rid not in valid_runs:
            dq.add(src, "quality", key, "parent_run_removed", "dropped: parent production run removed")
            _q(quarantine, r[raw_cols].to_dict(), "parent_run_removed")
            continue
        clean.append({"inspection_id": key, "run_id": rid, "inspected_qty": int(r["insp"]),
                      "rejected_qty": int(r["rej"]), "defect_type": r["defect_type"]})
    return _frame(clean, QC_COLS), pd.DataFrame(quarantine)


# ------------------------------------------------------------------ downtime
DT_COLS = ["downtime_id", "machine_id", "start_time", "end_time", "reason"]


def clean_downtime(df, machine_ids, dq, src):
    clean, quarantine = [], []
    dup = df.duplicated("downtime_id", keep="first")
    for k in df.loc[dup, "downtime_id"]:
        dq.add(src, "downtime", k, "duplicate_key", "removed: duplicate downtime id")
    df = df.loc[~dup]

    for _, r in df.iterrows():
        key = r["downtime_id"]
        s, e = parse_datetime(r["start_time"]), parse_datetime(r["end_time"])
        issues = []
        if str(r["machine_id"]).strip() not in machine_ids:
            issues.append("unknown_machine")
        if s is None or e is None:
            issues.append("invalid_timestamp")
        elif e < s:
            hours = (s - e).total_seconds() / 3600
            if hours <= MAX_DOWNTIME_HOURS:
                s, e = e, s
                dq.add(src, "downtime", key, "end_before_start",
                       "corrected: start and end were swapped")
            else:
                issues.append("end_before_start")
        elif e == s:
            issues.append("zero_duration")

        for issue in issues:
            dq.add(src, "downtime", key, issue, QUARANTINE_NOTE)
        if issues:
            _q(quarantine, r.to_dict(), "; ".join(issues))
            continue
        clean.append({"downtime_id": key, "machine_id": str(r["machine_id"]).strip(),
                      "start_time": s.strftime("%Y-%m-%d %H:%M:%S"),
                      "end_time": e.strftime("%Y-%m-%d %H:%M:%S"), "reason": r["reason"]})
    return _frame(clean, DT_COLS), pd.DataFrame(quarantine)


# ------------------------------------------------------------------ raw materials
MAT_COLS = ["purchase_id", "material", "supplier", "purchase_date", "qty_kg", "price_per_kg_usd"]


def clean_materials(df, dq, src):
    df = df.copy()
    df["qty"] = pd.to_numeric(df["qty_kg"], errors="coerce")
    df["price"] = pd.to_numeric(df["price_per_kg_usd"], errors="coerce")
    median = df.groupby("material")["price"].transform("median")
    df["ratio"] = df["price"] / median
    clean, quarantine = [], []

    dup = df.duplicated("purchase_id", keep="first")
    for k in df.loc[dup, "purchase_id"]:
        dq.add(src, "materials", k, "duplicate_key", "removed: duplicate purchase id")
    df = df.loc[~dup]

    for _, r in df.iterrows():
        key = r["purchase_id"]
        d, _fmt = parse_date(r["purchase_date"])
        issues, price = [], r["price"]
        if d is None:
            issues.append("invalid_purchase_date")
        if pd.isna(r["qty"]) or r["qty"] <= 0:
            issues.append("invalid_quantity")
        if pd.isna(price) or price <= 0:
            issues.append("invalid_price")
        elif r["ratio"] > PRICE_OUTLIER_RATIO or r["ratio"] < 1 / PRICE_OUTLIER_RATIO:
            fixed = price / 100
            fixed_ratio = fixed / (price / r["ratio"])
            if r["ratio"] > PRICE_OUTLIER_RATIO and 0.5 <= fixed_ratio <= 2.0:
                price = round(float(fixed), 2)
                dq.add(src, "materials", key, "price_outlier_x100",
                       "corrected: price divided by 100 (decimal slip), now matches material median range")
            else:
                issues.append("price_outlier")

        for issue in issues:
            dq.add(src, "materials", key, issue, QUARANTINE_NOTE)
        if issues:
            _q(quarantine, r[["purchase_id", "material", "supplier", "purchase_date", "qty_kg",
                              "price_per_kg_usd"]].to_dict(), "; ".join(issues))
            continue
        clean.append({"purchase_id": key, "material": r["material"], "supplier": r["supplier"],
                      "purchase_date": d.isoformat(), "qty_kg": float(r["qty"]),
                      "price_per_kg_usd": float(price)})
    return _frame(clean, MAT_COLS), pd.DataFrame(quarantine)


# ------------------------------------------------------------------ shipments
SHIP_COLS = ["shipment_id", "order_id", "actual_ship_date", "status"]


def clean_shipments(df, order_meta, all_order_ids, valid_orders, dq, src):
    clean, quarantine, seen_orders = [], [], set()
    dup = df.duplicated("shipment_id", keep="first")
    for k in df.loc[dup, "shipment_id"]:
        dq.add(src, "shipments", k, "duplicate_key", "removed: duplicate shipment id")
    df = df.loc[~dup]

    for _, r in df.iterrows():
        key, oid = r["shipment_id"], r["order_id"]
        issues = []
        d, _fmt = parse_date(r["actual_ship_date"])
        if oid not in all_order_ids:
            issues.append("orphan_order_id")
        if d is None:
            issues.append("invalid_ship_date")
        elif oid in order_meta and d < order_meta[oid]["od"]:
            issues.append("ship_date_before_order_date")
        if str(r["status"]).strip() not in VALID_SHIP_STATUS:
            issues.append("invalid_status")
        if oid in seen_orders:
            issues.append("duplicate_key")
        seen_orders.add(oid)

        for issue in issues:
            dq.add(src, "shipments", key if issue != "ship_date_before_order_date" else oid,
                   issue, QUARANTINE_NOTE)
        if issues:
            _q(quarantine, r[SHIP_COLS].to_dict(), "; ".join(issues))
            continue
        if oid not in valid_orders:
            dq.add(src, "shipments", key, "parent_order_quarantined",
                   "dropped: parent order quarantined")
            _q(quarantine, r[SHIP_COLS].to_dict(), "parent_order_quarantined")
            continue
        clean.append({"shipment_id": key, "order_id": oid, "actual_ship_date": d.isoformat(),
                      "status": str(r["status"]).strip()})
    return _frame(clean, SHIP_COLS), pd.DataFrame(quarantine)


# ------------------------------------------------------------------ manual delay sheet
LOG_COLS = ["order_id", "revised_eta", "delay_reason_category", "updated_by"]


def clean_manual_updates(df, order_meta, valid_orders, dq, src):
    clean, quarantine = [], []
    df = df.drop_duplicates("order_id", keep="last")
    for _, r in df.iterrows():
        oid = r["order_id"]
        raw_row = r[["order_id", "revised_eta", "delay_reason", "updated_by"]].to_dict()
        if oid not in valid_orders:
            dq.add(src, "delay_log", oid, "parent_order_quarantined",
                   "dropped: order not in clean orders")
            _q(quarantine, raw_row, "parent_order_quarantined")
            continue

        eta, fmt = parse_date(r["revised_eta"])
        if eta is None:
            eta = parse_month_day(r["revised_eta"], order_meta[oid]["dl"])
            fmt = "Mon dd (no year)" if eta else None
        if eta is None:
            dq.add(src, "delay_log", oid, "invalid_eta", "corrected: ETA left empty")
        elif fmt != "iso":
            dq.add(src, "delay_log", oid, "messy_date_format",
                   f"corrected: parsed '{r['revised_eta']}' ({fmt}) to ISO date")

        raw_reason = "" if is_blank(r["delay_reason"]) else str(r["delay_reason"]).strip()
        category = REASON_MAP.get(raw_reason.lower(), "Other")
        if raw_reason != category:
            dq.add(src, "delay_log", oid, "inconsistent_reason_text",
                   f"corrected: '{raw_reason}' mapped to '{category}'")

        clean.append({"order_id": oid, "revised_eta": eta.isoformat() if eta else None,
                      "delay_reason_category": category,
                      "updated_by": None if is_blank(r["updated_by"]) else str(r["updated_by"]).strip()})
    return _frame(clean, LOG_COLS), pd.DataFrame(quarantine)
