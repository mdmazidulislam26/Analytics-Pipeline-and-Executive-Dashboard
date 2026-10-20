"""
Build the SVP dashboard as ONE self-contained HTML file from the clean database.

    python -m etl.run_pipeline
    python -m reports.build_html_dashboard      ->  dashboard/SVP_Dashboard.html

The data snapshot is embedded as JSON, so the file opens in any browser with no
server. All data is SIMULATED.
"""
import json
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "tex_fasteners.db"
OUT_PATH = ROOT / "dashboard" / "SVP_Dashboard.html"
MIN_DELIVERED_PER_MONTH = 20


def snapshot():
    con = sqlite3.connect(DB_PATH)
    q = lambda sql: pd.read_sql(sql, con)
    orders = q("SELECT unit, deadline_month, on_time FROM v_order_status WHERE status = 'Delivered'")
    totals = orders.groupby("deadline_month").size()
    months = [m for m in sorted(totals.index) if totals[m] >= MIN_DELIVERED_PER_MONTH]
    trend = (orders[orders["deadline_month"].isin(months)]
             .groupby(["deadline_month", "unit"])["on_time"].agg(["size", "sum"]).reset_index())
    data = {
        "summary": q("SELECT * FROM v_executive_summary").iloc[0].to_dict(),
        "buyers": q("SELECT buyer_name AS name, otd_pct AS otd, delivered_orders AS n, "
                    "avg_days_late_when_late AS late FROM v_otd_by_buyer").to_dict("records"),
        "trend": [{"m": r.deadline_month, "u": r.unit, "n": int(r["size"]), "ok": int(r["sum"])}
                  for _, r in trend.iterrows()],
        "machines": q("SELECT d.machine_id AS id, d.unit, d.downtime_hours AS hours, "
                      "r.reject_rate_pct AS rej FROM v_downtime_by_machine d "
                      "JOIN v_reject_by_machine r USING (machine_id) ORDER BY d.downtime_hours DESC"
                      ).to_dict("records"),
        "reasons": q("SELECT delay_reason_category AS name, late_orders AS n, share_pct AS share "
                     "FROM v_delay_reasons").to_dict("records"),
        "materials": q("""SELECT material AS name,
                 MIN(CASE WHEN month = (SELECT MIN(month) FROM v_material_price_monthly) THEN avg_price_usd_kg END) AS first,
                 MAX(CASE WHEN month = (SELECT MAX(month) FROM v_material_price_monthly) THEN avg_price_usd_kg END) AS last
                 FROM v_material_price_monthly GROUP BY material""").to_dict("records"),
        "risk": q("SELECT order_id AS id, buyer_name AS buyer, delivery_deadline AS due, "
                  "order_value_usd AS value, risk_score AS score, risk_level AS level "
                  "FROM v_open_order_risk LIMIT 8").to_dict("records"),
    }
    con.close()
    return json.loads(json.dumps(data, default=lambda o: o.item() if hasattr(o, "item") else str(o)))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Tex Fasteners – SVP Executive Dashboard</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--tx:#1b1f27;--mu:#667085;--ln:#e3e6eb;--ac:#1f5fa8;--ac2:#e07b00;
--good:#1d7a4f;--warn:#b7791f;--bad:#c0392b;--gb:#e0f3ea;--wb:#fbf0d6;--bb:#fbe3e0;color-scheme:light dark}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14161b;--card:#1d2027;--tx:#eceef2;--mu:#98a0ae;--ln:#2e323b;--ac:#6ea8ee;--ac2:#f0a04b;--good:#6fd0a1;--warn:#e6bd6e;--bad:#f0877b;--gb:#16342a;--wb:#3a2f14;--bb:#3e2220}}
:root[data-theme=dark]{--bg:#14161b;--card:#1d2027;--tx:#eceef2;--mu:#98a0ae;--ln:#2e323b;--ac:#6ea8ee;--ac2:#f0a04b;--good:#6fd0a1;--warn:#e6bd6e;--bad:#f0877b;--gb:#16342a;--wb:#3a2f14;--bb:#3e2220}
*{box-sizing:border-box}html{scroll-padding-top:env(safe-area-inset-top,0px)}
body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:env(safe-area-inset-top,0px) 0 env(safe-area-inset-bottom,0px)}
main{max-width:1100px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:21px;margin:0}h2{font-size:14px;margin:0 0 10px}.sub{color:var(--mu);font-size:12px;margin:2px 0 16px}
.grid{display:grid;gap:12px}.k{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}.c2{grid-template-columns:repeat(auto-fit,minmax(320px,1fr));margin-top:12px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:10px;padding:14px}
.kl{color:var(--mu);font-size:12px}.kv{font-size:24px;font-weight:600}.ks{color:var(--mu);font-size:11px}
.row{display:grid;grid-template-columns:130px 1fr 64px;gap:8px;align-items:center;margin:6px 0;font-size:12px}
.t{background:var(--ln);border-radius:4px;height:14px;overflow:hidden}.t i{display:block;height:100%;border-radius:4px}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.bg-good{background:var(--good)}.bg-warn{background:var(--warn)}.bg-bad{background:var(--bad)}.bg-ac{background:var(--ac)}
.tabs button{border:1px solid var(--ln);background:var(--card);color:var(--tx);padding:3px 10px;border-radius:99px;font-size:12px;cursor:pointer;margin-right:4px}
.tabs button.on{background:var(--ac);color:#fff;border-color:var(--ac)}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--ln)}th{color:var(--mu);font-weight:500}
.pill{padding:1px 8px;border-radius:99px;font-size:11px}.p-High{background:var(--bb);color:var(--bad)}.p-Medium{background:var(--wb);color:var(--warn)}.p-Low{background:var(--gb);color:var(--good)}
svg text{fill:var(--mu);font-size:10px}.note{color:var(--mu);font-size:11px;margin-top:14px}
</style></head><body><main>
<h1>Tex Fasteners – Executive Dashboard</h1>
<p class="sub" id="sub"></p>
<div class="grid k" id="kpis"></div>
<div class="grid c2">
<div class="card"><h2>On-time delivery by buyer</h2><div id="buyers"></div></div>
<div class="card"><h2>OTD trend by deadline month</h2><div class="tabs" id="tabs"></div><div id="trend"></div></div>
<div class="card"><h2>Machines: downtime hours <span class="ks">(reject rate at right)</span></h2><div id="machines"></div></div>
<div class="card"><h2>Why orders were late</h2><div id="reasons"></div><h2 style="margin-top:14px">Raw material price, USD/kg</h2><div id="mats"></div></div>
</div>
<div class="card" style="margin-top:12px"><h2>Open orders to watch</h2><table id="risk"></table></div>
<p class="note">Simulated data. Buyer names are fictional. Risk score is rule-based (schedule, buyer history, machine, rejects). Margin is not shown because the data has no cost figures.</p>
</main>
<script>
const D=__DATA__;const $=s=>document.querySelector(s);const S=D.summary;
const f1=v=>Number(v).toFixed(1),fi=v=>Math.round(v).toLocaleString('en-US');
const cls=v=>v<70?'bad':v<85?'warn':'good';
$('#sub').textContent='Data as of '+S.report_date+' · '+fi(S.total_orders)+' orders';
const K=[['On-time delivery',f1(S.otd_pct)+'%',fi(S.delivered_orders)+' delivered',cls(S.otd_pct)],
['Avg days late',f1(S.avg_days_late_when_late),'when late',''],['Open orders',fi(S.open_orders),S.open_orders_already_late+' already late',''],
['Delivered value','$'+(S.delivered_value_usd/1e6).toFixed(1)+'M','USD',''],['Reject rate',S.overall_reject_rate_pct.toFixed(2)+'%','all inspections',''],
['Downtime',fi(S.total_downtime_hours)+' h','all machines',''],['Ship date unverified',fi(S.ship_date_unverified),'Logistics to confirm',S.ship_date_unverified?'warn':'']];
$('#kpis').innerHTML=K.map(k=>`<div class="card"><div class="kl">${k[0]}</div><div class="kv ${k[3]}">${k[1]}</div><div class="ks">${k[2]}</div></div>`).join('');
const bars=(id,rows,max)=>$(id).innerHTML=rows.map(r=>`<div class="row"><span>${r.l}</span><div class="t"><i class="bg-${r.c}" style="width:${Math.max(2,r.v/max*100)}%"></i></div><b class="${r.c==='ac'?'':r.c}">${r.t}</b></div>`).join('');
bars('#buyers',D.buyers.map(b=>({l:b.name,v:b.otd,c:cls(b.otd),t:f1(b.otd)+'%'})),100);
const mx=Math.max(...D.machines.map(m=>m.hours)),avg=D.machines.reduce((a,m)=>a+m.hours,0)/D.machines.length;
bars('#machines',D.machines.map(m=>({l:m.id+' · '+m.unit.split(' ')[0],v:m.hours,c:(m.hours>1.5*avg&&m.rej>2.5)?'bad':(m.hours>1.5*avg||m.rej>2.5)?'warn':'ac',t:fi(m.hours)+' h · '+f1(m.rej)+'%'})),mx);
bars('#reasons',D.reasons.map(r=>({l:r.name,v:r.n,c:'ac',t:r.n+' ('+fi(r.share)+'%)'})),Math.max(...D.reasons.map(r=>r.n)));
$('#mats').innerHTML='<table><tr><th>Material</th><th>First</th><th>Latest</th><th>Change</th></tr>'+D.materials.map(m=>{const c=(m.last/m.first-1)*100;return `<tr><td>${m.name}</td><td>${m.first.toFixed(2)}</td><td>${m.last.toFixed(2)}</td><td class="${c>10?'bad':c>0?'warn':'good'}">${c>0?'+':''}${f1(c)}%</td></tr>`}).join('')+'</table>';
$('#risk').innerHTML='<tr><th>Order</th><th>Buyer</th><th>Deadline</th><th>Value</th><th>Score</th><th>Level</th></tr>'+D.risk.map(r=>`<tr><td>${r.id}</td><td>${r.buyer}</td><td>${r.due}</td><td>$${fi(r.value)}</td><td>${r.score}</td><td><span class="pill p-${r.level}">${r.level}</span></td></tr>`).join('');
const units=['All units','Zipper','Metal Trims'];let cur=units[0];
function trend(){const ms=[...new Set(D.trend.map(t=>t.m))].sort();
const v=ms.map(m=>{const rows=D.trend.filter(t=>t.m===m&&(cur===units[0]||t.u===cur));const n=rows.reduce((a,r)=>a+r.n,0);return n?rows.reduce((a,r)=>a+r.ok,0)/n*100:null});
const W=420,H=190,p=30,x=i=>p+i*(W-p-8)/(ms.length-1),y=val=>H-22-(val-50)/50*(H-34);
let g='';[50,60,70,80,90,100].forEach(t=>g+=`<line x1="${p}" x2="${W-8}" y1="${y(t)}" y2="${y(t)}" stroke="var(--ln)"/><text x="2" y="${y(t)+3}">${t}%</text>`);
const pts=v.map((val,i)=>val==null?null:x(i)+','+y(Math.max(50,val))).filter(Boolean).join(' ');
const lab=ms.map((m,i)=>i%3==0?`<text x="${x(i)-14}" y="${H-6}">${m}</text>`:'').join('');
$('#trend').innerHTML=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="OTD trend">${g}<polyline fill="none" stroke="var(--ac)" stroke-width="2" points="${pts}"/>${lab}</svg>`;
$('#tabs').innerHTML=units.map(u=>`<button class="${u===cur?'on':''}" data-u="${u}">${u}</button>`).join('');
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{cur=b.dataset.u;trend()})}
trend();
</script></body></html>
"""


def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(TEMPLATE.replace("__DATA__", json.dumps(snapshot())), encoding="utf-8")
    print(f"Saved {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
