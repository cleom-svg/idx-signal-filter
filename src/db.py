"""Database access. SHARED FILE — agree changes with the team before editing.

Deliberately raw SQL, no ORM. The queries are simple and an ORM would cost
more time than it saves on an 18-day build.
"""
import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from config import DATABASE_URL

log = logging.getLogger(__name__)


@contextmanager
def get_conn():
    """Connection that commits on success and rolls back on any exception."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)


def query(sql, params=None):
    """Return list of dicts."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def upsert_many(table, columns, rows, conflict_cols, update_cols=None):
    """Bulk INSERT ... ON CONFLICT DO UPDATE.

    This is what makes re-running a day safe. Every write path in this project
    goes through here or an equivalent ON CONFLICT clause.
    """
    if not rows:
        return 0

    update_cols = update_cols or [c for c in columns if c not in conflict_cols]
    col_list = ", ".join(columns)
    conflict = ", ".join(conflict_cols)

    if update_cols:
        setters = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        action = f"DO UPDATE SET {setters}"
    else:
        action = "DO NOTHING"

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES %s "
        f"ON CONFLICT ({conflict}) {action}"
    )

    values = [tuple(r.get(c) for c in columns) for r in rows]

    with get_conn() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, values, page_size=500)
    log.info("upserted %d rows into %s", len(values), table)
    return len(values)


def init_schema(path="sql/schema.sql"):
    with open(path) as f:
        execute(f.read())
    log.info("schema applied")
