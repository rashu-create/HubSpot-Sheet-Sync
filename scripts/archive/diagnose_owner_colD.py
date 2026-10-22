"""Read col D of Sales Pipeline 2026 and report owner name distribution."""
import os
from collections import Counter
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds_file = os.environ["GOOGLE_CREDENTIALS_FILE"]
sheet_id   = os.environ["PIPELINE_SHEET_ID"]
tab_name   = os.getenv("PIPELINE_TAB_NAME", "Sales Pipeline 2026")

creds  = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
client = gspread.authorize(creds)
ws     = client.open_by_key(sheet_id).worksheet(tab_name)

col_d = ws.col_values(4)           # col D = index 4 (1-based)
data  = col_d[1:]                  # skip header

# Count individual name tokens (col D is comma-joined, e.g. "Chandra,Deeksha")
token_counts: Counter = Counter()
rows_with_piyush = 0
for cell in data:
    if not cell.strip():
        continue
    tokens = [t.strip() for t in cell.split(",") if t.strip()]
    for tok in tokens:
        token_counts[tok.lower()] += 1
    if any("piyush" in t.lower() for t in tokens):
        rows_with_piyush += 1

print("=== Owner token distribution (col D, case-normalised) ===")
for name, count in token_counts.most_common():
    flag = " ← PIYUSH" if "piyush" in name else ""
    print(f"  {name:<30} {count:>4}{flag}")

print(f"\nRows containing 'piyush': {rows_with_piyush}")
print(f"Total non-blank rows:      {sum(1 for c in data if c.strip())}")
