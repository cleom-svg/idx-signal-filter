"""Ingestion: Sectors API -> raw tables. No analysis happens here.

Raw data is stored BEFORE any filtering so that changing filter logic never
requires re-fetching from a paid, rate-limited API.
"""
import hashlib
import logging

from datetime import date, timedelta

from config import DAILY_LOOKBACK_DAYS
from db import query, upsert_many
from sectors import SectorsClient, _first, normalise_symbol

log = logging.getLogger(__name__)


def _num(value):
    """Tolerant numeric parse — APIs return '1.234' or None or ''."""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _news_id(row):
    """Stable id. Prefer the source's own id; fall back to a content hash."""
    for key in ("id", "article_id", "uuid"):
        if row.get(key):
            return str(row[key])
    blob = f"{row.get('title', '')}|{row.get('source', '')}|{row.get('url', '')}"
    return hashlib.sha1(blob.encode()).hexdigest()


def ingest_daily_close(client, trade_date, symbols=None):
    """OHLCV for every watchlist symbol, via /daily/{symbol}/.

    Why not /close/ (the whole market in one feed)? Confirmed live: /close/
    rows have no volume, so the volume filter could not run. /daily/ has
    volume and market cap. One call per watchlist symbol per night; each call
    re-fetches the last DAILY_LOOKBACK_DAYS so a missed run heals itself
    (upserts are idempotent on (symbol, trade_date)).
    """
    if symbols is None:
        symbols = [r["symbol"] for r in query("SELECT symbol FROM watchlist")]
    end = date.fromisoformat(str(trade_date))
    start = end - timedelta(days=DAILY_LOOKBACK_DAYS)

    rows = []
    for sym in symbols:
        try:
            raw = client.daily_symbol(sym, start.isoformat(), end.isoformat())
        except Exception as exc:  # one bad symbol must not kill the run
            log.warning("daily fetch failed for %s: %s", sym, exc)
            continue
        for r in raw if isinstance(raw, list) else (raw.get("results") or []):
            symbol = normalise_symbol(r.get("symbol")) or sym
            close = _num(r.get("close"))
            if close is None or not r.get("date"):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": r["date"],
                    "close": close,
                    "volume": _num(r.get("volume")),
                    "market_cap": _num(r.get("market_cap")),
                }
            )

    n = upsert_many(
        "daily_close",
        ["symbol", "trade_date", "close", "volume", "market_cap"],
        rows,
        conflict_cols=["symbol", "trade_date"],
    )
    log.info("ingested %d close rows for %d symbols up to %s", n, len(symbols), trade_date)
    return n


def ingest_news(client, start, end):
    """News via /v2/news/?extension=idx.

    Row keys CONFIRMED live: title, body, source (= article URL), thumbnail,
    timestamp, sector, sub_sector, tags, symbols, dimension.
    """
    raw = client.news(start, end)
    watch = {r["symbol"] for r in query("SELECT symbol FROM watchlist")}

    rows = []
    for r in raw:
        title = (r.get("title") or "").strip()
        if not title:
            continue
        # An article can mention several stocks. Keep a watchlist stock if it
        # mentions one (even if it isn't listed first), otherwise the first.
        # Articles with no symbols (macro news) keep symbol=None and are
        # dropped later by the watchlist stage.
        syms = [normalise_symbol(s) for s in (r.get("symbols") or []) if s]
        symbol = next((s for s in syms if s in watch), syms[0] if syms else None)
        rows.append(
            {
                "id": _news_id(r),
                "symbol": symbol,
                "published_at": r.get("timestamp"),
                "title": title,
                "body": r.get("body"),
                "source": r.get("source"),
                "url": r.get("source"),  # `source` holds the article URL
            }
        )

    n = upsert_many(
        "news_items",
        ["id", "symbol", "published_at", "title", "body", "source", "url"],
        rows,
        conflict_cols=["id"],
        update_cols=[],  # news is immutable once seen — DO NOTHING
    )
    log.info("ingested %d news rows for %s..%s", n, start, end)
    return n


def run(trade_date):
    client = SectorsClient()
    n_close = ingest_daily_close(client, trade_date)
    n_news = ingest_news(client, trade_date, trade_date)
    return {"close_rows": n_close, "news_rows": n_news}
