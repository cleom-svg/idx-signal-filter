# IDX Signal Filter

**Signal over noise: an automated daily filtering pipeline for IDX market information**

A scheduled autonomous pipeline that ingests IDX market data, reduces it to items
that meaningfully deviate from baseline, and delivers a daily digest via Telegram.

See [`DESIGN.md`](DESIGN.md) for the full blueprint.

---

## What this is

One IDX announcement generates dozens of near-duplicate articles. Routine price and
volume movement produces a constant stream of low-consequence updates. This pipeline
filters that down to a handful of items per day.

```
raw intake  ->  deduplicate  ->  statistical surprise  ->  watchlist  ->  digest
  ~10,000         ~3,000              ~400                   ~60          ~8
```

Stages run in **ascending order of computational cost** — cheap deterministic filters
first, so expensive work only ever touches a small remainder.

We claim **measurable reduction, not a solution.**

---

## Setup

```bash
git clone <repo> && cd idx-signal-filter
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # fill in your credentials
psql "$DATABASE_URL" -f sql/schema.sql

python scripts/backfill.py --days 365    # DAY 1 — this is the long pole
python src/main.py --date 2026-09-11     # one manual run
python -m pytest tests/ -v
```

### Credentials

| Variable | Where to get it |
|---|---|
| `DATABASE_URL` | Supabase or Neon, free Postgres tier |
| `SECTORS_API_KEY` | sectors.app, **requires paid Insider plan** |
| `TELEGRAM_BOT_TOKEN` | message `@BotFather`, `/newbot` |
| `TELEGRAM_CHAT_ID` | message your bot, then open `api.telegram.org/bot<TOKEN>/getUpdates` |

`.env` is gitignored. In CI these live in **Settings → Secrets and variables → Actions**.

---

## Layout

```
src/
  config.py      all tunable thresholds — nothing hardcoded downstream
  db.py          SHARED — connection + bulk upsert. Agree changes with the team
  sectors.py     API client: retry, backoff, pagination, symbol normalisation
  ingest.py      Sectors -> raw tables. No analysis here
  baselines.py   30-day rolling stats via SQL window functions
  filters.py     the funnel. Pure functions, fully unit-tested
  digest.py      formatting, Telegram delivery, dead man's switch
  main.py        orchestrator: trading-day check, run logging, error handling
sql/schema.sql   tables, indexes, conventions
scripts/backfill.py  one-time history load, chunked and resumable
tests/           run these before every push
docs/            static GitHub Pages dashboard
fixtures/        contract files so the frontend is never blocked
```

---

## Three things that will break if you change them

**1. The baseline window excludes the current row.**
`ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING` in `baselines.py`. Include today in
today's baseline and today can never look anomalous — every z-score becomes quietly
wrong while the pipeline keeps running beautifully.

**2. Symbol normalisation happens at the ingestion boundary.**
Sectors accepts `BBCA`, `bbca`, and `BBCA.JK` interchangeably. Canonical form is
uppercase, no suffix. Skip this and you silently get three rows per company.

**3. Do not swap TF-IDF for `sentence-transformers`.**
It pulls in PyTorch (~800MB), downloaded on every CI run. Lexical overlap is more
than enough for near-identical headlines about the same announcement.

---

## Team

| Track | Owner | Files |
|---|---|---|
| Ingestion + DB | A | `ingest.py`, `backfill.py`, `db.py`, `schema.sql` |
| Filters + CI | B | `baselines.py`, `filters.py`, `daily.yml` |
| Delivery | C | `digest.py`, Telegram bot |
| Observability | D | `docs/` dashboard |

**Assign files, not features.** Merge conflicts across four people on a two-week
deadline cost more than anyone expects.

The frontend pair visualise **the system, not the market** — run history and
per-stage filter counts are direct evidence of the thing the rubric grades. A price
chart is decoration.

---

## The deadline that matters is day 5, not day 18

Run history cannot be compressed later. Twelve green checkmarks across real trading
days is the primary evidence of autonomy. **Deploy something trivial early and
improve it in place.**

The version of this that goes wrong is spending days 1–8 perfecting the filter logic
because it is the interesting part, then deploying on day 12 with a beautiful
algorithm and four green checkmarks.

---

## Evaluation

We have ground truth, which most filtering projects lack. For a news item at time
`T`, measure the return from `T` to `T+1` normalised by the stock's own volatility.
Moved 3σ → it mattered. Moved 0.2σ → it did not.

| Metric | Definition |
|---|---|
| Compression ratio | `1 - sent/raw` |
| **Miss rate** | of items *suppressed*, % preceding a material move |
| Open rate | of items sent, % opened |

**Optimise miss rate, not precision.** A filter sending 5 useful items a day is
worthless if it silently drops the announcement that moved a position 12%.
