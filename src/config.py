"""Central config. Everything tunable lives here, nothing is hardcoded downstream."""
import os

from dotenv import load_dotenv

load_dotenv()

# --- credentials -----------------------------------------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")
SECTORS_API_KEY = os.environ.get("SECTORS_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- api -------------------------------------------------------------------
SECTORS_BASE_URL = "https://api.sectors.app/v2"
# NOTE: Sectors uses a BARE Authorization header, not "Bearer <key>".
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
# Requested rows per page on list endpoints (server default is 20). If the API
# rejects this, sectors.get_paginated falls back to the default automatically.
# Bigger pages = fewer calls = fewer credits.
PAGE_SIZE = 100
# How many calendar days each nightly /daily/{symbol}/ call re-fetches. Costs
# the same as 1 day, and lets a missed run heal itself on the next one.
DAILY_LOOKBACK_DAYS = 7

# --- filter thresholds -----------------------------------------------------
# These are STARTING values. Tune them against labelled history (see
# scripts/evaluate.py) and record the justification in the writeup.
DEDUP_SIMILARITY = 0.75   # cosine similarity above which items are duplicates
VOLUME_Z_THRESHOLD = 2.5  # |z| of volume vs 30-day baseline
RETURN_Z_THRESHOLD = 2.0  # |z| of daily return vs 30-day baseline
BASELINE_WINDOW = 30      # trading days
MIN_BASELINE_OBS = 10     # below this, baseline is unreliable — skip the symbol

MAX_DIGEST_ITEMS = 10     # hard cap. Alert fatigue kills these systems.

# --- market ----------------------------------------------------------------
MARKET_TZ = "Asia/Jakarta"
