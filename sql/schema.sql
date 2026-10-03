-- IDX Signal Filter — schema
-- Run once:  psql "$DATABASE_URL" -f sql/schema.sql
--
-- Conventions (agreed day 1, do not deviate):
--   * symbol is UPPERCASE with no .JK suffix — normalised at ingestion
--   * money is NUMERIC, never FLOAT
--   * all timestamps are UTC; convert to WIB only at display time
--   * prices are stored UNADJUSTED; corporate actions live separately

-- ---------------------------------------------------------------- raw layer

CREATE TABLE IF NOT EXISTS daily_close (
    symbol      TEXT          NOT NULL,
    trade_date  DATE          NOT NULL,
    close       NUMERIC(20,4) NOT NULL,
    volume      BIGINT,
    market_cap  NUMERIC(24,2),
    fetched_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)      -- idempotency: re-runs upsert
);

CREATE INDEX IF NOT EXISTS idx_daily_close_date ON daily_close (trade_date);

CREATE TABLE IF NOT EXISTS news_items (
    id           TEXT        PRIMARY KEY,   -- source id, else sha1 of title+url
    symbol       TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    title        TEXT        NOT NULL,
    body         TEXT,
    source       TEXT,
    url          TEXT,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_news_published ON news_items (published_at);
CREATE INDEX IF NOT EXISTS idx_news_symbol    ON news_items (symbol);

-- ------------------------------------------------------------ derived layer

CREATE TABLE IF NOT EXISTS baselines (
    symbol       TEXT NOT NULL,
    trade_date   DATE NOT NULL,
    vol_mean_30d NUMERIC,
    vol_std_30d  NUMERIC,
    ret_mean_30d NUMERIC,
    ret_std_30d  NUMERIC,
    daily_return NUMERIC,
    PRIMARY KEY (symbol, trade_date)
);

-- Every score we computed, kept so filters can be re-tuned against history
-- without re-fetching from the paid API.
CREATE TABLE IF NOT EXISTS item_scores (
    item_id      TEXT NOT NULL,
    run_date     DATE NOT NULL,
    dedup_group  INT,
    volume_z     NUMERIC,
    return_z     NUMERIC,
    passed_stage TEXT NOT NULL,   -- raw | deduped | surprising | watchlist | sent
    PRIMARY KEY (item_id, run_date)
);

-- ------------------------------------------------------- operational layer

-- Bot idempotency: prevents double-posting when a run is retried.
CREATE TABLE IF NOT EXISTS sent_digests (
    run_date   DATE PRIMARY KEY,
    sent_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    item_count INT NOT NULL
);

-- Observability: one row per run. Feeds the dashboard AND the evaluation.
-- `trigger` is the rubric evidence: the judging video needs to show runs
-- that fired unattended (schedule), not just manual ones (workflow_dispatch).
CREATE TABLE IF NOT EXISTS run_log (
    run_date         DATE PRIMARY KEY,
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    status           TEXT,   -- success | failed | no_trading_day
    trigger          TEXT,   -- schedule | workflow_dispatch
    error            TEXT,
    count_raw        INT DEFAULT 0,
    count_deduped    INT DEFAULT 0,
    count_surprising INT DEFAULT 0,
    count_sent       INT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    note   TEXT
);
