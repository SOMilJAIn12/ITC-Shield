"""
Synthetic sample data for ITC Shield (Phase 1).

build_sample() returns (purchase_register_df, portal_df, meta).

 - purchase_register_df : what the company booked / claimed ITC on (books)
 - portal_df            : what suppliers reported on the GST portal (GSTR-2B style)

Mismatches are injected deterministically (fixed seed) so the demo is repeatable.
To switch to a different synthetic dataset in Phase 2, replace build_sample() or
load CSVs through data_source.load_csvs() - the reconciliation code does not care.
"""
import numpy as np
import pandas as pd

SEED = 11
TARGET_EXPECTED_ITC = 2_500_000      # calibrates the synthetic dataset to ~Rs 25L of claimed ITC
N_BASE_INVOICES = 598

BUYER = {"name": "Veda Industrial Components Pvt Ltd", "gstin": "07AABCV4821K1Z5", "state_code": "07"}
PERIOD = {"label": "Jul-Sep 2026", "start": "2026-07-01", "end": "2026-09-30"}

# issue counts injected (non-duplicate + duplicate) -> 147 issues in total
ISSUE_PLAN = {"MISSING_IN_PORTAL": 34, "AMOUNT_MISMATCH": 62, "INVOICE_NO_MISMATCH": 18,
              "GSTIN_MISMATCH": 11, "DUPLICATE_ENTRY": 22}

# name, invoice prefix, state code, category, gst rate, volume weight, size multiplier, issue weight
SUPPLIERS = [
    ("Kalyani Polymers Pvt Ltd", "KP", "27", "Plastic granules", 18, 5.0, 1.0, 2.6),
    ("Sundaram Steel Traders", "SST", "33", "Steel & alloys", 18, 4.5, 1.2, 2.6),
    ("Raghav Logistics & Freight Pvt Ltd", "RLF", "07", "Freight services", 18, 6.0, 0.6, 2.0),
    ("Mehta Packaging Industries", "MPI", "24", "Packaging", 18, 5.0, 0.8, 2.2),
    ("Orion Electricals Pvt Ltd", "OEP", "07", "Electrical components", 18, 3.5, 1.2, 1.5),
    ("Bharat Lubricants & Allied", "BLA", "06", "Lubricants", 18, 3.0, 0.9, 1.2),
    ("Shree Ganesh Fasteners", "SGF", "09", "Fasteners", 18, 4.0, 0.5, 0.8),
    ("Nirmal Chemicals Ltd", "NCL", "24", "Industrial chemicals", 18, 3.0, 1.1, 1.0),
    ("Anand Tool Works", "ATW", "06", "Tooling", 18, 2.5, 1.0, 0.6),
    ("Paramount Office Solutions", "POS", "07", "Office supplies", 18, 2.0, 0.3, 0.5),
    ("Vikram Cargo Movers", "VCM", "09", "Freight services", 18, 3.0, 0.6, 1.2),
    ("Greenfield Industrial Gases", "GIG", "06", "Industrial gases", 18, 2.0, 0.8, 0.4),
    ("Delta Precision Castings", "DPC", "24", "Castings", 18, 3.0, 1.4, 1.0),
    ("Himalaya Rubber Products", "HRP", "09", "Rubber components", 18, 2.5, 0.9, 0.5),
    ("Techno Weld Supplies", "TWS", "07", "Welding consumables", 18, 2.0, 0.6, 0.5),
    ("Surya Bearings Co", "SBC", "27", "Bearings", 18, 2.5, 1.0, 0.7),
    ("Metro Facility Services LLP", "MFS", "07", "Facility services", 18, 2.0, 0.7, 0.8),
    ("Apex Industrial Paints", "AIP", "29", "Coatings", 18, 2.0, 0.8, 0.4),
    ("Jai Hind Hardware Mart", "JHH", "07", "Hardware", 18, 2.0, 0.4, 0.5),
    ("Ritu Print & Labels", "RPL", "07", "Printing & labels", 12, 1.5, 0.4, 0.4),
    ("Cosmos Safety Equipments", "CSE", "06", "Safety equipment", 18, 1.5, 0.5, 0.3),
    ("Venkateshwara Cables Pvt Ltd", "VCP", "36", "Cables & wires", 18, 2.0, 1.1, 0.6),
    ("Unity Electro Controls", "UEC", "07", "Control panels", 18, 1.5, 1.3, 0.4),
    ("Pioneer Pallets & Crates", "PPC", "09", "Pallets", 12, 1.5, 0.4, 0.3),
    ("NorthStar IT Services Pvt Ltd", "NIS", "29", "IT services", 18, 1.0, 1.0, 0.2),
    ("Bansal Industrial Supplies", "BIS", "07", "Industrial supplies", 18, 2.0, 0.5, 0.3),
    ("Kiran Machine Tools Pvt Ltd", "KMT", "27", "Machine tools", 18, 1.0, 2.2, 0.3),
    ("Goel Power & Fuel Agency", "GPF", "07", "Fuel & power", 18, 1.5, 0.6, 0.3),
]
# suppliers whose invoices are preferentially "not found in portal" (late / non-filers)
LATE_FILER_PREFIXES = {"KP", "SST", "RLF"}

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LETTERS = "ABCDEFGHJKLMNPRSTUVW"


def _gstin_checksum(g14: str) -> str:
    total = 0
    for i, ch in enumerate(g14):
        p = _CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += p // 36 + p % 36
    return _CHARS[(36 - total % 36) % 36]


def _make_gstin(rng, state: str) -> str:
    pan = "".join(rng.choice(list(_LETTERS), 3)) + "C" + rng.choice(list("ABCDEFGHKLMNPRST")) \
        + "".join(str(x) for x in rng.integers(0, 10, 4)) + rng.choice(list(_LETTERS))
    g14 = f"{state}{pan}1Z"
    return g14 + _gstin_checksum(g14)


def _taxes(taxable: float, rate: float, intra: bool):
    tax = taxable * rate / 100.0
    if intra:
        return round(tax / 2, 2), round(tax / 2, 2), 0.0
    return 0.0, 0.0, round(tax, 2)


def build_sample(seed: int = SEED):
    rng = np.random.default_rng(seed)

    sup = []
    for name, prefix, state, cat, rate, vol, size, iw in SUPPLIERS:
        sup.append(dict(name=name, prefix=prefix, state=state, category=cat, rate=rate,
                        vol=vol, size=size, iw=iw, gstin=_make_gstin(rng, state)))

    # ---- base invoices -------------------------------------------------------
    vol = np.array([s["vol"] for s in sup])
    sup_idx = rng.choice(len(sup), size=N_BASE_INVOICES, p=vol / vol.sum())
    start = pd.Timestamp(PERIOD["start"])
    base = []
    for si in sup_idx:
        s = sup[si]
        base.append(dict(si=int(si),
                         date=start + pd.Timedelta(days=int(rng.integers(0, 92))),
                         taxable=float(rng.lognormal(9.4, 0.85)) * s["size"],
                         issue=None))
    # invoice numbers: sequential per supplier in date order
    by_sup = {}
    for i, b in enumerate(base):
        by_sup.setdefault(b["si"], []).append(i)
    for si, idxs in by_sup.items():
        idxs.sort(key=lambda i: base[i]["date"])
        n = int(rng.integers(100, 400))
        for i in idxs:
            n += int(rng.integers(1, 9))
            base[i]["invoice_no"] = f"{sup[si]['prefix']}/26-27/{n:04d}"

    # ---- choose which invoices get which issue ---------------------------------
    n_dup = ISSUE_PLAN["DUPLICATE_ENTRY"]
    n_other = sum(ISSUE_PLAN.values()) - n_dup
    w = np.array([sup[b["si"]]["iw"] for b in base], dtype=float)
    chosen = list(rng.choice(len(base), size=n_other, replace=False, p=w / w.sum()))
    rest = [i for i in range(len(base)) if i not in set(chosen)]
    wr = np.array([w[i] for i in rest]); wr = wr / wr.sum()
    dup_src = list(rng.choice(rest, size=n_dup, replace=False, p=wr))

    other_plan = [t for t in ISSUE_PLAN if t != "DUPLICATE_ENTRY"]
    pool = [t for t in other_plan for _ in range(ISSUE_PLAN[t])]
    rng.shuffle(pool)
    # late filers get "missing in portal" first
    late = [i for i in chosen if sup[base[i]["si"]]["prefix"] in LATE_FILER_PREFIXES]
    others = [i for i in chosen if i not in set(late)]
    n_missing = ISSUE_PLAN["MISSING_IN_PORTAL"]
    assigned = {}
    for i in late[:n_missing]:
        assigned[i] = "MISSING_IN_PORTAL"
    remaining_types = list(pool)
    for t in assigned.values():
        remaining_types.remove(t)
    rest_idx = [i for i in chosen if i not in assigned]
    rng.shuffle(rest_idx)
    for i, t in zip(rest_idx, remaining_types):
        assigned[i] = t
    for i, t in assigned.items():
        base[i]["issue"] = t

    # ---- books rows (base + duplicates) ----------------------------------------
    books = []
    for i, b in enumerate(base):
        books.append(dict(b, is_dup=False, src=i))
    for i in dup_src:
        books.append(dict(base[i], is_dup=True, src=int(i), issue="DUPLICATE_ENTRY"))

    # calibrate taxable values so total claimed ITC is ~ TARGET_EXPECTED_ITC
    raw_tax = sum(b["taxable"] * sup[b["si"]]["rate"] / 100 for b in books)
    scale = TARGET_EXPECTED_ITC / raw_tax
    scaled = {}
    for i, b in enumerate(base):
        scaled[i] = float(round(b["taxable"] * scale))
    for b in books:
        b["taxable"] = scaled[b["src"]]

    # ---- build DataFrames ----------------------------------------------------------
    clean_pool = [i for i in range(len(base)) if base[i]["issue"] is None and i not in set(dup_src)]
    rounding_idx = set(rng.choice(clean_pool, size=28, replace=False).tolist())

    books_rows, portal_rows = [], []
    order = sorted(range(len(books)), key=lambda k: (books[k]["date"], books[k]["is_dup"], k))
    for seq, k in enumerate(order, start=1):
        b = books[k]
        s = sup[b["si"]]
        intra = s["state"] == BUYER["state_code"]
        cg, sg, ig = _taxes(b["taxable"], s["rate"], intra)
        gstin_books = s["gstin"]
        inv_no = b["invoice_no"]
        date = b["date"].strftime("%Y-%m-%d")
        issue = b["issue"]

        # portal values start as a faithful copy
        p_gstin, p_inv, p_tv = s["gstin"], inv_no, b["taxable"]
        p_rate = s["rate"]
        p_taxes = None
        include_portal = not b["is_dup"]

        if issue == "MISSING_IN_PORTAL":
            include_portal = False
        elif issue == "AMOUNT_MISMATCH":
            if rng.random() < 0.7:
                p_tv = float(round(b["taxable"] * (1 - rng.uniform(0.12, 0.45))))
            else:
                p_rate = {18: 12, 12: 5}.get(s["rate"], s["rate"])
        elif issue == "INVOICE_NO_MISMATCH":
            r = rng.integers(0, 3)
            if r == 0:
                p_inv = inv_no.replace("26-27", "25-26")
            elif r == 1:
                p_inv = inv_no[:-1] + "A"
            else:
                p_inv = inv_no.replace("0", "O", 1) if "0" in inv_no.split("/")[-1] else inv_no + "A"
        elif issue == "GSTIN_MISMATCH":
            pos = 7
            old = gstin_books[pos]
            new = str((int(old) + int(rng.integers(1, 9))) % 10) if old.isdigit() else "7"
            gstin_books = gstin_books[:pos] + new + gstin_books[pos + 1:]
        elif k < len(base) and k in rounding_idx:
            p_taxes = "round"

        books_rows.append(dict(
            invoice_id=f"PI-{seq:05d}", supplier_name=s["name"], supplier_gstin=gstin_books,
            invoice_no=inv_no, invoice_date=date, taxable_value=b["taxable"],
            cgst=cg, sgst=sg, igst=ig, category=s["category"]))

        if include_portal:
            pc, ps, pi = _taxes(p_tv, p_rate, intra)
            if p_taxes == "round":
                nudge = float(rng.choice([-1.0, -0.5, 0.5, 1.0]))
                if intra:
                    pc, ps = round(pc + nudge, 2), round(ps + nudge, 2)
                else:
                    pi = round(pi + nudge, 2)
            portal_rows.append(dict(
                supplier_name=s["name"], supplier_gstin=p_gstin, invoice_no=p_inv,
                invoice_date=date, taxable_value=p_tv, cgst=pc, sgst=ps, igst=pi))

    books_df = pd.DataFrame(books_rows)
    portal_df = pd.DataFrame(portal_rows)
    meta = {"company": BUYER["name"], "buyer_gstin": BUYER["gstin"], "period": PERIOD["label"],
            "source": "sample"}
    return books_df, portal_df, meta
