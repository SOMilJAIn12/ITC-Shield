"""
User-data upload: validation + normalisation into the EXISTING internal schema.

Accepted files : CSV, XLSX (first sheet), JSON (array of records, {"data": [...]}, or GSTR-2B-style b2b/inv/items nesting).
Internal schema: supplier_gstin, invoice_no, invoice_date (YYYY-MM-DD), taxable_value, cgst, sgst, igst  (+ optional supplier_name, category)
The reconciliation / exposure / risk / priority engines are not touched: they receive exactly the same frame shape as the built-in datasets.

Rules
- Column headers are matched case-/punctuation-insensitively against a documented alias table (ALIASES).
- Two source columns mapping to the same field is an error (never guessed). A field with no match is reported with the accepted names.
- Tax may be given as IGST/CGST/SGST columns or as a single Total GST column (then stored as IGST with a note; the engine compares totals).
- Numbers may contain ₹, commas and spaces. Blank tax cells are 0; a blank taxable value is an error.
- Dates: ISO, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY, Excel dates. Unparseable dates are an error (first offenders listed).
"""
from __future__ import annotations

import io
import json
import re

import pandas as pd

REQUIRED = ["supplier_gstin", "invoice_no", "invoice_date", "taxable_value"]
TAX_FIELDS = ["igst", "cgst", "sgst"]
OPTIONAL = ["supplier_name", "category", "total_gst", "invoice_id"]
INTERNAL_ORDER = ["supplier_name", "supplier_gstin", "invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst", "category"]

# internal field -> accepted header spellings (compared after lower-casing and stripping everything but letters/digits)
ALIASES = {
    "supplier_gstin": ["supplier_gstin", "gstin", "supplier gstin", "gstin of supplier", "vendor gstin", "seller gstin", "party gstin",
                       "supplier gst no", "supplier gstin no", "gst no", "gst number", "gstin number", "ctin", "supplier gstin/uin", "gstin/uin of supplier"],
    "supplier_name": ["supplier_name", "supplier name", "vendor name", "supplier", "vendor", "trade name", "legal name", "party name",
                      "name of supplier", "trade/legal name", "trdnm", "seller name", "name"],
    "invoice_no": ["invoice_no", "invoice no", "invoice number", "inv no", "inv num", "invoice num", "inum", "bill no", "bill number",
                   "document no", "document number", "doc no", "invoice", "invoice #", "inv #", "voucher no", "invoice no."],
    "invoice_date": ["invoice_date", "invoice date", "date", "inv date", "idt", "bill date", "document date", "doc date", "invoice dt", "dt"],
    "taxable_value": ["taxable_value", "taxable value", "taxable amount", "taxable amt", "txval", "basic amount", "basic value",
                      "assessable value", "taxable", "amount before tax", "net amount", "value before tax", "taxable value (rs)"],
    "igst": ["igst", "igst amount", "igst amt", "iamt", "integrated tax", "integrated tax amount", "igst (rs)"],
    "cgst": ["cgst", "cgst amount", "cgst amt", "camt", "central tax", "central tax amount", "cgst (rs)"],
    "sgst": ["sgst", "sgst amount", "sgst amt", "samt", "state tax", "state/ut tax", "state tax amount", "utgst", "sgst/utgst", "sgst utgst", "sgst (rs)"],
    "total_gst": ["total_gst", "total gst", "total tax", "gst amount", "gst", "tax amount", "total tax amount", "tax", "total gst amount", "gst total"],
    "category": ["category", "item category", "expense category", "nature of supply", "description", "item description"],
    "invoice_id": ["invoice_id", "invoice id", "record id", "row id"],
}
_KEY_RE = re.compile(r"[^a-z0-9]+")


def _key(s) -> str:
    return _KEY_RE.sub("", str(s).strip().lower())


_ALIAS_LOOKUP = {_key(a): field for field, names in ALIASES.items() for a in names}
_INTERNAL = set(ALIASES)

MONEY_RE = re.compile(r"[₹$,\s]|rs\.?|inr", re.I)


# ----------------------------------------------------------------------------------------------------------------- reading
def sniff_format(filename: str | None, data: bytes) -> str:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        return "xlsx"
    if name.endswith(".xls"):
        return "xls"
    if name.endswith(".json"):
        return "json"
    head = data.lstrip()[:1]
    if head in (b"[", b"{") and not name.endswith(".csv"):
        return "json"
    return "csv"


def _flatten_json(obj) -> list[dict]:
    """Array of records; {"data": [...]} / first list value; or GSTR-2B b2b: [{ctin, trdnm, inv: [{inum, idt, items: [...]}]}]."""
    if isinstance(obj, dict):
        # GSTR-2B style: data.docdata.b2b or any nested key "b2b"
        def find_key(o, key):
            if isinstance(o, dict):
                if key in o:
                    return o[key]
                for v in o.values():
                    r = find_key(v, key)
                    if r is not None:
                        return r
            return None
        b2b = find_key(obj, "b2b")
        if isinstance(b2b, list) and b2b and isinstance(b2b[0], dict) and "inv" in b2b[0]:
            obj = b2b
        else:
            lists = [v for v in obj.values() if isinstance(v, list)]
            if not lists:
                raise ValueError("The JSON object does not contain a list of invoices.")
            obj = lists[0]
    if not isinstance(obj, list):
        raise ValueError("The JSON must be a list of invoice records.")
    rows = []
    for rec in obj:
        if not isinstance(rec, dict):
            raise ValueError("Each JSON record must be an object with named fields.")
        if "inv" in rec and isinstance(rec["inv"], list):          # supplier-level wrapper with nested invoices
            base = {k: v for k, v in rec.items() if k != "inv"}
            for inv in rec["inv"]:
                row = {**base, **{k: v for k, v in inv.items() if k != "items"}}
                items = inv.get("items")
                if isinstance(items, list) and items:
                    for f in ("txval", "igst", "cgst", "sgst"):
                        row[f] = sum(float(it.get(f) or 0) for it in items)
                rows.append(row)
        else:
            rows.append(rec)
    return rows


def read_table(label: str, filename: str | None, data: bytes) -> tuple[pd.DataFrame, str]:
    """Reads an uploaded file into a raw DataFrame (original headers). Raises ValueError with a user-facing message."""
    if not data or not data.strip():
        raise ValueError(f"The {label} is empty. Upload a file with a header row and at least one invoice.")
    fmt = sniff_format(filename, data)
    try:
        if fmt == "xlsx":
            df = pd.read_excel(io.BytesIO(data), sheet_name=0, engine="openpyxl")
        elif fmt == "xls":
            raise ValueError(f"The {label} is an old .xls workbook. Save it as .xlsx or .csv and upload again.")
        elif fmt == "json":
            try:
                obj = json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ValueError(f"The {label} is not valid JSON ({e.__class__.__name__}).")
            df = pd.DataFrame(_flatten_json(obj))
        else:
            try:
                df = pd.read_csv(io.BytesIO(data), encoding="utf-8-sig", skip_blank_lines=True, dtype=str)
            except UnicodeDecodeError:
                raise ValueError(f"The {label} is not UTF-8 text. Export it as CSV (UTF-8) or XLSX and try again.")
            except pd.errors.EmptyDataError:
                raise ValueError(f"The {label} has no data. Upload a file with a header row and at least one invoice.")
    except ValueError:
        raise
    except Exception:
        raise ValueError(f"The {label} could not be read as {fmt.upper()}. Export it as CSV, XLSX or JSON with a header row and try again.")
    df = df.dropna(how="all")
    if df.shape[1] < 2 and fmt == "csv":
        raise ValueError(f"The {label} does not look like a CSV (only one column found). Expected comma-separated columns such as "
                         "Supplier GSTIN, Invoice No, Invoice Date, Taxable Value, IGST, CGST, SGST.")
    if df.shape[1] == 0 or len(df) == 0:
        raise ValueError(f"The {label} has a header row but no invoices.")
    return df, fmt


# ------------------------------------------------------------------------------------------------------------ normalising
def map_columns(columns) -> tuple[dict, list[str]]:
    """Source header -> internal field. Raises on ambiguity. Returns (mapping, unmapped headers)."""
    mapping, by_field, unmapped = {}, {}, []
    for c in columns:
        k = _key(c)
        field = _ALIAS_LOOKUP.get(k) or (k if k in _INTERNAL else None)
        if field is None:
            unmapped.append(str(c))
            continue
        if field in by_field and by_field[field] != c:
            raise ValueError(f"Ambiguous columns: both '{by_field[field]}' and '{c}' would be used as {field}. Keep one of them.")
        by_field[field] = c
        mapping[str(c)] = field
    return mapping, unmapped


def _accepted(field: str) -> str:
    return ", ".join(f"'{a}'" for a in ALIASES[field][:5])


def _text(series: pd.Series) -> pd.Series:
    """Cell text with NA / 'nan' / 'None' as empty strings (pandas string dtypes keep NA through astype(str))."""
    return series.fillna("").astype(str).str.strip().replace({"nan": "", "None": "", "NaN": "", "<NA>": ""})


def _to_number(series: pd.Series, allow_blank: bool) -> tuple[pd.Series, int]:
    s = _text(series)
    blank = s.isin(["", "nan", "None", "NaN", "-", "–"])
    cleaned = s.where(~blank, "0" if allow_blank else "")
    cleaned = cleaned.map(lambda v: MONEY_RE.sub("", v).replace("(", "-").replace(")", ""))
    num = pd.to_numeric(cleaned, errors="coerce")
    bad = int(num.isna().sum())
    return num, bad


def _to_date(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Returns (YYYY-MM-DD strings, mask of unparseable). Day-first for slashed/dashed numeric dates; ISO unaffected."""
    if pd.api.types.is_datetime64_any_dtype(series):
        dt = pd.to_datetime(series, errors="coerce")
    else:
        s = _text(series)
        iso = s.str.match(r"^\d{4}-\d{2}-\d{2}")
        dt = pd.to_datetime(s.where(iso), errors="coerce", format="%Y-%m-%d")
        rest = s.where(~iso)
        parsed_rest = pd.to_datetime(rest, errors="coerce", dayfirst=True, format="mixed")
        dt = dt.fillna(parsed_rest)
        # Excel serials that arrived as text
        serial = pd.to_numeric(s.where(dt.isna()), errors="coerce")
        dt = dt.fillna(pd.to_datetime(serial, unit="D", origin="1899-12-30", errors="coerce"))
    bad = dt.isna()
    return dt.dt.strftime("%Y-%m-%d"), bad


def normalize_table(label: str, raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Raw uploaded frame -> internal schema. Returns (frame, info{mapping, unmapped, notes})."""
    mapping, unmapped = map_columns(raw.columns)
    present = set(mapping.values())
    missing = [f for f in REQUIRED if f not in present]
    if missing:
        raise ValueError(f"The {label} is missing columns: " + "; ".join(f"{f} (accepted names: {_accepted(f)})" for f in missing)
                         + (f". Columns found: {', '.join(str(c) for c in raw.columns)}." if len(raw.columns) <= 20 else "."))
    has_components = any(f in present for f in TAX_FIELDS)
    if not has_components and "total_gst" not in present:
        raise ValueError(f"The {label} has no tax columns. Provide IGST / CGST / SGST columns or a single Total GST column.")

    df = pd.DataFrame(index=raw.index)
    notes: list[str] = []
    inv = {v: k for k, v in mapping.items()}        # internal -> source header

    df["supplier_gstin"] = _text(raw[inv["supplier_gstin"]]).str.upper()
    df["invoice_no"] = _text(raw[inv["invoice_no"]])
    if "supplier_name" in inv:
        df["supplier_name"] = _text(raw[inv["supplier_name"]])
    if "category" in inv:
        df["category"] = _text(raw[inv["category"]])

    # drop rows with no GSTIN and no invoice number (trailing totals, blank lines)
    empty = (df["supplier_gstin"] == "") & (df["invoice_no"] == "")
    if empty.any():
        notes.append(f"{int(empty.sum())} blank row(s) ignored.")
        df, raw = df[~empty], raw[~empty]
    if len(df) == 0:
        raise ValueError(f"The {label} has no invoice rows after removing blank lines.")
    if (df["supplier_gstin"] == "").any():
        n = int((df["supplier_gstin"] == "").sum())
        raise ValueError(f"The {label} has {n} row(s) with a blank {inv['supplier_gstin']}. Every invoice needs the supplier GSTIN.")
    if (df["invoice_no"] == "").any():
        n = int((df["invoice_no"] == "").sum())
        raise ValueError(f"The {label} has {n} row(s) with a blank {inv['invoice_no']}.")

    dates, bad_dates = _to_date(raw[inv["invoice_date"]])
    if bad_dates.any():
        ex = raw.loc[bad_dates, inv["invoice_date"]].astype(str).head(3).tolist()
        raise ValueError(f"The {label} has {int(bad_dates.sum())} unreadable date(s) in '{inv['invoice_date']}' (e.g. {', '.join(ex)}). "
                         "Use YYYY-MM-DD or DD/MM/YYYY.")
    df["invoice_date"] = dates

    tv, bad = _to_number(raw[inv["taxable_value"]], allow_blank=False)
    if bad:
        ex = raw.loc[tv.isna(), inv["taxable_value"]].astype(str).head(3).tolist()
        raise ValueError(f"The {label} has {bad} non-numeric or blank taxable value(s) in '{inv['taxable_value']}' (e.g. {', '.join(ex)}).")
    df["taxable_value"] = tv.round(2)

    for f in TAX_FIELDS:
        if f in inv:
            num, bad = _to_number(raw[inv[f]], allow_blank=True)
            if bad:
                ex = raw.loc[num.isna(), inv[f]].astype(str).head(3).tolist()
                raise ValueError(f"The {label} has {bad} non-numeric value(s) in '{inv[f]}' (e.g. {', '.join(ex)}).")
            df[f] = num.round(2)
        else:
            df[f] = 0.0
    if "total_gst" in inv:
        total, bad = _to_number(raw[inv["total_gst"]], allow_blank=True)
        if bad:
            raise ValueError(f"The {label} has {bad} non-numeric value(s) in '{inv['total_gst']}'.")
        if not has_components:
            df["igst"] = total.round(2)
            notes.append(f"Tax was given only as '{inv['total_gst']}'; it is used as the total GST (shown under IGST).")
        else:
            comp = (df["igst"] + df["cgst"] + df["sgst"]).round(2)
            diff = (comp - total.round(2)).abs() > 1.0
            if diff.any():
                notes.append(f"IGST+CGST+SGST differs from '{inv['total_gst']}' on {int(diff.sum())} row(s); the component columns are used.")
    if not has_components and "total_gst" not in inv:          # unreachable (checked above), kept for clarity
        raise ValueError("No tax columns.")
    if (df["igst"] + df["cgst"] + df["sgst"]).sum() == 0:
        notes.append("All tax values are zero in this file.")
    if unmapped:
        notes.append("Ignored columns: " + ", ".join(unmapped[:8]) + (" …" if len(unmapped) > 8 else "") + ".")

    cols = [c for c in INTERNAL_ORDER if c in df.columns]
    out = df[cols].reset_index(drop=True)
    return out, {"mapping": mapping, "unmapped": unmapped, "notes": notes, "rows": int(len(out))}


def load_upload(purchase: tuple[str | None, bytes], portal: tuple[str | None, bytes]):
    """(filename, bytes) x2 -> (books, portal, meta) in the internal schema. Raises ValueError with user-facing text."""
    (p_name, p_bytes), (g_name, g_bytes) = purchase, portal
    raw_b, fmt_b = read_table("purchase register", p_name, p_bytes)
    raw_p, fmt_p = read_table("portal file", g_name, g_bytes)
    books, info_b = normalize_table("purchase register", raw_b)
    portal_df, info_p = normalize_table("portal file", raw_p)
    dates = pd.concat([books["invoice_date"], portal_df["invoice_date"]])
    first, last = dates.min(), dates.max()
    period = f"{pd.Timestamp(first).strftime('%b %Y')} - {pd.Timestamp(last).strftime('%b %Y')}" if first != last else pd.Timestamp(first).strftime("%b %Y")
    if first[:7] == last[:7]:
        period = pd.Timestamp(first).strftime("%b %Y")
    meta = {
        "company": "Your uploaded data", "buyer_gstin": "", "period": period, "source": "upload",
        "upload": {
            "purchase": {"filename": p_name, "format": fmt_b, "rows": info_b["rows"], "mapping": info_b["mapping"], "notes": info_b["notes"]},
            "portal": {"filename": g_name, "format": fmt_p, "rows": info_p["rows"], "mapping": info_p["mapping"], "notes": info_p["notes"]},
        },
    }
    return books, portal_df, meta
