"""Extract: read every raw file exactly as it is (all columns as text).

Reading everything as text is deliberate: it stops pandas from silently
guessing types, so bad values can be detected and reported instead of hidden.
"""
import pandas as pd

from .config import RAW_DIR, SOURCES


# "None" is a legitimate value here (defect_type), so pandas' default NA list is replaced.
NA_VALUES = ["", "NaN", "nan", "NA", "N/A", "null", "NULL"]


def read_source(filename: str) -> pd.DataFrame:
    path = RAW_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Missing raw file: {path}. Run generate_data.py first.")
    if path.suffix == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=NA_VALUES)
    if path.suffix == ".xlsx":
        return pd.read_excel(path, dtype=str, keep_default_na=False, na_values=NA_VALUES)
    raise ValueError(f"Unsupported file type: {path.suffix}")


def read_all() -> dict:
    return {name: read_source(fname) for name, fname in SOURCES.items()}
