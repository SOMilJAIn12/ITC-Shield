"""
Phase 2 realistic synthetic dataset (fixed seed). An ADDITIONAL dataset: the Phase 1 demo sample
(sample_data.py) is untouched and remains the default.

build_bundle() returns a dict:
    books     purchase register (DataFrame)
    portal    GSTR-2B style records (DataFrame)
    payments  payment records for books invoices (DataFrame, informational)
    truth     ground-truth label per books row: case, expected recon status, expected exposure
    suppliers supplier profile (archetype) per GSTIN
    meta      company / period / source
build_realistic() returns (books, portal, meta) - the same shape data_source expects.

Supplier behaviour is generated from archetypes, not random corruption:
    reliable       mostly clean; rare issues
    inconsistent   steady, repeated mismatches of mixed types
    high_value     few invoices, very large tax; modest issue rate -> large exposure
    deteriorating  issue rate climbs month by month
    late_filer     reports late: recent months are often missing on the portal (delayed reporting)
Expected statuses are derived here from what was written to the two files, independently of reconcile.py.
"""
import re

import numpy as np
import pandas as pd

from sample_data import BUYER, _make_gstin, _taxes

SEED = 2026
SIZE_SCALE = 0.4      # keeps invoice sizes in the same range as the Phase 1 demo
MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
PERIOD = {"label": "Apr-Sep 2026"}
AS_OF = pd.Timestamp("2026-09-30")

# name, prefix, state, category, gst rate, archetype, invoices/month, typical taxable value, invoice-number style
SUPPLIERS = [
    ("Alpine Industrial Fasteners Pvt Ltd", "AIF", "07", "Fasteners", 18, "reliable", 4, 90_000, "slash"),
    ("Brightline Packaging Co", "BPC", "09", "Packaging", 18, "reliable", 5, 70_000, "dash"),
    ("Cedar Lubricants Pvt Ltd", "CLP", "06", "Lubricants", 18, "reliable", 4, 110_000, "slash"),
    ("Dhruv Cable Works", "DCW", "24", "Cables & wires", 18, "reliable", 3, 140_000, "plain"),
    ("Eastern Freight Carriers LLP", "EFC", "07", "Freight services", 18, "reliable", 6, 45_000, "dash"),
    ("Falcon Tooling Pvt Ltd", "FTP", "27", "Tooling", 18, "reliable", 3, 120_000, "slash"),
    ("Gemini Office Supplies", "GOS", "07", "Office supplies", 18, "reliable", 4, 30_000, "plain"),
    ("Harbor Chemicals Ltd", "HCL", "24", "Industrial chemicals", 18, "inconsistent", 5, 100_000, "slash"),
    ("Indus Rubber Components", "IRC", "09", "Rubber components", 18, "inconsistent", 5, 80_000, "dash"),
    ("Jupiter Welding Supplies", "JWS", "07", "Welding consumables", 18, "inconsistent", 4, 60_000, "plain"),
    ("Kestrel Metal Works Pvt Ltd", "KMW", "29", "Steel & alloys", 18, "inconsistent", 5, 130_000, "slash"),
    ("Lotus Heavy Machinery Pvt Ltd", "LHM", "27", "Machine tools", 18, "high_value", 3, 900_000, "slash"),
    ("Meridian Power Systems Ltd", "MPS", "33", "Electrical equipment", 18, "high_value", 2, 1_200_000, "dash"),
    ("Nova Industrial Castings", "NIC", "24", "Castings", 18, "high_value", 3, 700_000, "plain"),
    ("Orbit Logistics Pvt Ltd", "OLP", "07", "Freight services", 18, "deteriorating", 6, 55_000, "dash"),
    ("Pinnacle Polymers Pvt Ltd", "PPL", "27", "Plastic granules", 18, "deteriorating", 5, 150_000, "slash"),
    ("Quest Components Co", "QCC", "06", "Components", 18, "deteriorating", 4, 95_000, "plain"),
    ("Radiant Paints & Coatings", "RPC", "29", "Coatings", 18, "late_filer", 4, 85_000, "slash"),
    ("Sterling Facility Services LLP", "SFS", "07", "Facility services", 18, "late_filer", 4, 65_000, "dash"),
    ("Titan Pallets & Crates", "TPC", "09", "Pallets", 12, "late_filer", 3, 50_000, "plain"),
]

# probability that an invoice has a problem, by archetype and month index 0..5
ISSUE_RATE = {
    "reliable": [0.03] * 6,
    "inconsistent": [0.26, 0.30, 0.25, 0.28, 0.27, 0.30],
    "high_value": [0.10, 0.12, 0.10, 0.14, 0.12, 0.13],
    "deteriorating": [0.02, 0.05, 0.12, 0.24, 0.38, 0.52],
    "late_filer": [0.05, 0.05, 0.06, 0.06, 0.06, 0.06],
}
# mix of problem types by archetype
ISSUE_MIX = {
    "reliable": {"TAX": 0.3, "AMOUNT": 0.3, "MISSING": 0.2, "FUZZY": 0.2},
    "inconsistent": {"TAX": 0.28, "AMOUNT": 0.24, "MISSING": 0.10, "DUPLICATE": 0.14, "GSTIN_TYPO": 0.12, "FUZZY": 0.12},
    "high_value": {"TAX": 0.4, "AMOUNT": 0.3, "MISSING": 0.3},
    "deteriorating": {"MISSING": 0.3, "AMOUNT": 0.28, "TAX": 0.2, "DUPLICATE": 0.1, "FUZZY": 0.12},
    "late_filer": {"MISSING": 0.5, "TAX": 0.3, "AMOUNT": 0.2},
}
# share of clean invoices that carry a harmless variation (still MATCHED)
P_FORMAT_VARIATION, P_NAME_VARIATION, P_ROUNDING = 0.10, 0.18, 0.08
# late filers: chance that an invoice in these months simply is not reported yet
DELAYED_MONTHS = {4: 0.55, 5: 0.75}

EXPECTED_STATUS = {
    "EXACT": "MATCHED", "FORMAT_VARIATION": "MATCHED", "NAME_VARIATION": "MATCHED", "ROUNDING": "MATCHED",
    "TAX_MISMATCH": "TAX_MISMATCH", "AMOUNT_MISMATCH": "AMOUNT_MISMATCH", "MISSING": "MISSING",
    "DELAYED_REPORTING": "MISSING", "DUPLICATE": "DUPLICATE", "GSTIN_TYPO": "REVIEW", "FUZZY_INVOICE": "REVIEW",
}


def _inv_no(sup, n: int, style: str, year: str = "26-27") -> str:
    p = sup["prefix"]
    return {"slash": f"{p}/{year}/{n:04d}", "dash": f"{p}-{n:05d}", "plain": f"{p}{n:06d}"}[style]


def _name_variant(name: str, rng) -> str:
    opts = [name, name.upper(), name.replace("Pvt Ltd", "Private Limited").replace("Ltd", "Limited"),
            name.replace("Pvt Ltd", "Pvt. Ltd."), name.replace(" & ", " and ")]
    v = opts[int(rng.integers(0, len(opts)))]
    return v if v != name else name.upper()


def _format_variant(inv: str, rng) -> str:
    """Same invoice, keyed differently in the books (case / separators / leading zeros)."""
    head = inv.replace("/", "-") if "/" in inv else inv
    opts = [inv.lower(), head, inv.replace("-", " ").replace("/", " "),
            re.sub(r"(?<!\d)0+(?=[1-9])", "", inv)]    # leading zeros dropped from number runs
    out = opts[int(rng.integers(0, len(opts)))]
    return out if out != inv else inv.lower()


def _pick(rng, mix: dict) -> str:
    keys = list(mix)
    return keys[int(rng.choice(len(keys), p=np.array(list(mix.values())) / sum(mix.values())))]


def _transpose(inv: str, rng) -> str:
    """Typo a digit pair near the end of the number (a plausible keying error)."""
    chars = list(inv)
    idx = [i for i, ch in enumerate(chars) if ch.isdigit()][-3:]
    i = idx[int(rng.integers(0, len(idx) - 1))]
    chars[i], chars[i + 1] = chars[i + 1], chars[i]
    out = "".join(chars)
    return out if out != inv else inv[:-1] + ("7" if inv[-1] != "7" else "3")


def build_bundle(seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    sups = []
    for name, prefix, state, cat, rate, arch, per_m, size, style in SUPPLIERS:
        sups.append(dict(name=name, prefix=prefix, state=state, category=cat, rate=rate, archetype=arch,
                         per_month=per_m, size=size, style=style, gstin=_make_gstin(rng, state)))

    # ---- one record per genuine invoice, with its intended case ---------------------
    inv = []
    for si, s in enumerate(sups):
        counter = int(rng.integers(100, 600))
        for mi, month in enumerate(MONTHS):
            n = max(1, s["per_month"] + int(rng.integers(-1, 2)))
            days = sorted(int(d) for d in rng.choice(28, size=n, replace=n > 28)) if n <= 28 else list(range(28))
            for day in days:
                counter += int(rng.integers(1, 6))
                taxable = float(round(max(10_000.0, rng.lognormal(np.log(s["size"] * SIZE_SCALE), 0.45))))
                date = pd.Timestamp(f"{month}-01") + pd.Timedelta(days=day)
                case = "EXACT"
                if s["archetype"] == "late_filer" and mi in DELAYED_MONTHS and rng.random() < DELAYED_MONTHS[mi]:
                    case = "DELAYED_REPORTING"
                elif rng.random() < ISSUE_RATE[s["archetype"]][mi]:
                    case = {"TAX": "TAX_MISMATCH", "AMOUNT": "AMOUNT_MISMATCH", "MISSING": "MISSING",
                            "DUPLICATE": "DUPLICATE", "GSTIN_TYPO": "GSTIN_TYPO",
                            "FUZZY": "FUZZY_INVOICE"}[_pick(rng, ISSUE_MIX[s["archetype"]])]
                else:
                    r = rng.random()
                    if r < P_FORMAT_VARIATION:
                        case = "FORMAT_VARIATION"
                    elif r < P_FORMAT_VARIATION + P_NAME_VARIATION:
                        case = "NAME_VARIATION"
                    elif r < P_FORMAT_VARIATION + P_NAME_VARIATION + P_ROUNDING:
                        case = "ROUNDING"
                inv.append(dict(si=si, date=date, taxable=taxable, counter=counter, case=case))

    # a duplicate is an extra books row copied from an otherwise clean invoice
    for it in list(inv):
        if it["case"] == "DUPLICATE":
            it["case"] = "EXACT"
            inv.append(dict(it, case="DUPLICATE", is_dup=True))
    for it in inv:
        it.setdefault("is_dup", False)
    inv.sort(key=lambda it: (it["date"], it["si"], it["counter"], it["is_dup"]))

    books_rows, portal_rows, truth_rows, pay_rows = [], [], [], []
    for seq, it in enumerate(inv, start=1):
        s = sups[it["si"]]
        intra = s["state"] == BUYER["state_code"]
        case, taxable = it["case"], it["taxable"]
        inv_no = _inv_no(s, it["counter"], s["style"])
        date = it["date"]
        iid = f"PI-{seq:05d}"
        cg, sg, ig = _taxes(taxable, s["rate"], intra)
        books_tax = round(cg + sg + ig, 2)

        b_inv, b_name, b_gstin, b_date = inv_no, s["name"], s["gstin"], date
        p_inv, p_gstin, p_date, p_tv, p_rate = inv_no, s["gstin"], date, taxable, s["rate"]
        in_portal, nudge = not it["is_dup"], 0.0

        if case == "FORMAT_VARIATION":
            b_inv = _format_variant(inv_no, rng)
        elif case == "NAME_VARIATION":
            b_name = _name_variant(s["name"], rng)
        elif case == "ROUNDING":
            nudge = float(rng.choice([-1.0, -0.5, 0.5, 1.0]))
        elif case == "TAX_MISMATCH":
            p_rate = {18: 12, 12: 5}[s["rate"]]
        elif case == "AMOUNT_MISMATCH":
            p_tv = float(round(taxable * (1 - rng.uniform(0.10, 0.40))))
        elif case in ("MISSING", "DELAYED_REPORTING"):
            in_portal = False
        elif case == "GSTIN_TYPO":
            pos = 7
            old = b_gstin[pos]
            new = str((int(old) + int(rng.integers(1, 9))) % 10) if old.isdigit() else "7"
            b_gstin = b_gstin[:pos] + new + b_gstin[pos + 1:]
            b_name = _name_variant(s["name"], rng)
        elif case == "FUZZY_INVOICE":
            p_inv = _transpose(inv_no, rng)                       # portal number differs by a keying slip
            p_date = date + pd.Timedelta(days=int(rng.integers(1, 7)))
            if rng.random() < 0.6:
                p_tv = float(round(taxable * (1 + rng.uniform(-0.08, 0.08))))
        elif case == "DUPLICATE" and rng.random() < 0.4:
            b_inv = _format_variant(inv_no, rng)                  # duplicate keyed with different formatting

        books_rows.append(dict(
            invoice_id=iid, supplier_name=b_name, supplier_gstin=b_gstin, invoice_no=b_inv,
            invoice_date=b_date.strftime("%Y-%m-%d"), taxable_value=taxable, cgst=cg, sgst=sg, igst=ig,
            category=s["category"]))

        portal_tax = None
        if in_portal:
            pc, ps, pi = _taxes(p_tv, p_rate, intra)
            if nudge:
                if intra:
                    pc, ps = round(pc + nudge, 2), round(ps + nudge, 2)
                else:
                    pi = round(pi + nudge, 2)
            portal_tax = round(pc + ps + pi, 2)
            portal_rows.append(dict(
                supplier_name=s["name"], supplier_gstin=p_gstin, invoice_no=p_inv,
                invoice_date=p_date.strftime("%Y-%m-%d"), taxable_value=p_tv, cgst=pc, sgst=ps, igst=pi))

        # expected exposure, derived from what was written (independent of reconcile.py)
        if case in ("MISSING", "DELAYED_REPORTING", "DUPLICATE", "GSTIN_TYPO", "FUZZY_INVOICE"):
            exp = books_tax
        elif case in ("TAX_MISMATCH", "AMOUNT_MISMATCH"):
            exp = round(max(books_tax - portal_tax, 0.0), 2)
        else:
            exp = 0.0
        truth_rows.append(dict(invoice_id=iid, supplier_gstin=s["gstin"], archetype=s["archetype"], case=case,
                               expected_status=EXPECTED_STATUS[case], expected_exposure=exp, itc_claimed=books_tax,
                               month=date.strftime("%Y-%m")))

        # payments (informational): most invoices are paid 10-75 days after invoice date
        if not it["is_dup"]:
            lag = int(rng.integers(10, 76)) + (40 if s["archetype"] in ("deteriorating", "late_filer") and rng.random() < 0.35 else 0)
            pay_date = date + pd.Timedelta(days=lag)
            if pay_date <= AS_OF:
                total = round(taxable + books_tax, 2)
                part = rng.random() < 0.06
                pay_rows.append(dict(invoice_id=iid, supplier_gstin=s["gstin"], payment_date=pay_date.strftime("%Y-%m-%d"),
                                     invoice_total=total, amount_paid=round(total * 0.5, 2) if part else total))

    books = pd.DataFrame(books_rows)
    portal = pd.DataFrame(portal_rows)
    meta = {"company": BUYER["name"], "buyer_gstin": BUYER["gstin"], "period": PERIOD["label"], "source": "realistic"}
    profile = pd.DataFrame([dict(supplier_gstin=s["gstin"], supplier_name=s["name"], archetype=s["archetype"])
                            for s in sups])
    return {"books": books, "portal": portal, "payments": pd.DataFrame(pay_rows),
            "truth": pd.DataFrame(truth_rows), "suppliers": profile, "meta": meta}


def build_realistic(seed: int = SEED):
    b = build_bundle(seed)
    return b["books"], b["portal"], b["meta"]
