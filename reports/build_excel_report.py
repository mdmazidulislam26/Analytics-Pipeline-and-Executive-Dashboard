"""
Build the SVP weekly Excel report from the clean database.

Run (from the project root, after the pipeline):
    python -m etl.run_pipeline
    python -m reports.build_excel_report

Design: raw view data goes into the Data_* sheets as values; every KPI, table
and trend on the report sheets is an Excel FORMULA over those sheets, so the
workbook stays live if someone edits or refreshes the data. All data is SIMULATED.
"""
import sqlite3
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, DataBarRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "tex_fasteners.db"
OUT_PATH = ROOT / "reports" / "output" / "SVP_Weekly_Report.xlsx"

MIN_DELIVERED_PER_MONTH = 20   # months with fewer delivered orders are left out of the OTD trend
OTD_GREEN, OTD_AMBER = 0.85, 0.70   # display thresholds for colouring OTD cells

# ------------------------------------------------------------------ styles
FONT = "Arial"
F_BASE = Font(name=FONT, size=10)
F_BOLD = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=16, bold=True, color="1F3A5F")
F_H2 = Font(name=FONT, size=12, bold=True, color="1F3A5F")
F_NOTE = Font(name=FONT, size=9, italic=True, color="6B6B6B")
F_HEAD = Font(name=FONT, size=10, bold=True, color="FFFFFF")
F_INPUT = Font(name=FONT, size=10, color="0000FF")          # hardcoded inputs are blue
F_KPI = Font(name=FONT, size=16, bold=True, color="1F3A5F")
F_KPI_LABEL = Font(name=FONT, size=9, color="6B6B6B")
FILL_HEAD = PatternFill("solid", start_color="1F3A5F")
FILL_TILE = PatternFill("solid", start_color="EEF3F9")
FILL_INPUT = PatternFill("solid", start_color="FFFF00")
FILL_RED = PatternFill("solid", start_color="F8D0D0", end_color="F8D0D0")
FILL_AMBER = PatternFill("solid", start_color="FBE8B8", end_color="FBE8B8")
FILL_GREEN = PatternFill("solid", start_color="CDEBD9", end_color="CDEBD9")
THIN = Side(style="thin", color="D5D9DE")
BORDER = Border(bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

LINE_COLORS = ["1F5FA8", "E07B00", "2E8B57", "8E44AD"]


def style_lines(chart):
    """Straight lines with distinct colours (openpyxl defaults to smoothed, similar-looking lines)."""
    for series, color in zip(chart.series, LINE_COLORS):
        series.smooth = False
        series.graphicalProperties.line.solidFill = color
        series.graphicalProperties.line.width = 22000


FMT_INT, FMT_USD, FMT_PCT, FMT_DEC = "#,##0", "$#,##0", "0.0%", "0.0"
FMT_DATE = "yyyy-mm-dd"


def query(con, sql):
    return pd.read_sql(sql, con)


def style_range(ws, cell_range, font=F_BASE):
    for row in ws[cell_range]:
        for c in row:
            c.font = font


def header_row(ws, row, headers, col_start=1):
    for i, h in enumerate(headers):
        c = ws.cell(row=row, column=col_start + i, value=h)
        c.font, c.fill, c.alignment = F_HEAD, FILL_HEAD, CENTER


def write_data_sheet(wb, name, df, date_cols=(), formats=None):
    """Dump a DataFrame as values. Returns (column letters by name, last row)."""
    ws = wb.create_sheet(name)
    formats = formats or {}
    header_row(ws, 1, list(df.columns))
    letters = {}
    for j, col in enumerate(df.columns, start=1):
        letters[col] = ws.cell(row=1, column=j).column_letter
        ws.column_dimensions[letters[col]].width = max(12, min(30, len(col) + 4))
    for i, rec in enumerate(df.itertuples(index=False), start=2):
        for j, (col, v) in enumerate(zip(df.columns, rec), start=1):
            if pd.isna(v):
                v = None
            elif col in date_cols:
                v = pd.to_datetime(v).to_pydatetime()
            c = ws.cell(row=i, column=j, value=v)
            c.font = F_BASE
            if col in date_cols:
                c.number_format = FMT_DATE
            elif col in formats:
                c.number_format = formats[col]
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return letters, len(df) + 1


class Ref:
    """Absolute range helper: Ref('Data_Orders', letters, last)('status') -> Data_Orders!$J$2:$J$1183"""
    def __init__(self, sheet, letters, last):
        self.sheet, self.letters, self.last = sheet, letters, last

    def __call__(self, col):
        L = self.letters[col]
        return f"{self.sheet}!${L}$2:${L}${self.last}"


def main():
    con = sqlite3.connect(DB_PATH)
    report_date = query(con, "SELECT report_date FROM v_params").iat[0, 0]
    orders = query(con, "SELECT * FROM v_order_status ORDER BY order_id")
    buyers = query(con, "SELECT buyer_name FROM v_otd_by_buyer")          # worst OTD first
    machines = query(con, """
        SELECT d.machine_id, d.unit, d.downtime_hours, d.events, r.inspected_qty, r.rejected_qty
        FROM v_downtime_by_machine d JOIN v_reject_by_machine r USING (machine_id)
        ORDER BY d.downtime_hours DESC""")
    materials = query(con, "SELECT material, month, qty_kg, avg_price_usd_kg FROM v_material_price_monthly")
    reasons = query(con, "SELECT delay_reason_category, late_orders FROM v_delay_reasons")
    risk = query(con, "SELECT * FROM v_open_order_risk")
    con.close()

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    trend = wb.create_sheet("OTD_Trend")
    mats = wb.create_sheet("Materials")
    risk_ws = wb.create_sheet("Risk_Orders")
    notes = wb.create_sheet("Notes")

    # ---------------------------------------------------------------- data sheets
    L_o, last_o = write_data_sheet(wb, "Data_Orders", orders,
                                   date_cols=("order_date", "delivery_deadline", "actual_ship_date"),
                                   formats={"total_value_usd": FMT_USD})
    L_m, last_m = write_data_sheet(wb, "Data_Machines", machines)
    L_p, last_p = write_data_sheet(wb, "Data_Materials", materials)
    L_r, last_r = write_data_sheet(wb, "Data_Reasons", reasons)
    O, M, P, R = (Ref("Data_Orders", L_o, last_o), Ref("Data_Machines", L_m, last_m),
                  Ref("Data_Materials", L_p, last_p), Ref("Data_Reasons", L_r, last_r))

    # ---------------------------------------------------------------- notes (inputs live here)
    notes.column_dimensions["A"].width = 62
    notes.column_dimensions["B"].width = 16
    notes.column_dimensions["C"].width = 70
    notes["A1"], notes["A1"].font = "Notes, definitions and inputs", F_TITLE
    notes["A3"], notes["A3"].font = "Inputs (blue on yellow = editable, Summary flags update automatically)", F_H2
    notes["A4"], notes["B4"] = "Downtime flag: hours above this multiple of the fleet average", 1.5
    notes["C4"] = "Assumption chosen for this project, not a company standard"
    notes["A5"], notes["B5"] = "Reject-rate flag: rate above this level", 0.025
    notes["C5"] = "Assumption chosen for this project, not a company standard"
    notes["B4"].number_format, notes["B5"].number_format = "0.0x", FMT_PCT
    for ref in ("B4", "B5"):
        notes[ref].font, notes[ref].fill = F_INPUT, FILL_INPUT
    THR_DT, THR_REJ = "Notes!$B$4", "Notes!$B$5"

    defs = [
        ("Definitions", None),
        ("On-Time Delivery (OTD) %", "Delivered orders shipped on or before the delivery deadline, divided by all delivered orders."),
        ("Delivered / Open / Ship date unverified", "Delivered = shipment recorded. Open = not yet shipped. Unverified = shipment record had an impossible date and awaits Logistics confirmation."),
        ("Reject rate %", "Rejected pieces divided by inspected pieces, from quality inspections."),
        ("Risk score (Risk_Orders)", "Rule-based 0-100: schedule pressure (up to 40) + buyer's historical late rate (up to 30) + problem machine used (15) + high reject rate (15). High >= 60, Medium >= 35."),
        ("Data and limits", None),
        ("All data is SIMULATED", "Buyer names are fictional. Numbers illustrate the method, not real company performance."),
        ("No cost data", "Margin cannot be computed. The Materials sheet shows raw-material price trends as a cost-pressure indicator instead."),
        ("OTD trend months", f"Months with fewer than {MIN_DELIVERED_PER_MONTH} delivered orders are omitted to avoid misleading percentages."),
        ("Refresh", "python -m etl.run_pipeline  then  python -m reports.build_excel_report"),
    ]
    r = 8
    for label, text in defs:
        notes.cell(row=r, column=1, value=label).font = F_H2 if text is None else F_BOLD
        if text:
            c = notes.cell(row=r, column=2, value=text)
            c.font = F_BASE
            notes.merge_cells(start_row=r, start_column=2, end_row=r, end_column=3)
            c.alignment = Alignment(wrap_text=True, vertical="top")
            notes.row_dimensions[r].height = 30
        r += 1
    style_range(notes, "A4:C5")
    for ref in ("B4", "B5"):
        notes[ref].font = F_INPUT

    # ---------------------------------------------------------------- summary sheet
    ws = summary
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 24
    for col in "CDEF":
        ws.column_dimensions[col].width = 17
    ws.column_dimensions["G"].width = 3
    ws["A1"], ws["A1"].font = "Tex Fasteners - SVP Weekly Executive Report", F_TITLE
    ws["A2"], ws["A2"].font = f"Data as of {report_date}", F_BASE
    ws["A3"], ws["A3"].font = "Simulated data. Buyer names are fictional.", F_NOTE

    kpis = [
        # (label, formula, format)
        ("Delivered orders", f'=COUNTIFS({O("status")},"Delivered")', FMT_INT),
        ("On-time delivery", f'=IFERROR(SUMIFS({O("on_time")},{O("status")},"Delivered")/B6,0)', FMT_PCT),
        ("Avg days late (late orders)", f'=IFERROR(AVERAGEIFS({O("delay_days")},{O("status")},"Delivered",{O("delay_days")},">0"),0)', FMT_DEC),
        ("Open orders", f'=COUNTIFS({O("status")},"Open")', FMT_INT),
        ("Ship date unverified", f'=COUNTIFS({O("status")},"Ship date unverified")', FMT_INT),
        ("Delivered value (USD)", f'=SUMIFS({O("total_value_usd")},{O("status")},"Delivered")', FMT_USD),
        ("Reject rate", f'=IFERROR(SUM({M("rejected_qty")})/SUM({M("inspected_qty")}),0)', FMT_PCT),
        ("Machine downtime (hours)", f'=SUM({M("downtime_hours")})', FMT_INT),
    ]
    # tiles: row 5/6 (first four, columns A-D), row 8/9 (last four, columns A-D)
    # KPI formulas refer to B6 for "Delivered orders", so place tile 1 in B.
    tile_cols = ["B", "C", "D", "E"]
    ws["A5"], ws["A5"].font = "Delivery", F_BOLD
    ws["A8"], ws["A8"].font = "Exceptions and operations", F_BOLD
    for idx, (label, formula, fmt) in enumerate(kpis):
        col = tile_cols[idx % 4]
        lab_row, val_row = (5, 6) if idx < 4 else (8, 9)
        lc, vc = ws[f"{col}{lab_row}"], ws[f"{col}{val_row}"]
        lc.value, lc.font, lc.fill, lc.alignment = label, F_KPI_LABEL, FILL_TILE, CENTER
        vc.value, vc.font, vc.fill, vc.alignment = formula, F_KPI, FILL_TILE, CENTER
        vc.number_format = fmt
        ws.row_dimensions[lab_row].height = 28
        ws.row_dimensions[val_row].height = 30
    ws.conditional_formatting.add("C6", CellIsRule(operator="lessThan", formula=[str(OTD_AMBER)], fill=FILL_RED))
    ws.conditional_formatting.add("C6", CellIsRule(operator="lessThan", formula=[str(OTD_GREEN)], fill=FILL_AMBER))
    ws.conditional_formatting.add("C6", CellIsRule(operator="greaterThanOrEqual", formula=[str(OTD_GREEN)], fill=FILL_GREEN))

    # --- buyer table
    r0 = 12
    ws.cell(row=r0 - 1, column=1, value="On-time delivery by buyer (worst first)").font = F_H2
    header_row(ws, r0, ["Buyer", "Delivered", "On time", "OTD %", "Avg days late", "Delivered value (USD)"])
    first = r0 + 1
    for i, name in enumerate(buyers["buyer_name"], start=first):
        ws.cell(row=i, column=1, value=name)
        ws.cell(row=i, column=2, value=f'=COUNTIFS({O("buyer_name")},$A{i},{O("status")},"Delivered")')
        ws.cell(row=i, column=3, value=f'=SUMIFS({O("on_time")},{O("buyer_name")},$A{i},{O("status")},"Delivered")')
        ws.cell(row=i, column=4, value=f"=IFERROR(C{i}/B{i},0)")
        ws.cell(row=i, column=5, value=f'=IFERROR(AVERAGEIFS({O("delay_days")},{O("buyer_name")},$A{i},{O("status")},"Delivered",{O("delay_days")},">0"),0)')
        ws.cell(row=i, column=6, value=f'=SUMIFS({O("total_value_usd")},{O("buyer_name")},$A{i},{O("status")},"Delivered")')
    last = first + len(buyers) - 1
    tot = last + 1
    ws.cell(row=tot, column=1, value="All buyers").font = F_BOLD
    ws.cell(row=tot, column=2, value=f"=SUM(B{first}:B{last})")
    ws.cell(row=tot, column=3, value=f"=SUM(C{first}:C{last})")
    ws.cell(row=tot, column=4, value=f"=IFERROR(C{tot}/B{tot},0)")
    ws.cell(row=tot, column=5, value=f'=IFERROR(AVERAGEIFS({O("delay_days")},{O("status")},"Delivered",{O("delay_days")},">0"),0)')
    ws.cell(row=tot, column=6, value=f"=SUM(F{first}:F{last})")
    for i in range(first, tot + 1):
        for col, fmt in ((2, FMT_INT), (3, FMT_INT), (4, FMT_PCT), (5, FMT_DEC), (6, FMT_USD)):
            ws.cell(row=i, column=col).number_format = fmt
        for col in range(1, 7):
            c = ws.cell(row=i, column=col)
            c.border = BORDER
            c.font = F_BOLD if i == tot else F_BASE
    rng = f"D{first}:D{last}"
    ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=[str(OTD_AMBER)], fill=FILL_RED))
    ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=[str(OTD_GREEN)], fill=FILL_AMBER))
    ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThanOrEqual", formula=[str(OTD_GREEN)], fill=FILL_GREEN))
    buyer_hdr, buyer_first, buyer_last = r0, first, last

    # --- machine table
    m0 = tot + 3
    ws.cell(row=m0 - 1, column=1, value="Machines: downtime and rejects").font = F_H2
    header_row(ws, m0, ["Machine", "Unit", "Downtime hours", "Downtime events", "Reject rate", "Flag"])
    mfirst = m0 + 1
    for i, rec in enumerate(machines.itertuples(index=False), start=mfirst):
        ws.cell(row=i, column=1, value=rec.machine_id)
        ws.cell(row=i, column=2, value=rec.unit)
        ws.cell(row=i, column=3, value=f'=SUMIFS({M("downtime_hours")},{M("machine_id")},$A{i})')
        ws.cell(row=i, column=4, value=f'=SUMIFS({M("events")},{M("machine_id")},$A{i})')
        ws.cell(row=i, column=5, value=f'=IFERROR(SUMIFS({M("rejected_qty")},{M("machine_id")},$A{i})/SUMIFS({M("inspected_qty")},{M("machine_id")},$A{i}),0)')
    mlast = mfirst + len(machines) - 1
    for i in range(mfirst, mlast + 1):
        ws.cell(row=i, column=6, value=(
            f'=IF(AND(C{i}>{THR_DT}*AVERAGE($C${mfirst}:$C${mlast}),E{i}>{THR_REJ}),"Investigate",'
            f'IF(OR(C{i}>{THR_DT}*AVERAGE($C${mfirst}:$C${mlast}),E{i}>{THR_REJ}),"Watch","OK"))'))
        ws.cell(row=i, column=3).number_format = FMT_INT
        ws.cell(row=i, column=4).number_format = FMT_INT
        ws.cell(row=i, column=5).number_format = "0.00%"
        for col in range(1, 7):
            c = ws.cell(row=i, column=col)
            c.font, c.border = F_BASE, BORDER
    frng = f"F{mfirst}:F{mlast}"
    ws.conditional_formatting.add(frng, CellIsRule(operator="equal", formula=['"Investigate"'], fill=FILL_RED))
    ws.conditional_formatting.add(frng, CellIsRule(operator="equal", formula=['"Watch"'], fill=FILL_AMBER))
    ws.conditional_formatting.add(frng, CellIsRule(operator="equal", formula=['"OK"'], fill=FILL_GREEN))
    ws.conditional_formatting.add(f"C{mfirst}:C{mlast}", DataBarRule(start_type="num", start_value=0, end_type="max", color="8DB0D9"))

    # --- delay reasons
    d0 = mlast + 3
    ws.cell(row=d0 - 1, column=1, value="Why orders were late (from the planning team's sheet)").font = F_H2
    header_row(ws, d0, ["Reason", "Late orders", "Share"])
    dfirst = d0 + 1
    for i, cat in enumerate(reasons["delay_reason_category"], start=dfirst):
        ws.cell(row=i, column=1, value=cat)
        ws.cell(row=i, column=2, value=f'=SUMIFS({R("late_orders")},{R("delay_reason_category")},$A{i})')
    dlast = dfirst + len(reasons) - 1
    for i in range(dfirst, dlast + 1):
        ws.cell(row=i, column=3, value=f"=IFERROR(B{i}/SUM($B${dfirst}:$B${dlast}),0)")
        ws.cell(row=i, column=2).number_format = FMT_INT
        ws.cell(row=i, column=3).number_format = FMT_PCT
        for col in range(1, 4):
            c = ws.cell(row=i, column=col)
            c.font, c.border = F_BASE, BORDER
    ws.conditional_formatting.add(f"C{dfirst}:C{dlast}", DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1, color="8DB0D9"))

    # --- top open orders by risk (linked to Risk_Orders)
    k0 = dlast + 3
    ws.cell(row=k0 - 1, column=1, value="Open orders to watch (highest risk score)").font = F_H2
    header_row(ws, k0, ["Order", "Buyer", "Deadline", "Value (USD)", "Risk score", "Level"])
    risk_cols = list(risk.columns)
    link = ["order_id", "buyer_name", "delivery_deadline", "order_value_usd", "risk_score", "risk_level"]
    letters_risk = {c: chr(ord("A") + risk_cols.index(c)) for c in link}
    for n in range(min(8, len(risk))):
        row = k0 + 1 + n
        for j, col in enumerate(link, start=1):
            c = ws.cell(row=row, column=j, value=f"=Risk_Orders!{letters_risk[col]}{n + 2}")
            c.font, c.border = F_BASE, BORDER
        ws.cell(row=row, column=3).number_format = FMT_DATE
        ws.cell(row=row, column=4).number_format = FMT_USD
    krange = f"F{k0 + 1}:F{k0 + 8}"
    ws.conditional_formatting.add(krange, CellIsRule(operator="equal", formula=['"High"'], fill=FILL_RED))
    ws.conditional_formatting.add(krange, CellIsRule(operator="equal", formula=['"Medium"'], fill=FILL_AMBER))
    ws.conditional_formatting.add(krange, CellIsRule(operator="equal", formula=['"Low"'], fill=FILL_GREEN))

    # --- charts
    ch = BarChart()
    ch.type, ch.title, ch.style = "bar", "On-time delivery % by buyer", 10
    ch.add_data(Reference(ws, min_col=4, min_row=buyer_hdr, max_row=buyer_last), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=buyer_first, max_row=buyer_last))
    ch.y_axis.scaling.min, ch.y_axis.scaling.max, ch.y_axis.number_format = 0, 1, "0%"
    ch.x_axis.delete = ch.y_axis.delete = False
    ch.legend = None
    ch.height, ch.width = 8.5, 16
    ws.add_chart(ch, "H5")

    ch2 = BarChart()
    ch2.type, ch2.title, ch2.style = "col", "Machine downtime (hours)", 10
    ch2.add_data(Reference(ws, min_col=3, min_row=m0, max_row=mlast), titles_from_data=True)
    ch2.set_categories(Reference(ws, min_col=1, min_row=mfirst, max_row=mlast))
    ch2.x_axis.delete = ch2.y_axis.delete = False
    ch2.legend = None
    ch2.height, ch2.width = 8.5, 16
    ws.add_chart(ch2, f"H{m0 - 1}")

    # ---------------------------------------------------------------- OTD trend
    t = trend
    t.sheet_view.showGridLines = False
    t["A1"], t["A1"].font = "On-time delivery trend by deadline month", F_TITLE
    t["A2"], t["A2"].font = f"Months with fewer than {MIN_DELIVERED_PER_MONTH} delivered orders are omitted.", F_NOTE
    header_row(t, 4, ["Month", "Zipper delivered", "Zipper on time", "Zipper OTD %",
                      "Metal Trims delivered", "Metal Trims on time", "Metal Trims OTD %", "All units OTD %"])
    delivered = orders[orders["status"] == "Delivered"]
    counts = delivered.groupby("deadline_month").size()
    months = [m for m in sorted(counts.index) if counts[m] >= MIN_DELIVERED_PER_MONTH]
    for i, m in enumerate(months, start=5):
        t.cell(row=i, column=1, value=m)
        for unit, c0 in (("Zipper", 2), ("Metal Trims", 5)):
            t.cell(row=i, column=c0, value=f'=COUNTIFS({O("deadline_month")},$A{i},{O("unit")},"{unit}",{O("status")},"Delivered")')
            t.cell(row=i, column=c0 + 1, value=f'=SUMIFS({O("on_time")},{O("deadline_month")},$A{i},{O("unit")},"{unit}",{O("status")},"Delivered")')
            a, b = t.cell(row=i, column=c0).column_letter, t.cell(row=i, column=c0 + 1).column_letter
            t.cell(row=i, column=c0 + 2, value=f"=IFERROR({b}{i}/{a}{i},0)")
        t.cell(row=i, column=8, value=f"=IFERROR((C{i}+F{i})/(B{i}+E{i}),0)")
        for col in range(1, 9):
            c = t.cell(row=i, column=col)
            c.font, c.border = F_BASE, BORDER
            c.number_format = FMT_PCT if col in (4, 7, 8) else FMT_INT
    tlast = 4 + len(months)
    for col in "ABCDEFGH":
        t.column_dimensions[col].width = 17
    lc = LineChart()
    lc.title, lc.style, lc.height, lc.width = "OTD % by unit", 12, 9, 20
    for col in (4, 7):
        lc.add_data(Reference(t, min_col=col, min_row=4, max_row=tlast), titles_from_data=True)
    lc.set_categories(Reference(t, min_col=1, min_row=5, max_row=tlast))
    lc.y_axis.scaling.min, lc.y_axis.scaling.max, lc.y_axis.number_format = 0.5, 1, "0%"
    lc.x_axis.delete = lc.y_axis.delete = False
    style_lines(lc)
    t.add_chart(lc, f"A{tlast + 3}")

    # ---------------------------------------------------------------- materials
    mw = mats
    mw.sheet_view.showGridLines = False
    mw["A1"], mw["A1"].font = "Raw material price trend (USD per kg, quantity-weighted)", F_TITLE
    mw["A2"], mw["A2"].font = "Cost-pressure indicator. Margin is not computed because the data has no cost figures.", F_NOTE
    mat_names = sorted(materials["material"].unique())
    header_row(mw, 4, ["Month"] + mat_names)
    month_list = sorted(materials["month"].unique())
    for i, mo in enumerate(month_list, start=5):
        mw.cell(row=i, column=1, value=mo).font = F_BASE
        for j, name in enumerate(mat_names, start=2):
            c = mw.cell(row=i, column=j, value=f'=SUMIFS({P("avg_price_usd_kg")},{P("month")},$A{i},{P("material")},"{name}")')
            c.number_format, c.font = "0.00", F_BASE
        for col in range(1, len(mat_names) + 2):
            mw.cell(row=i, column=col).border = BORDER
    mlast_row = 4 + len(month_list)
    chg = mlast_row + 1
    mw.cell(row=chg, column=1, value="Change, first to last month").font = F_BOLD
    for j in range(2, len(mat_names) + 2):
        L = mw.cell(row=4, column=j).column_letter
        c = mw.cell(row=chg, column=j, value=f"=IFERROR({L}{mlast_row}/{L}5-1,0)")
        c.number_format, c.font = FMT_PCT, F_BOLD
    mw.column_dimensions["A"].width = 28
    for j in range(2, len(mat_names) + 2):
        mw.column_dimensions[mw.cell(row=4, column=j).column_letter].width = 16
    mc = LineChart()
    mc.title, mc.style, mc.height, mc.width = "Price per kg by material", 12, 9, 20
    for j in range(2, len(mat_names) + 2):
        mc.add_data(Reference(mw, min_col=j, min_row=4, max_row=mlast_row), titles_from_data=True)
    mc.set_categories(Reference(mw, min_col=1, min_row=5, max_row=mlast_row))
    mc.x_axis.delete = mc.y_axis.delete = False
    style_lines(mc)
    mw.add_chart(mc, f"A{chg + 3}")

    # ---------------------------------------------------------------- risk orders
    rw = risk_ws
    # header on row 1 and data from row 2 keeps the links from Summary simple and stable
    header_row(rw, 1, list(risk.columns))
    for i, rec in enumerate(risk.itertuples(index=False), start=2):
        for j, (col, v) in enumerate(zip(risk.columns, rec), start=1):
            if col == "delivery_deadline":
                v = pd.to_datetime(v).to_pydatetime()
            c = rw.cell(row=i, column=j, value=v)
            c.font, c.border = F_BASE, BORDER
            if col == "delivery_deadline":
                c.number_format = FMT_DATE
            elif col == "order_value_usd":
                c.number_format = FMT_USD
    for j, col in enumerate(risk.columns, start=1):
        rw.column_dimensions[rw.cell(row=1, column=j).column_letter].width = max(14, len(col) + 4)
    rw.column_dimensions["B"].width = 24
    lvl = chr(ord("A") + list(risk.columns).index("risk_level"))
    rrng = f"{lvl}2:{lvl}{len(risk) + 1}"
    rw.conditional_formatting.add(rrng, CellIsRule(operator="equal", formula=['"High"'], fill=FILL_RED))
    rw.conditional_formatting.add(rrng, CellIsRule(operator="equal", formula=['"Medium"'], fill=FILL_AMBER))
    rw.conditional_formatting.add(rrng, CellIsRule(operator="equal", formula=['"Low"'], fill=FILL_GREEN))
    rw.freeze_panes = "A2"
    rw.auto_filter.ref = rw.dimensions

    # sheet order and print setup
    wb._sheets = [summary, trend, mats, risk_ws, notes] + [wb[n] for n in wb.sheetnames if n.startswith("Data_")]
    for sheet in (summary, trend, mats):
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT_PATH)
    print(f"Saved {OUT_PATH}")


if __name__ == "__main__":
    main()
