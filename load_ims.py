"""
Load ims_subscribers.csv into combined.duckdb as table `ims_dump`.

Handles schema drift: any column present in the CSV is loaded, but
IMSI and the MSISDN-equivalent (MSISDN or ISDN) are renamed to
lowercase imsi/msisdn so downstream code has stable names.
"""
import duckdb
import pandas as pd
import os

DB_PATH  = "combined.duckdb"
CSV_PATH = "ims_subscribers.csv"

if not os.path.exists(CSV_PATH):
    raise SystemExit(f"Missing {CSV_PATH}")

# Read once with pandas so we can rename columns robustly
df = pd.read_csv(CSV_PATH, dtype=str, keep_default_na=False)

# Strip BOM and whitespace from headers
df.columns = [c.strip().lstrip("\ufeff").upper() for c in df.columns]
print("CSV columns:", list(df.columns))

# Locate the MSISDN-equivalent column (ISDN in the new dump, MSISDN in the old)
msisdn_col = None
for candidate in ("MSISDN", "ISDN"):
    if candidate in df.columns:
        msisdn_col = candidate
        break
if msisdn_col is None:
    raise SystemExit("No MSISDN or ISDN column found in the CSV")

if "IMSI" not in df.columns:
    raise SystemExit("No IMSI column found in the CSV")

# Rename the two columns the reconciliation depends on
df = df.rename(columns={msisdn_col: "msisdn", "IMSI": "imsi"})

# Lowercase every remaining column name for consistency
df.columns = [c.lower() for c in df.columns]

# Load into DuckDB
con = duckdb.connect(DB_PATH)
con.execute("DROP TABLE IF EXISTS ims_dump")
con.register("ims_df", df)
con.execute("CREATE TABLE ims_dump AS SELECT * FROM ims_df")

print("ims_dump rows:", con.execute("SELECT COUNT(*) FROM ims_dump").fetchone()[0])
print(con.execute("DESCRIBE ims_dump").df())
con.close()
