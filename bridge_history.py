"""Read persisted CC usage, including archived days, for configured providers."""

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import NamedTuple

from bridge_db import _identifiers


class History(NamedTuple):
    requests: int
    tokens: int
    first_day: str | None
    last_day: str | None


class Day(NamedTuple):
    day: str
    requests: int
    tokens: int


# Details and compacted days share the same fresh-input convention.
RECORDS = """
        WITH records(n, day, tokens) AS (
            SELECT 1, date(created_at, 'unixepoch', 'localtime'),
                   input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
            FROM proxy_request_logs WHERE app_type=:app AND provider_id=:provider
            UNION ALL
            SELECT request_count, date,
                   input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
            FROM usage_daily_rollups WHERE app_type=:app AND provider_id=:provider
        )
    """


def _params(source):
    app, _, provider, _ = _identifiers(source)
    return {"app": app, "provider": provider}


def read_history(path, sources):
    """Read all source totals from one SQLite snapshot without running any scanner."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    query = RECORDS + "SELECT COALESCE(SUM(n),0), COALESCE(SUM(tokens),0), MIN(day), MAX(day) FROM records"
    totals = {}
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
        db.execute("BEGIN")
        for source in sources:
            totals[source["id"]] = History(*db.execute(query, _params(source)).fetchone())
    return totals


def read_days(path, source):
    """Return days with saved usage in ascending local-date order."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    query = RECORDS + "SELECT day, SUM(n), SUM(tokens) FROM records GROUP BY day ORDER BY day"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
        return [Day(*row) for row in db.execute(query, _params(source))]
