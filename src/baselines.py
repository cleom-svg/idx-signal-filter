"""Rolling 30-day baselines, computed in SQL.

THE CRITICAL DETAIL: the window is `ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING`.
It excludes the current row. If you include today in today's baseline, today
can never look anomalous and every z-score is quietly wrong. This is the single
easiest way to silently break the whole project.
"""
import logging

from config import BASELINE_WINDOW
from db import execute

log = logging.getLogger(__name__)

BASELINE_SQL = """
WITH returns AS (
    SELECT
        symbol,
        trade_date,
        volume,
        close / NULLIF(LAG(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
        ), 0) - 1 AS daily_return
    FROM daily_close
)
INSERT INTO baselines (
    symbol, trade_date, daily_return,
    vol_mean_30d, vol_std_30d, ret_mean_30d, ret_std_30d
)
SELECT
    symbol,
    trade_date,
    daily_return,
    AVG(volume)          OVER w,
    STDDEV_SAMP(volume)  OVER w,
    AVG(daily_return)    OVER w,
    STDDEV_SAMP(daily_return) OVER w
FROM returns
WINDOW w AS (
    PARTITION BY symbol
    ORDER BY trade_date
    ROWS BETWEEN %(window)s PRECEDING AND 1 PRECEDING
)
ON CONFLICT (symbol, trade_date) DO UPDATE SET
    daily_return = EXCLUDED.daily_return,
    vol_mean_30d = EXCLUDED.vol_mean_30d,
    vol_std_30d  = EXCLUDED.vol_std_30d,
    ret_mean_30d = EXCLUDED.ret_mean_30d,
    ret_std_30d  = EXCLUDED.ret_std_30d;
"""


def run(window=BASELINE_WINDOW):
    """Recompute baselines for the whole table.

    Cheap at this scale (40 tickers x 1 year = ~10k rows). If you later scale to
    the full universe, add a WHERE clause to limit to recent dates.
    """
    execute(BASELINE_SQL, {"window": window})
    log.info("baselines recomputed (window=%d)", window)
