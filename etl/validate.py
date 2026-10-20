"""Validation helpers: the data-quality log plus strict parsers.

Every issue found anywhere in the pipeline is recorded in a DQLog. The log is
saved to the dq_issues table and drives the Data Quality Report.

Action text always starts with one of:
    corrected:   value was fixed automatically and the row was kept
    removed:     row was an exact duplicate and was dropped
    quarantined: row could not be fixed and was set aside for the owning team
    dropped:     row was healthy but its parent record was quarantined
    flagged:     row was kept but a human should confirm it
"""
import re
from datetime import date, datetime

import pandas as pd


class DQLog:
    COLUMNS = ["source_file", "table_name", "record_key", "issue_type", "action_taken"]

    def __init__(self):
        self.rows = []

    def add(self, source_file, table, key, issue, action):
        self.rows.append({
            "source_file": source_file,
            "table_name": table,
            "record_key": None if key is None else str(key),
            "issue_type": issue,
            "action_taken": action,
        })

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=self.COLUMNS)


def is_blank(value) -> bool:
    return value is None or bool(pd.isna(value)) or str(value).strip() == ""


def canon_unit(value):
    """Map messy unit labels to 'Zipper' / 'Metal Trims', or None if unknown."""
    if is_blank(value):
        return None
    key = re.sub(r"[\s_]+", "", str(value).strip().lower())
    if key in ("zipper", "zippers"):
        return "Zipper"
    if key in ("metaltrim", "metaltrims"):
        return "Metal Trims"
    return None


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DMY_DASH = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_DMY_SLASH = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
_DMON_YY = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{2}$")


def parse_date(value):
    """Return (date, format_name) or (None, None). Day-first for non-ISO formats."""
    if is_blank(value):
        return None, None
    s = str(value).strip()
    try:
        if _ISO.match(s):
            return date.fromisoformat(s), "iso"
        m = _DMY_DASH.match(s)
        if m:
            d, mo, y = map(int, m.groups())
            return date(y, mo, d), "dd-mm-yyyy"
        m = _DMY_SLASH.match(s)
        if m:
            d, mo, y = map(int, m.groups())
            return date(y, mo, d), "dd/mm/yyyy"
        if _DMON_YY.match(s):
            return datetime.strptime(s, "%d-%b-%y").date(), "dd-Mon-yy"
    except ValueError:
        return None, None
    return None, None


def parse_month_day(value, anchor: date):
    """Parse 'Mar 05' (no year). Pick the year that lands closest to the anchor date."""
    if is_blank(value):
        return None
    try:
        md = datetime.strptime(str(value).strip(), "%b %d")
    except ValueError:
        return None
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(date(year, md.month, md.day))
        except ValueError:
            continue
    return min(candidates, key=lambda d: abs((d - anchor).days)) if candidates else None


def parse_datetime(value):
    """Accepts 'YYYY-MM-DD HH:MM:SS' with or without fractional seconds."""
    if is_blank(value):
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt).replace(microsecond=0)
        except ValueError:
            continue
    return None
