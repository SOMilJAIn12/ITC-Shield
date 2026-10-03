"""Where the data comes from: a built-in SYNTHETIC dataset (sample | realistic), or two user-uploaded files (CSV / XLSX / JSON)."""
import pandas as pd
import sample_data
import realistic_data
import upload_normalize

REQUIRED_PURCHASE = ["supplier_gstin", "invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst"]
REQUIRED_PORTAL = ["supplier_gstin", "invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst"]


DATASETS = ("sample", "realistic")
DATASET_LABELS = {"sample": "Demo Dataset (synthetic / sample data)", "realistic": "Realistic Sample Dataset (synthetic / sample data)"}


def load_sample():
    """The stable, calibrated Phase 1 demo dataset (default)."""
    return sample_data.build_sample()


def load_dataset(name: str = "sample"):
    """'sample' = calibrated demo dataset; 'realistic' = Phase 2 archetype-based dataset."""
    if name == "sample":
        return load_sample()
    if name == "realistic":
        return realistic_data.build_realistic()
    raise ValueError(f"Unknown dataset '{name}'. Choose one of: {', '.join(DATASETS)}")


def load_csvs(purchase_bytes: bytes, portal_bytes: bytes, purchase_name: str | None = None, portal_name: str | None = None):
    """User data: two uploaded files (CSV / XLSX / JSON) validated and normalised into the internal schema (see upload_normalize.py)."""
    return upload_normalize.load_upload((purchase_name, purchase_bytes), (portal_name, portal_bytes))
