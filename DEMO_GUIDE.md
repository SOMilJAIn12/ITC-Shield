# ITC Shield – 90-second demo guide

**Before you start:** run `Start ITC Shield.bat` (Windows) or `./start.sh` (macOS/Linux), or open the deployed URL.
On the pre-analysis dashboard, under **Sample data**, **Demo Dataset** must be selected (the top-bar chip reads
"Demo Dataset (synthetic)"). Refresh the page for a clean take. If a Groq key is configured on the server the brief is AI-written;
otherwise the evidence-based brief appears – both work for the demo.

| # | Do | Say | ~sec |
|---|----|-----|-----|
| 1 | **Open the dashboard**, click **Analyze Data**. | "ITC Shield reconciles our purchase register with portal / GSTR-2B-style data. This is the synthetic demo set – you can also upload your own files here." | 0–10 |
| 2 | Point at the hero: **₹4.2L Potential ITC Exposure**, then **₹25.0L expected / ₹20.8L reconciled / 147 issues**. | "Of ₹25 lakh of expected input tax credit, ₹20.8 lakh reconciles. **₹4.2 lakh is potentially exposed** – that's the number a CFO wants first." | 10–20 |
| 3 | Scroll to **ITC Risk Radar** – "Where should you investigate first?" | "Reconciliation tools stop at a list of mismatches. The Radar ranks suppliers by Investigation Priority: exposure, supplier risk and issue evidence, all computed by the backend and fully explainable (open *How is it calculated?* if asked)." | 20–30 |
| 4 | Click **#1 Sundaram Steel Traders** (HIGH). | "Our highest priority supplier." | 30–35 |
| 5 | Point at the stat tiles and **Why is this supplier prioritized?**: ₹90.0K exposure (21.4 % of total), 19 of 39 invoices flagged, Missing in portal is the largest source, score 99.1 / 100. | "Every figure traces back to invoice-level evidence. On the Realistic dataset this tile also shows the predicted **Supplier Risk** (79 % HIGH for Lotus Heavy Machinery) learned from six months of history." | 35–50 |
| 6 | Scroll to **Affected invoices**, click the first one (**SST/26-27/0488**). | "Here is one invoice: ₹22,282.74 claimed, no portal record, matching confidence not available, exposure ₹22,282.74, priority HIGH, rank 1." Click **Back**. | 50–60 |
| 7 | On the supplier page click **Generate AI Brief**. | "Now the Act step. The AI only *explains* the backend's evidence – it never calculates. It tells us why this was flagged and exactly what to verify." | 60–75 |
| 8 | Scroll to **Draft supplier follow-up**, click **Copy draft**. | "…and drafts the supplier e-mail with the invoice numbers, dates and figures already filled in. Every figure quoted is checked against the evidence before it is shown." | 75–85 |
| 9 | Close. | **"Traditional reconciliation tells you what went wrong. ITC Shield tells you how much is exposed, who needs attention, why, and what to do next."** | 85–90 |

## Optional extras (if there is time)

* **Dataset switch:** click **Change data**, choose **Realistic Sample Dataset** and **Analyze Data** – ₹59.0L / ₹53.1L / ₹5.9L / 85 issues,
  and the Radar now shows Supplier Risk scores (Lotus Heavy Machinery ₹1.4L · 79 % · HIGH). Switch back to **Demo Dataset** afterwards.
* **Your own data:** under **User data** upload `demo_upload_data/sample_purchase_register.csv` + `sample_portal_data.csv`, **Upload & validate**,
  **Analyze uploaded data** – ₹4.2L expected / ₹49.7K exposure / 6 issues, top-bar chip "Your uploaded data". (Synthetic test files; the
  6 cases are listed in `demo_upload_data/README.md`.)
* **Explain this issue:** on any flagged invoice, the AI explanation panel does the same for one invoice.
* **Reconciliation tab:** every entry with status, exposure and investigation priority; filter by issue type or search.

## Safe wording

Say *Potential ITC Exposure*, *Supplier Risk* and *Investigation Priority*. Do not say fraud, violation, non-compliance or
guaranteed loss – the product never does, and the AI brief is rejected if it tries.

## If something goes wrong

* Red banner "cannot reach the server": the backend is not running – restart the launcher, click **Retry**.
* AI brief shows "Evidence-based brief (no AI)": no LLM key or the provider failed – the demo continues unchanged.
* Numbers differ from the table above: another data source is active (see the top-bar chip); click **Change data**, pick **Demo Dataset**, re-run.
