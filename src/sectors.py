"""Thin Sectors API client.

STATUS: /close/, /daily/ and /news/ shapes CONFIRMED against the live API
on 23 Sep 2026 (scripts/verify_api.py). Originally drafted from the docs — the team's key was still pending when this was written.
Two things here are still guesses and MUST be confirmed with
`scripts/verify_api.py` (or one manual call) before day 5:

  1. Whether /close/ and /news/ paginate with page= or limit/offset. This file
     now auto-detects both shapes in get_paginated(), so it should work either
     way — but that auto-detection itself is untested against a real payload.
  2. Field names on each row. Per the v2 changelog, IDX rows use `symbol`
     (renamed from the old `idx_ticker`/`ticker`), so normalise_symbol() below
     is called with a symbol-first, ticker-fallback lookup. If the live
     response uses something else entirely, fix it HERE only — nothing
     downstream touches raw API JSON.

Run this the moment the key is confirmed working:

    python -c "from sectors import SectorsClient; \
               import json; \
               print(json.dumps(SectorsClient().daily_universe_close('2026-09-22')[:2], indent=2))"
"""
import logging
import time

import requests

from config import (
    HTTP_RETRIES,
    HTTP_TIMEOUT,
    PAGE_SIZE,
    SECTORS_API_KEY,
    SECTORS_BASE_URL,
)

log = logging.getLogger(__name__)


class SectorsError(RuntimeError):
    pass


class SectorsClient:
    def __init__(self, api_key=None):
        self.api_key = api_key or SECTORS_API_KEY
        if not self.api_key:
            raise SectorsError("SECTORS_API_KEY is not set")
        self.session = requests.Session()
        # Bare key, NOT "Bearer <key>". This trips people up.
        self.session.headers.update({"Authorization": self.api_key})

    def get(self, path, params=None):
        """GET with exponential backoff. Retries on 5xx and 429 only."""
        url = f"{SECTORS_BASE_URL}{path}"
        delay = 1.0

        for attempt in range(1, HTTP_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
            except requests.RequestException as exc:
                if attempt == HTTP_RETRIES:
                    raise SectorsError(f"network failure on {path}: {exc}") from exc
                log.warning("network error %s (attempt %d), retrying", exc, attempt)
                time.sleep(delay)
                delay *= 2
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt == HTTP_RETRIES:
                    raise SectorsError(f"{resp.status_code} on {path} after retries")
                wait = float(resp.headers.get("Retry-After", delay))
                log.warning("%s on %s, waiting %.1fs", resp.status_code, path, wait)
                time.sleep(wait)
                delay *= 2
                continue

            # 4xx other than 429 will not fix themselves — fail loudly.
            raise SectorsError(f"{resp.status_code} on {path}: {resp.text[:200]}")

        raise SectorsError(f"exhausted retries on {path}")

    def get_paginated(self, path, params=None, page_size=PAGE_SIZE, max_pages=200):
        """Walk a v2 list endpoint, yielding rows.

        CONFIRMED against the live API (verify_api.py, 23 Sep 2026):
          - envelope is {"results": [...], "pagination": {...}}
          - pagination is limit/offset ONLY; page= returns HTTP 400
          - pagination carries has_next and next_offset
          - default page size is 20
        If the API rejects our page_size on the first call (400), we retry
        once with the server default rather than failing the whole run.
        """
        params = dict(params or {})
        limit = page_size
        offset = 0

        for _ in range(max_pages):
            call = {**params, "offset": offset}
            if limit:
                call["limit"] = limit
            try:
                payload = self.get(path, call)
            except SectorsError as exc:
                if limit and offset == 0 and " 400 " in f" {exc} ":
                    log.warning("limit=%s rejected on %s, using server default", limit, path)
                    limit = None
                    continue
                raise

            if isinstance(payload, list):  # e.g. /daily/{symbol}/ — unpaginated
                yield from payload
                return

            rows = payload.get("results") or []
            yield from rows

            pg = payload.get("pagination") or {}
            if not rows or not pg.get("has_next"):
                return
            offset = pg.get("next_offset") or offset + len(rows)

        log.warning("hit max_pages=%d on %s — results may be truncated", max_pages, path)

    # ------------------------------------------------------------ endpoints
    # Paths below are relative to SECTORS_BASE_URL = "https://api.sectors.app/v2"
    # per the v2 changelog. Confirm each against verify_api.py output.

    def daily_universe_close(self, trade_date):
        """Every IDX ticker's close for one day, in one paginated feed.

        GET /v2/close/?date=YYYY-MM-DD — confirmed live.
        WARNING: rows are only {symbol, date, close}. NO volume, NO market cap,
        so the volume filter cannot run on this feed. The nightly job uses
        per-symbol /daily/ for the watchlist instead. Kept for reference only.
        """
        return list(self.get_paginated("/close/", {"date": trade_date}))

    def daily_symbol(self, symbol, start, end):
        """Per-symbol OHLCV history. Max 90-day window.

        GET /v2/daily/{symbol}/?start=&end= — confirmed live. Returns a BARE
        list of {symbol, date, open, high, low, close, volume, market_cap}.
        One call costs the same whether the window is 1 day or 90.
        """
        return self.get(f"/daily/{symbol}/", {"start": start, "end": end})

    def news(self, start, end, sub_sector=None):
        """GET /v2/news/?extension=idx — confirmed live, results/pagination envelope.

        NOTE: `source` is the article URL, not a publisher name.
        """
        params = {"extension": "idx", "start": start, "end": end}
        if sub_sector:
            params["sub_sector"] = sub_sector
        return list(self.get_paginated("/news/", params))


def _first(row, *keys):
    """Return the first present, non-empty key from row."""
    for k in keys:
        if row.get(k) not in (None, ""):
            return row[k]
    return None


def normalise_symbol(raw):
    """BBCA / bbca / BBCA.JK  ->  BBCA

    Call this at EVERY ingestion boundary. Skipping it silently produces three
    rows per company and the bug is invisible until your baselines are wrong.
    """
    if not raw:
        return None
    return str(raw).strip().upper().removesuffix(".JK")
