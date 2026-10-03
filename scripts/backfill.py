"""One-time history load. Run this on DAY 1 — it is the long pole.

    python scripts/backfill.py            # 89 days = ONE /daily/ call per symbol

The Sectors API caps most range endpoints at 90 days, so history must be
fetched in chunks. This script is resumable: it skips (symbol, window) pairs
already present in the DB, so a crash halfway through costs you nothing.

40 tickers x 1 year = ~160 calls. Minutes, not hours. Do NOT backfill the full
universe — that would consume your entire first week.
"""
import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import baselines  # noqa: E402
from db import query, upsert_many  # noqa: E402
from ingest import _num  # noqa: E402
from sectors import SectorsClient, SectorsError, normalise_symbol  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s | %(message)s")
log = logging.getLogger("backfill")

WINDOW_DAYS = 90

# Start narrow. Liquid large-caps give you meaningful baselines quickly.
DEFAULT_WATCHLIST = [
    "BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII", "UNVR", "ICBP",
    "INDF", "KLBF", "GGRM", "HMSP", "ADRO", "PTBA", "ITMG", "UNTR",
    "ANTM", "INCO", "MDKA", "AMRT", "CPIN", "JPFA", "SMGR", "INTP",
    "PGAS", "EXCL", "ISAT", "TOWR", "MIKA", "GOTO", "BRPT", "TPIA",
    "MEDC", "AKRA", "BUKA", "ARTO", "BRIS", "MAPI", "ACES", "ERAA",
]


def windows(start, end, size=WINDOW_DAYS):
    cur = start
    while cur < end:
        stop = min(cur + timedelta(days=size - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


def existing_dates(symbol):
    rows = query(
        "SELECT trade_date FROM daily_close WHERE symbol = %s", (symbol,)
    )
    return {r["trade_date"] for r in rows}


def backfill_symbol(client, symbol, start, end, sleep=0.3):
    have = existing_dates(symbol)
    total = 0

    for w_start, w_end in windows(start, end):
        # Resume: skip a window we already have most of.
        span = (w_end - w_start).days + 1
        covered = sum(1 for d in have if w_start <= d <= w_end)
        if covered >= span * 0.6:
            log.debug("%s %s..%s already covered", symbol, w_start, w_end)
            continue

        try:
            payload = client.daily_symbol(symbol, w_start.isoformat(), w_end.isoformat())
        except SectorsError as exc:
            log.warning("%s %s..%s failed: %s", symbol, w_start, w_end, exc)
            continue

        rows = payload if isinstance(payload, list) else payload.get("data", [])
        parsed = []
        for r in rows:
            close = _num(r.get("close"))
            if close is None:
                continue
            parsed.append({
                "symbol": normalise_symbol(r.get("symbol") or r.get("ticker") or symbol),
                "trade_date": r.get("date"),
                "close": close,
                "volume": _num(r.get("volume")),
                "market_cap": _num(r.get("market_cap")),
            })

        total += upsert_many(
            "daily_close",
            ["symbol", "trade_date", "close", "volume", "market_cap"],
            parsed,
            conflict_cols=["symbol", "trade_date"],
        )
        time.sleep(sleep)   # be polite to a paid quota

    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=89)  # 1 window/symbol; enough for a 30-trading-day baseline
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_WATCHLIST)
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=args.days)
    client = SectorsClient()

    # Seed the watchlist table too.
    upsert_many(
        "watchlist", ["symbol"],
        [{"symbol": s} for s in args.symbols],
        conflict_cols=["symbol"], update_cols=[],
    )

    grand_total = 0
    for i, symbol in enumerate(args.symbols, 1):
        n = backfill_symbol(client, symbol, start, end)
        grand_total += n
        log.info("[%d/%d] %s: %d rows", i, len(args.symbols), symbol, n)

    log.info("backfill complete: %d rows", grand_total)
    log.info("computing baselines...")
    baselines.run()
    log.info("done")


if __name__ == "__main__":
    main()
