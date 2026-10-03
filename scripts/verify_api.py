"""One-off sanity check against the live Sectors v2 API.

Run this locally (not in the sandbox — api.sectors.app isn't reachable from
there) the moment SECTORS_API_KEY is set:

    export SECTORS_API_KEY=your_key_here
    python scripts/verify_api.py

It answers the exact questions sectors.py currently guesses at:
  - does /v2/close/ exist and what does one row look like?
  - does /v2/news/?extension=idx exist and what does one row look like?
  - is pagination page= or limit/offset, and is the envelope {data:[...]}
    or {results:[...], pagination:{...}}?
  - does /close/ have TODAY's data yet (matters for the same-day-vs-
    yesterday fix in main.py)?
Nothing here writes to the DB or spends more than ~4 credits.
"""
import json
import os
from datetime import date, timedelta

import requests

KEY = os.environ.get("SECTORS_API_KEY")
if not KEY:
    raise SystemExit("export SECTORS_API_KEY first")

BASE = "https://api.sectors.app/v2"
HEADERS = {"Authorization": KEY}  # bare key, not "Bearer ..."


def show(label, resp):
    print(f"\n=== {label} ===")
    print("status:", resp.status_code)
    try:
        body = resp.json()
    except ValueError:
        print(resp.text[:500])
        return None
    # print just the shape + first row, not the whole payload
    if isinstance(body, list):
        print("shape: bare list, len =", len(body))
        print(json.dumps(body[0] if body else None, indent=2)[:1000])
    elif isinstance(body, dict):
        print("shape: dict, keys =", list(body.keys()))
        rows = body.get("data") or body.get("results")
        print("first row:")
        print(json.dumps(rows[0] if rows else None, indent=2)[:1000])
        if "pagination" in body:
            print("pagination:", body["pagination"])
        if "next" in body or "has_next" in body:
            print("next:", body.get("next"), "has_next:", body.get("has_next"))
    return body


today = date.today()
yesterday = today - timedelta(days=1)

# 1. Does /close/ exist, and does it have TODAY's data yet?
r = requests.get(f"{BASE}/close/", params={"date": today.isoformat()}, headers=HEADERS, timeout=30)
show(f"/close/?date={today} (today)", r)

# 2. Fall back to yesterday, in case today isn't settled yet
r = requests.get(f"{BASE}/close/", params={"date": yesterday.isoformat()}, headers=HEADERS, timeout=30)
show(f"/close/?date={yesterday} (yesterday)", r)

# 3. Does pagination use page= or limit/offset? Try both, see which moves the cursor.
r = requests.get(f"{BASE}/close/", params={"date": yesterday.isoformat(), "page": 2}, headers=HEADERS, timeout=30)
show("/close/ with page=2", r)

r = requests.get(f"{BASE}/close/", params={"date": yesterday.isoformat(), "limit": 5, "offset": 5}, headers=HEADERS, timeout=30)
show("/close/ with limit=5&offset=5", r)

# 4. News shape
r = requests.get(
    f"{BASE}/news/",
    params={"extension": "idx", "start": yesterday.isoformat(), "end": today.isoformat()},
    headers=HEADERS, timeout=30,
)
show("/news/?extension=idx", r)

# 5. One per-symbol daily call, to compare field names against src/sectors.py's
#    normalise_* assumptions (ticker/date/close/volume/market_cap)
r = requests.get(f"{BASE}/daily/BBCA/", params={"start": (today - timedelta(days=5)).isoformat(), "end": today.isoformat()}, headers=HEADERS, timeout=30)
show("/daily/BBCA/", r)

print("\n--- credits spent: ~5 ---")
print("Compare the field names above against src/sectors.py's normalise_symbol")
print("callers (they expect: symbol/ticker, date, close, volume, market_cap).")
print("Compare the pagination shape above against get_paginated() in src/sectors.py.")
