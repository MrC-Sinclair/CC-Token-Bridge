"""Import per-call token counts into the official CC Switch usage database."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from typing import Iterable
from codebuddy_rollup import has_pending, merge_archive, ROLLUP_SCHEMA


BUILTIN_APPS = frozenset(
    "claude claude-desktop codex gemini grokbuild opencode openclaw hermes "
    "pi mcode kimi dsh agy omp copilot workbuddy codebuddy".split()
)
FRESH_INPUT_SEMANTICS = 2
REQUEST_COLUMNS = {
    "TEXT": ("request_id", "provider_id", "app_type", "model", "request_model",
             "provider_type", "data_source"),
    "INTEGER": ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
                "input_token_semantics", "latency_ms", "status_code", "is_streaming", "created_at"),
}
RECEIPT_COLUMNS = {
    "TEXT": ("data_source", "request_id", "semantic_id"),
    "INTEGER": ("has_entry_id",),
}


def _check_schema(db, archive=False):
    version = db.execute("PRAGMA main.user_version").fetchone()[0]
    if version < 28:
        raise ValueError(f"CC 数据库版本为 {version}，同步需要兼容当前 CC Switch 数据库结构。")
    tables = [
        ("proxy_request_logs", REQUEST_COLUMNS, ("request_id",)),
        ("session_usage_dedup", RECEIPT_COLUMNS, ("data_source", "request_id")),
    ]
    if archive:
        tables.append(ROLLUP_SCHEMA)
    for table, columns, primary_key in tables:
        info = db.execute(f"PRAGMA main.table_info({table})").fetchall()
        actual = {row[1]: row[2].upper() for row in info}
        key = tuple(row[1] for row in sorted(info, key=lambda row: row[5]) if row[5])
        supplied = {name for names in columns.values() for name in names}
        missing_value = any(
            name not in supplied and required and default is None
            for _, name, _, required, default, _ in info
        )
        if missing_value or key != primary_key or any(
            actual.get(name) != dtype for dtype, names in columns.items() for name in names
        ):
            raise ValueError(f"CC 数据库结构已变化，无法向 {table} 同步用量。")


def _text(value, field):
    if not isinstance(value, str) or not value.strip() or not value.isprintable():
        raise ValueError(f"{field} 应为非空的可打印文本。")
    return value.strip()


def _identifiers(source):
    if source["kind"] == "codebuddy":
        # Match the previous desktop patch, including receipts for pruned detail rows.
        return "codebuddy", "codebuddy_session", "_codebuddy_session", "codebuddy-ide:"
    if source["kind"] not in ("custom", "desktop"):
        raise ValueError("无法识别扫描器类型。")
    app = _text(source["app_type"], "CC 应用名")
    if app.lower() in BUILTIN_APPS:
        app = app.lower()
    # An app keeps its event IDs when its configuration is removed and added again.
    namespace = sha256(app.encode("utf-8")).hexdigest()
    # Official CC's provider picker reads stored names, including custom apps.
    return app, "token_bridge_session", f"{app} (Token Bridge)", f"token-bridge:{namespace}:"


def _number(value, field, upper):
    if type(value) is not int or not 0 <= value <= upper:
        raise ValueError(f"{field} 应为 0 至 {upper} 之间的整数。")
    return value


def _stage(rows, prefix, stats):
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 条用量应为 JSON 对象。")
        try:
            event_id = _text(row.get("id"), "id")
            # CC stores whole Unix seconds and uses Rust u32 for per-request token counts.
            timestamp = _number(row.get("timestamp"), "timestamp（Unix 秒）", 253402300799)
            model = _text(row.get("model") or "unknown", "model")
            counts = (
                _number(row.get("input"), "input", 2**32 - 1),
                _number(row.get("output"), "output", 2**32 - 1),
                _number(row.get("cache_read", 0), "cache_read", 2**32 - 1),
                _number(row.get("cache_write", 0), "cache_write", 2**32 - 1),
            )
        except ValueError as exc:
            raise ValueError(f"第 {index} 条用量无效。{exc}") from exc
        stats["seen"] += 1
        stats["tokens"] += sum(counts)
        yield (prefix + event_id, timestamp, model, *counts)


def import_rows(db_path: Path, source: dict, rows: Iterable[dict], archives=()) -> dict:
    """Atomically insert new requests and durable receipts. Counts cover the given scan."""
    app, data_source, provider, prefix = _identifiers(source)
    stats = {"seen": 0, "inserted": 0, "skipped": 0, "tokens": 0}
    turns = list(archives)
    if turns and source["kind"] != "codebuddy":
        raise ValueError("此历史汇总仅适用于 CodeBuddy IDE。")
    path = Path(db_path).resolve(strict=True)
    uri = path.as_uri() + "?mode=rw"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        _check_schema(db, bool(turns))
        # Only TEMP is written while the generator runs. A late scanner failure leaves
        # the main DB untouched; TEMP tables are private and discarded on disconnect.
        db.execute("""
            CREATE TEMP TABLE incoming(
                id TEXT PRIMARY KEY, ts INTEGER, model TEXT, fresh INTEGER,
                output INTEGER, cache_read INTEGER, cache_write INTEGER
            ) WITHOUT ROWID
        """)
        with db:
            db.executemany(
                "INSERT INTO temp.incoming VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                _stage(rows, prefix, stats),
            )
        if not stats["seen"] and not turns and not (source["kind"] == "codebuddy" and has_pending(db)):
            return stats
        with db:
            db.execute("BEGIN IMMEDIATE")
            archive = bool(turns) or (source["kind"] == "codebuddy" and has_pending(db))
            _check_schema(db, archive)
            saved = db.execute("""
                INSERT INTO main.proxy_request_logs(
                    request_id, provider_id, app_type, model, request_model,
                    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                    input_token_semantics, latency_ms, status_code, is_streaming,
                    created_at, provider_type, data_source
                )
                SELECT i.id, :provider, :app, i.model, i.model, i.fresh, i.output,
                       i.cache_read, i.cache_write, :semantics, 0, 200, :streaming,
                       i.ts, :source, :source
                FROM temp.incoming i
                WHERE NOT EXISTS (
                    SELECT 1 FROM main.session_usage_dedup d
                    WHERE d.data_source = :source AND d.request_id = i.id
                )
                ON CONFLICT(request_id) DO NOTHING
            """, {"provider": provider, "app": app, "source": data_source,
                  "semantics": FRESH_INPUT_SEMANTICS, "streaming": int(source["kind"] == "codebuddy")})
            stats["inserted"] = saved.rowcount
            # Also heal a missing receipt for an already-present detailed request.
            db.execute("""
                INSERT INTO main.session_usage_dedup(data_source, request_id, semantic_id, has_entry_id)
                SELECT ?, id, id, 1 FROM temp.incoming WHERE true
                ON CONFLICT(data_source, request_id) DO NOTHING
            """, (data_source,))
            if archive:
                merge_archive(db, turns, stats, app, data_source, provider, prefix)
    stats["skipped"] = stats["seen"] - stats["inserted"]
    return stats
