# demo_upload_data — SYNTHETIC TEST DATA for the upload workflow

**Everything in this folder is made up.** The suppliers, GSTINs, invoice numbers and amounts are fictional and exist only to
exercise ITC Shield's *User data* upload. They are not real GST records and were not obtained from the GST portal.

## Files

| File | What it represents | Rows |
|---|---|---|
| `sample_purchase_register.csv` | The buyer's purchase register (what was booked / ITC claimed) | 24 (23 invoices, one keyed twice) |
| `sample_portal_data.csv` | Portal / GSTR-2B-style data (what suppliers reported) | 22 |

Buyer is assumed to be in Delhi (state code 07): Delhi suppliers charge CGST + SGST, the others IGST. All rates are 18 %.
Period: July – September 2026. Five suppliers: Arvind Steel Industries, Prakash Packaging Pvt Ltd, Sunrise Logistics Services,
Neha Electricals & Cables, Om Chemicals Ltd.

## Columns

The two files deliberately use *different* header spellings to show the column mapping at work. Both are accepted as-is.

| Purchase register header | Portal header | Internal field | Required |
|---|---|---|---|
| Supplier Name | Supplier Name | `supplier_name` | optional |
| Supplier GSTIN | GSTIN | `supplier_gstin` | **yes** |
| Invoice No | Invoice Number | `invoice_no` | **yes** |
| Invoice Date | Invoice Date | `invoice_date` (YYYY-MM-DD, DD/MM/YYYY, DD-Mon-YYYY, Excel dates) | **yes** |
| Taxable Value | Taxable Value | `taxable_value` | **yes** |
| CGST / SGST / IGST | IGST / CGST / SGST | `cgst` / `sgst` / `igst` (blank = 0) | at least one, **or** a single *Total GST* column |
| Category | – | `category` | optional |

Other accepted spellings include *Vendor GSTIN, GSTIN of Supplier, CTIN, Vendor Name, Trade Name, Invoice Number, Inv No, Bill No,
Date, Inv Date, Taxable Amount, Basic Amount, IGST Amount, Central Tax, State Tax, Total Tax, GST Amount* (full list: `GET /api/upload/schema`).
The same files also work as **XLSX** (first sheet) and **JSON** (array of objects; GSTR-2B-style `b2b → inv → items` nesting is flattened).

## Intentional cases (6 issues out of 24 rows; the other 18 match)

| # | Invoice | Supplier | What differs between the two files | Expected result | Potential ITC Exposure |
|---|---|---|---|---|---|
| 1 | ASI/26-27/0112 | Arvind Steel | Taxable value equal (₹2,40,000); portal IGST ₹40,320 vs booked ₹43,200 | **Tax mismatch** | ₹2,880 |
| 2 | PPL/26-27/0345 | Prakash Packaging | Taxable value equal (₹85,000); portal CGST+SGST ₹13,600 vs booked ₹15,300 | **Tax mismatch** | ₹1,700 |
| 3 | SLS/26-27/0781 | Sunrise Logistics | Portal taxable value ₹1,02,000 vs booked ₹1,20,000 (tax ₹18,360 vs ₹21,600) | **Amount mismatch** | ₹3,240 |
| 4 | NEC/26-27/0456 | Neha Electricals | Booked (₹68,500 + IGST ₹12,330) but absent from the portal file | **Missing in portal** | ₹12,330 |
| 5 | OCL/26-27/0223 | Om Chemicals | Keyed **twice** in the purchase register; the portal has it once | **Duplicate entry** (second row) | ₹9,720 |
| 6 | ASI/26-27/0130 | Arvind Steel | Same GSTIN, date and value, but the portal shows invoice no. **ASI/26-27/0131** | **Invoice no. mismatch → Needs review** (97.5 % match confidence) | ₹19,800 |
| – | PPL/26-27/0360 | Prakash Packaging | Booked as `PPL-26-27-0360`, portal shows `PPL/26-27/360` (separators / leading zero only) | **Matched** (format variation is normalised) | ₹0 |

**Expected dashboard after Analyze:** Expected ITC **₹4,22,820** · Reconciled **₹3,73,150** · Potential ITC Exposure **₹49,670** (11.7 %) · **6 issues** ·
18 matched of 24. ITC Risk Radar: Arvind Steel Industries ₹22,680 HIGH (rank 1), Neha Electricals ₹12,330 HIGH, Om Chemicals ₹9,720 MEDIUM,
Sunrise Logistics ₹3,240 LOW, Prakash Packaging ₹1,700 LOW. Supplier Risk is *not available* (three months of history is below the model's
five-month minimum), so Investigation Priority uses exposure and issue evidence only. The AI Investigation Brief works on this data
(AI-written when a Groq key is configured, otherwise the evidence-based fallback).

## How to upload

1. Open ITC Shield (pre-analysis dashboard). In **User data**, choose `sample_purchase_register.csv` as the *Purchase register* and
   `sample_portal_data.csv` as the *Portal / GSTR-2B data*.
2. Click **Upload & validate** — you should see "24 purchase rows · 22 portal rows · Jul 2026 - Sep 2026" and the column mapping.
3. Click **Analyze uploaded data**. The dashboard, Radar, Supplier Detail and Invoice Detail now show *your uploaded data*.
4. To go back to the built-in synthetic datasets, pick one under **Sample data**.

API equivalent: `curl -F purchase_file=@sample_purchase_register.csv -F portal_file=@sample_portal_data.csv localhost:8000/api/upload`
then `curl -X POST localhost:8000/api/analyze`.

## Try breaking it (expected errors)

* Remove the *Taxable Value* column → "The purchase register is missing columns: taxable_value (accepted names: …)".
* Add both a *GSTIN* and a *Supplier GSTIN* column → "Ambiguous columns … Keep one of them."
* Upload an empty file, a PDF, or a file with a date like `31/31/2026` → a specific message, never a stack trace.
