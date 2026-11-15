# Site Safety Intelligence

A database and ingestion pipeline for a general contractor to quickly check the OSHA safety history of subcontractors before signing them on, and for a foreman to check specific violation history on site.

Built with Python and SQLite using the public OSHA inspection and violation CSVs from `data.dol.gov`.

## Database Schema

I split the data into 3 main tables and 1 summary table in SQLite (`safety.db`):

```text
subcontractors (1) ---> (many) inspections (1) ---> (many) violations
```

- **subcontractors**: `id`, `canonical_name`, `state`, `naics_code`
  Stores one cleaned row per company (e.g. turning `"Apex Steel, LLC"` and `"APEX STEEL INC"` into `"APEX STEEL"`).
- **inspections**: `activity_nr`, `subcontractor_id`, `raw_estab_name`, `open_date`, `insp_type`, `site_city`, `site_state`
  Stores each OSHA inspection. I kept `raw_estab_name` here so you can still see the original company name OSHA recorded.
- **violations**: `id`, `activity_nr`, `viol_type`, `standard_code`, `standard_name`, `current_penalty`, `nr_exposed`
  Stores active citations linked to each inspection, with common codes translated into plain English (e.g. `W` to `Willful`, `19260501` to `Fall Protection`).
- **subcontractor_risk_summary**: `subcontractor_id`, `canonical_name`, `state`, `total_inspections`, `fatality_inspections`, `willful_count`, `repeat_count`, `serious_count`, `total_penalty`, `last_inspection_date`, `risk_score`
  Pre-calculates the totals for each subcontractor when the database is built.

### Why structure it like this?

1. **Avoids repeating data:** Separating subcontractors, inspections, and violations means company info isn't duplicated on every violation row, and one company can link to multiple inspections with different name spellings.
2. **Keeps the raw names:** Because OSHA company names are messy, keeping `raw_estab_name` on the inspections table makes it easy to check if two records were actually the same company or a false match.
3. **Faster lookups for the GC:** Instead of joining millions of rows every time someone checks 10–15 subcontractors, queries can hit `subcontractor_risk_summary` straight away and only pull full inspection/violation rows when drilling down into a company.

## Decisions and Trade-offs

- **Filtered to Construction (NAICS `23*`) from 2021 onwards:** The full OSHA download has over 18 million rows across all industries going back to the 1970s. Filtering to recent construction keeps the database small and relevant to how a sub operates now, though older history is left out.
- **Dropped deleted violations (`DELETE_FLAG`):** Some rows in the violations CSV have `DELETE_FLAG = 'X'` (dismissed/vacated citations). I filtered those out during ingestion so subcontractors aren't flagged for fines that were thrown out.
- **Simple company name cleaning:** Stripping punctuation and suffixes like `LLC`, `INC`, and `CORP` catches most duplicate spellings without needing slow matching logic. The trade-off is that two completely different companies with the same name in different states could get grouped together.
- **Weighted risk score instead of total count:** Bigger subcontractors naturally get inspected more often. Scoring `Fatality` (+60), `Willful` (+50), `Repeat` (+30), and `Serious` (+10) highlights actual safety red flags rather than company size.
- **SQLite over Postgres:** Easier for anyone to run locally without setting up a database server. The downside is it wouldn't handle lots of concurrent users in production.

## How to Run

1. Install pandas:
   ```bash
   pip install pandas
   ```
2. Download the OSHA Inspection and Violation CSVs from `data.dol.gov` and put them in `data/OSHA_inspection/` and `data/OSHA_violation/`.
3. Build the database:
   ```bash
   python ingest.py
   ```
