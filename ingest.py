import os
import sqlite3
import pandas as pd

INSP_FOLDER = "data/OSHA_inspection"
VIOL_FOLDER = "data/OSHA_violation"
DB_FILE = "safety.db"
MIN_DATE = "2021-01-01"   # Last 5-6 years of construction data

VIOL_TYPES = {
    "W": "Willful",
    "R": "Repeat",
    "S": "Serious",
    "O": "Other",
}

INSP_TYPES = {
    "A": "Fatality/Catastrophe",
    "B": "Complaint",
    "C": "Referral",
    "F": "Follow-Up",
    "H": "Planned",
}

TOP_STANDARDS = {
    "19260501": "Fall Protection",
    "19260503": "Fall Protection Training",
    "19260451": "Scaffolding",
    "19261053": "Ladders",
    "19260651": "Excavations & Trenching",
    "19260652": "Trench Safety",
    "19260405": "Electrical Wiring",
    "19260754": "Steel Erection",
    "19261153": "Silica Exposure",
    "19260100": "Hard Hats / Head Protection",
}


def clean_company_name(name):
    """Turn 'Apex Steel Erectors, LLC.' into 'APEX STEEL ERECTORS'."""
    name = name.upper()
    for ch in [".", ",", "-", "&", "'", "/"]:
        name = name.replace(ch, " ")
    words = name.split()
    ignore = {"LLC", "INC", "CORP", "CO", "LTD", "COMPANY", "INCORPORATED", "CORPORATION"}
    kept = [w for w in words if w not in ignore]
    return " ".join(kept)


def clean_date(text):
    if not text or text == "NaT":
        return ""
    return text[:10]


def get_standard_name(code):
    for prefix, label in TOP_STANDARDS.items():
        if code.startswith(prefix):
            return label
    return "Other Safety Standard"


def to_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def to_int(val):
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return 0


def main():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    # Create our 3 clean tables
    cur.executescript("""
        DROP TABLE IF EXISTS subcontractor_risk_summary;
        DROP TABLE IF EXISTS violations;
        DROP TABLE IF EXISTS inspections;
        DROP TABLE IF EXISTS subcontractors;

        CREATE TABLE subcontractors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT UNIQUE,
            state TEXT,
            naics_code TEXT
        );

        CREATE TABLE inspections (
            activity_nr TEXT PRIMARY KEY,
            subcontractor_id INTEGER,
            raw_estab_name TEXT,
            open_date TEXT,
            insp_type TEXT,
            site_city TEXT,
            site_state TEXT,
            FOREIGN KEY (subcontractor_id) REFERENCES subcontractors(id)
        );

        CREATE TABLE violations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            activity_nr TEXT,
            viol_type TEXT,
            standard_code TEXT,
            standard_name TEXT,
            current_penalty REAL,
            nr_exposed INTEGER,
            FOREIGN KEY (activity_nr) REFERENCES inspections(activity_nr)
        );
    """)

    company_ids = {}
    kept_inspections = set()

    # Load Construction inspections
    insp_cols = ["ACTIVITY_NR", "ESTAB_NAME", "SITE_CITY", "SITE_STATE", "NAICS_CODE", "INSP_TYPE", "OPEN_DATE"]
    for filename in sorted(os.listdir(INSP_FOLDER)):
        if not filename.endswith(".csv"):
            continue
        print("Reading inspections:", filename)
        path = os.path.join(INSP_FOLDER, filename)

        for chunk in pd.read_csv(path, usecols=insp_cols, chunksize=100_000, dtype=str, low_memory=False):
            chunk = chunk.fillna("")
            # Filter to Construction and recent years
            chunk = chunk[chunk["NAICS_CODE"].str.startswith("23") & (chunk["OPEN_DATE"] >= MIN_DATE)]

            for row in chunk.to_dict("records"):
                canon = clean_company_name(row["ESTAB_NAME"])
                if not canon:
                    continue

                if canon not in company_ids:
                    cur.execute(
                        "INSERT INTO subcontractors (canonical_name, state, naics_code) VALUES (?, ?, ?)",
                        (canon, row["SITE_STATE"], row["NAICS_CODE"]),
                    )
                    company_ids[canon] = cur.lastrowid

                kept_inspections.add(row["ACTIVITY_NR"])
                cur.execute(
                    "INSERT OR IGNORE INTO inspections VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["ACTIVITY_NR"],
                        company_ids[canon],
                        row["ESTAB_NAME"],
                        clean_date(row["OPEN_DATE"]),
                        INSP_TYPES.get(row["INSP_TYPE"], row["INSP_TYPE"]),
                        row["SITE_CITY"],
                        row["SITE_STATE"],
                    ),
                )
        conn.commit()

    print(f"Saved {len(company_ids):,} subcontractors and {len(kept_inspections):,} inspections.")

    #Load active violations
    viol_cols = ["ACTIVITY_NR", "DELETE_FLAG", "STANDARD", "VIOL_TYPE", "CURRENT_PENALTY", "NR_EXPOSED"]
    total_viols = 0
    for filename in sorted(os.listdir(VIOL_FOLDER)):
        if not filename.endswith(".csv"):
            continue
        print("Reading violations:", filename)
        path = os.path.join(VIOL_FOLDER, filename)

        for chunk in pd.read_csv(path, usecols=viol_cols, chunksize=100_000, dtype=str, low_memory=False):
            chunk = chunk.fillna("")
            chunk = chunk[chunk["ACTIVITY_NR"].isin(kept_inspections) & (chunk["DELETE_FLAG"].str.strip() == "")]

            rows = []
            for row in chunk.to_dict("records"):
                rows.append((
                    row["ACTIVITY_NR"],
                    VIOL_TYPES.get(row["VIOL_TYPE"], "Other"),
                    row["STANDARD"],
                    get_standard_name(row["STANDARD"]),
                    to_float(row["CURRENT_PENALTY"]),
                    to_int(row["NR_EXPOSED"]),
                ))
            cur.executemany(
                "INSERT INTO violations (activity_nr, viol_type, standard_code, standard_name, current_penalty, nr_exposed) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            total_viols += len(rows)
        conn.commit()

    print(f"Saved {total_viols:,} active violations.")

    # Build summary table + indexes so searches are instant
    cur.executescript("""
        CREATE INDEX idx_sub_name ON subcontractors(canonical_name);
        CREATE INDEX idx_insp_sub ON inspections(subcontractor_id);
        CREATE INDEX idx_viol_act ON violations(activity_nr);

        CREATE TABLE subcontractor_risk_summary AS
        SELECT
            s.id AS subcontractor_id,
            s.canonical_name,
            s.state,
            COUNT(DISTINCT i.activity_nr) AS total_inspections,
            COUNT(DISTINCT CASE WHEN i.insp_type = 'Fatality/Catastrophe' THEN i.activity_nr END) AS fatality_inspections,
            SUM(CASE WHEN v.viol_type = 'Willful' THEN 1 ELSE 0 END) AS willful_count,
            SUM(CASE WHEN v.viol_type = 'Repeat' THEN 1 ELSE 0 END) AS repeat_count,
            SUM(CASE WHEN v.viol_type = 'Serious' THEN 1 ELSE 0 END) AS serious_count,
            ROUND(COALESCE(SUM(v.current_penalty), 0), 2) AS total_penalty,
            MAX(i.open_date) AS last_inspection_date,
            (
                SUM(CASE WHEN v.viol_type = 'Willful' THEN 50 ELSE 0 END) +
                SUM(CASE WHEN v.viol_type = 'Repeat' THEN 30 ELSE 0 END) +
                SUM(CASE WHEN v.viol_type = 'Serious' THEN 10 ELSE 0 END) +
                COUNT(DISTINCT CASE WHEN i.insp_type = 'Fatality/Catastrophe' THEN i.activity_nr END) * 60
            ) AS risk_score
        FROM subcontractors s
        LEFT JOIN inspections i ON s.id = i.subcontractor_id
        LEFT JOIN violations v ON i.activity_nr = v.activity_nr
        GROUP BY s.id, s.canonical_name, s.state;
    """)
    conn.commit()

    size_mb = os.path.getsize(DB_FILE) / (1024 * 1024)
    print(f"\nDONE! Built {DB_FILE} ({size_mb:.1f} MB)")

    print("Top 5 highest-risk subs:")
    for r in cur.execute("SELECT canonical_name, state, willful_count, repeat_count, serious_count, total_penalty, risk_score FROM subcontractor_risk_summary ORDER BY risk_score DESC LIMIT 5"):
        print(" ", r)

    conn.close()


if __name__ == "__main__":
    main()