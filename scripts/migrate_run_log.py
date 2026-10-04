import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import psycopg2  # noqa: E402
from config import DATABASE_URL  # noqa: E402

SQL = """
ALTER TABLE run_log ADD COLUMN IF NOT EXISTS count_watchlist INT DEFAULT 0;
UPDATE run_log SET trigger = 'local' WHERE trigger IS NULL;
ALTER TABLE run_log ALTER COLUMN trigger SET DEFAULT 'local';
ALTER TABLE run_log ALTER COLUMN trigger SET NOT NULL;
ALTER TABLE run_log DROP CONSTRAINT run_log_pkey;
ALTER TABLE run_log ADD PRIMARY KEY (run_date, trigger);
"""

conn = psycopg2.connect(DATABASE_URL)
try:
    with conn, conn.cursor() as cur:   # all-or-nothing: an error undoes everything
        cur.execute(SQL)
    print("run_log upgraded")
finally:
    conn.close()
