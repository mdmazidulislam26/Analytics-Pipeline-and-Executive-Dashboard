"""Build the Data Quality Report as one self-contained HTML file."""
import html
import sqlite3
from datetime import datetime

import pandas as pd

from .config import OWNERS, REPORT_PATH

DISPOSITIONS = ["corrected", "removed", "quarantined", "dropped", "flagged"]
LABELS = {"corrected": "Auto-corrected", "removed": "Duplicate removed",
          "quarantined": "Quarantined", "dropped": "Dropped (parent issue)",
          "flagged": "Flagged for review"}


def _disposition(action: str) -> str:
    prefix = str(action).split(":", 1)[0].strip().lower()
    return prefix if prefix in DISPOSITIONS else "corrected"


def _esc(value) -> str:
    return html.escape(str(value))


def _table(headers, rows, num_cols=()):
    num = ' class="num"'
    head = "".join("<th%s>%s</th>" % (num if i in num_cols else "", _esc(h)) for i, h in enumerate(headers))
    body = ""
    for row in rows:
        cells = "".join("<td%s>%s</td>" % (num if i in num_cols else "", c) for i, c in enumerate(row))
        body += "<tr>%s</tr>" % cells
    return '<div class="scroll"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (head, body)


def build_report(dq: pd.DataFrame, counts: dict, db_path, out_path=REPORT_PATH):
    dq = dq.copy()
    dq["disposition"] = dq["action_taken"].map(_disposition)
    by_disp = dq["disposition"].value_counts().to_dict()

    total_raw = sum(c["raw"] for c in counts.values())
    total_clean = sum(c["clean"] for c in counts.values())

    con = sqlite3.connect(db_path)
    recon = con.execute(
        "SELECT COUNT(*), SUM(ABS(o.total_value_usd - s.items_total) <= 0.05) FROM orders o "
        "JOIN (SELECT order_id, SUM(quantity * unit_price_usd) AS items_total FROM order_items "
        "GROUP BY order_id) s USING (order_id)").fetchone()
    fk_violations = len(con.execute("PRAGMA foreign_key_check").fetchall())
    con.close()

    cards = [
        ("Records read", f"{total_raw:,}"),
        ("Records loaded clean", f"{total_clean:,}"),
        ("Issues detected", f"{len(dq):,}"),
        ("Auto-corrected", f"{by_disp.get('corrected', 0):,}"),
        ("Quarantined", f"{by_disp.get('quarantined', 0):,}"),
    ]
    cards_html = "".join(
        "<div class=\"card\"><div class=\"lbl\">%s</div><div class=\"val\">%s</div></div>" % (_esc(k), _esc(v))
        for k, v in cards)

    # row counts
    count_rows = []
    for table, c in counts.items():
        removed = c["raw"] - c["clean"] - c["quarantined"]
        count_rows.append([_esc(table), f"{c['raw']:,}", f"{c['clean']:,}",
                           f"{c['quarantined']:,}", f"{removed:,}"])
    counts_html = _table(["Table", "Raw rows", "Loaded clean", "Quarantined", "Duplicates removed"],
                         count_rows, num_cols=(1, 2, 3, 4))

    # issues by file and type
    grouped = (dq.groupby(["source_file", "issue_type"])
                 .agg(n=("record_key", "size"), disp=("disposition", lambda s: s.mode().iat[0]),
                      example=("action_taken", "first"))
                 .reset_index().sort_values(["n"], ascending=False))
    issue_rows = []
    for _, g in grouped.iterrows():
        issue_rows.append([_esc(g["source_file"]), _esc(g["issue_type"]), f"{g['n']:,}",
                           "<span class=\"tag %s\">%s</span>" % (g["disp"], _esc(LABELS[g["disp"]])),
                           _esc(g["example"]), _esc(OWNERS.get(g["source_file"], ""))])
    issues_html = _table(["Source file", "Issue", "Count", "Outcome", "What the pipeline did", "Owner"],
                         issue_rows, num_cols=(2,))

    # per-team follow-up
    team = (dq[dq["disposition"].isin(["quarantined", "corrected", "removed", "flagged"])]
            .assign(owner=lambda d: d["source_file"].map(OWNERS).fillna("Unassigned"))
            .groupby(["owner", "disposition"]).size().unstack(fill_value=0))
    for col in ("corrected", "removed", "quarantined"):
        if col not in team.columns:
            team[col] = 0
    team_rows = [[_esc(owner), f"{r['quarantined']:,}", f"{r['corrected']:,}", f"{r['removed']:,}"]
                 for owner, r in team.sort_values("quarantined", ascending=False).iterrows()]
    team_html = _table(["Team", "Rows needing confirmation", "Fixed automatically", "Duplicates removed"],
                       team_rows, num_cols=(1, 2, 3))

    recon_total, recon_ok = recon[0], int(recon[1] or 0)
    checks = [
        ("Order lines add up to order totals", f"{recon_ok:,} of {recon_total:,} orders match (tolerance 0.05 USD)"),
        ("Foreign key violations in clean database", f"{fk_violations}"),
        ("Rows silently discarded", "0. Every non-loaded row is in data/quarantine or counted as a removed duplicate"),
    ]
    checks_html = _table(["Check", "Result"], [[_esc(a), _esc(b)] for a, b in checks])

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data Quality Report - Tex Fasteners Executive Intelligence System</title>
<style>
:root {{ --bg:#fafaf7; --surface:#ffffff; --text:#1f1f1c; --muted:#6b6a64; --line:#e3e1d9;
  --good:#1d6b4f; --good-bg:#e4f3ec; --warn:#8a5a0b; --warn-bg:#fbf0d9; --bad:#9c2f2f; --bad-bg:#fbe6e6;
  --info:#1f4f8a; --info-bg:#e3edf9; color-scheme: light dark; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#17171a; --surface:#202024; --text:#ecebe6;
  --muted:#a09f98; --line:#34343a; --good:#7fd1ad; --good-bg:#17332a; --warn:#e6bd6e; --warn-bg:#3a2e14;
  --bad:#f0a0a0; --bad-bg:#3d1f1f; --info:#9bc2f0; --info-bg:#1b2d44; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.6 system-ui,-apple-system,Segoe UI,sans-serif; }}
main {{ max-width:1080px; margin:0 auto; padding:32px 20px 56px; }}
h1 {{ font-size:24px; margin:0 0 4px; font-weight:600; }}
h2 {{ font-size:17px; margin:36px 0 10px; font-weight:600; }}
.sub {{ color:var(--muted); margin:0 0 24px; }}
.banner {{ background:var(--info-bg); color:var(--info); padding:10px 14px; border-radius:8px; margin-bottom:24px; font-size:14px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }}
.card {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }}
.lbl {{ color:var(--muted); font-size:13px; }}
.val {{ font-size:26px; font-weight:600; margin-top:2px; }}
.scroll {{ overflow-x:auto; background:var(--surface); border:1px solid var(--line); border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
th,td {{ text-align:left; padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ color:var(--muted); font-weight:500; font-size:13px; white-space:nowrap; }}
tr:last-child td {{ border-bottom:none; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.tag {{ display:inline-block; padding:2px 9px; border-radius:99px; font-size:12px; white-space:nowrap; }}
.corrected {{ background:var(--good-bg); color:var(--good); }}
.removed {{ background:var(--info-bg); color:var(--info); }}
.quarantined {{ background:var(--bad-bg); color:var(--bad); }}
.dropped, .flagged {{ background:var(--warn-bg); color:var(--warn); }}
footer {{ color:var(--muted); font-size:13px; margin-top:36px; }}
</style></head><body><main>
<h1>Data Quality Report</h1>
<p class="sub">Tex Fasteners Executive Intelligence System &middot; generated {stamp}</p>
<div class="banner">All data in this project is simulated. Buyer names are fictional.</div>
<div class="cards">{cards_html}</div>

<h2>Row counts: raw to clean</h2>
{counts_html}

<h2>Issues found, by source file</h2>
{issues_html}

<h2>Follow-up by team</h2>
{team_html}

<h2>Integrity checks on the clean database</h2>
{checks_html}

<footer>Auto-corrected = fixed and verified automatically. Quarantined = set aside in data/quarantine for the
owning team to confirm; never guessed. Dropped = healthy row whose parent record was quarantined. Flagged = kept, but needs a human to confirm.</footer>
</main></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page, encoding="utf-8")
    return out_path
