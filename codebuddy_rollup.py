"""Reconcile completed CodeBuddy turns with individual calls in CC's usage tables."""

import hashlib
import json
import sqlite3


ROLLUP_KEY = ("date", "app_type", "provider_id", "model", "request_model", "pricing_model")
ROLLUP_SCHEMA = (
    "usage_daily_rollups",
    {"TEXT": ROLLUP_KEY, "INTEGER": ("request_count", "success_count", "input_tokens",
     "output_tokens", "cache_read_tokens", "cache_creation_tokens", "input_token_semantics")},
    ROLLUP_KEY,
)
ARCHIVE_SOURCE = "token_bridge_codebuddy_archive"
PENDING_SOURCE = "token_bridge_codebuddy_pending"
COUNTERS = ("input", "output", "cache_read", "cache_write")
ZERO = (0, 0, 0, 0)


def has_pending(db):
    return db.execute("SELECT 1 FROM session_usage_dedup WHERE data_source=? LIMIT 1",
                      (PENDING_SOURCE,)).fetchone() is not None


def _remember(db, source, identity, value):
    db.execute("""
        INSERT INTO main.session_usage_dedup(data_source,request_id,semantic_id,has_entry_id)
        VALUES(?,?,?,1) ON CONFLICT(data_source,request_id) DO UPDATE SET semantic_id=excluded.semantic_id
    """, (source, identity, value))


def _signature(turn):
    # Count may become available when an additional copy of the final message appears.
    key = [turn["timestamp"], sorted(turn["message_ids"]), [turn[k] for k in COUNTERS]]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def _coverage(db, turn, members, source, provider, app):
    """Find exact counters for previous detailed imports, including pruned requests."""
    cur = db.cursor()
    cur.row_factory = sqlite3.Row
    existing = cur.execute("""
        SELECT m.value AS member, p.request_id, p.provider_id, p.app_type, p.input_token_semantics,
               p.input_tokens AS fresh, p.output_tokens AS output,
               p.cache_read_tokens AS read, p.cache_creation_tokens AS write,
               i.fresh AS scan_fresh, i.output AS scan_output,
               i.cache_read AS scan_read, i.cache_write AS scan_write, d.semantic_id
        FROM json_each(?) m
        LEFT JOIN main.proxy_request_logs p ON p.request_id=m.value
        LEFT JOIN main.session_usage_dedup d ON d.request_id=m.value AND d.data_source=?
        LEFT JOIN temp.incoming i ON i.id=m.value
        WHERE p.request_id IS NOT NULL OR d.request_id IS NOT NULL
    """, (json.dumps(members), source))
    remaining = [turn[k] for k in COUNTERS]
    covered = set()
    for row in existing:
        if row["request_id"] is not None:
            if (row["provider_id"], row["app_type"], row["input_token_semantics"]) != (provider, app, 2):
                return None
            fields = ("fresh", "output", "read", "write")
        elif row["scan_fresh"] is not None and row["semantic_id"] == row["member"]:
            fields = ("scan_fresh", "scan_output", "scan_read", "scan_write")
        else:
            return None
        remaining = [n - row[k] for n, k in zip(remaining, fields)]
        covered.add(row["member"])
    if min(remaining) < 0:
        return None
    return covered, remaining


def _write_rollup(db, day, app, provider, count, tokens, stats):
    saved = db.execute("""
        INSERT INTO main.usage_daily_rollups(
            date, app_type, provider_id, model, request_model, pricing_model,
            request_count, success_count, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, input_token_semantics
        ) VALUES(?, ?, ?, 'unknown', '', '', ?, ?, ?, ?, ?, ?, 2)
        ON CONFLICT(date, app_type, provider_id, model, request_model, pricing_model)
        DO UPDATE SET request_count=request_count+excluded.request_count,
            success_count=success_count+excluded.success_count,
            input_tokens=input_tokens+excluded.input_tokens,
            output_tokens=output_tokens+excluded.output_tokens,
            cache_read_tokens=cache_read_tokens+excluded.cache_read_tokens,
            cache_creation_tokens=cache_creation_tokens+excluded.cache_creation_tokens
        WHERE input_token_semantics=2
    """, (day, app, provider, count, count, *tokens))
    if saved.rowcount != 1:
        raise ValueError("已有相同日期的 CodeBuddy 汇总使用其他缓存计量方式，无法安全合并。")
    stats["archived_requests"] += count
    stats["archived_tokens"] += sum(tokens)


def _conflict(stats, tokens):
    stats["archive_unresolved_turns"] += 1
    stats["archive_unresolved_tokens"] += tokens


def _add_turn(db, turn, members, source, provider, app, stats):
    totals = sum(turn[k] for k in COUNTERS)
    coverage = _coverage(db, turn, members, source, provider, app)
    if coverage is None:
        _conflict(stats, totals)
        return
    covered, remaining = coverage
    count = turn["request_count"]
    extra = 0 if count is None else count - len(covered)
    if count is not None and (extra < 0 or bool(extra) != any(remaining)):
        _conflict(stats, totals)
        return
    if not any(remaining):
        return
    # Aggregate history identifies the start of the turn, not the time of each call.
    day = db.execute("SELECT date(?,'unixepoch','localtime')", (turn["timestamp"],)).fetchone()[0]
    _write_rollup(db, day, app, provider, extra, remaining, stats)
    _remember(db, ARCHIVE_SOURCE, turn["id"], turn["id"])
    db.executemany("""
        INSERT INTO main.session_usage_dedup(data_source,request_id,semantic_id,has_entry_id)
        VALUES(?,?,?,1) ON CONFLICT(data_source,request_id) DO NOTHING
    """, ((source, key, "codebuddy-archive:" + turn["id"]) for key in members))
    if count is None:
        state = dict(signature=_signature(turn), day=day, remaining=remaining, counted=len(covered),
                     members=sorted(set(members)-covered), total=totals)
        _remember(db, PENDING_SOURCE, turn["id"], json.dumps(state))


def _settle_count(db, turn, identity, state, app, provider, stats):
    """All tokens are already credited. Add only newly corroborated call counts."""
    if turn and _signature(turn) != state["signature"]:
        return False
    new = db.execute("""
        SELECT i.id,i.fresh,i.output,i.cache_read,i.cache_write
        FROM json_each(?) m JOIN temp.incoming i ON i.id=m.value
    """, (json.dumps(state["members"]),)).fetchall()
    left = state["remaining"].copy()
    for _, *tokens in new:
        left = [n - used for n, used in zip(left, tokens)]
    if min(left) < 0:
        return False
    extra = len(new)
    count = turn["request_count"] if turn else None
    if count is not None:
        missing = count - state["counted"] - extra
        if missing < 0 or bool(missing) != any(left):
            return False
        extra += missing
    if extra:
        _write_rollup(db, state["day"], app, provider, extra, ZERO, stats)
    if count is not None or not any(left):
        db.execute("DELETE FROM main.session_usage_dedup WHERE data_source=? AND request_id=?",
                   (PENDING_SOURCE, identity))
        return True
    if new:
        observed = {row[0] for row in new}
        state.update(remaining=left, counted=state["counted"] + extra,
                     members=[m for m in state["members"] if m not in observed])
        _remember(db, PENDING_SOURCE, identity, json.dumps(state))
    return True


def merge_archive(db, turns, stats, app, source, provider, prefix):
    stats.update(archived_requests=0, archived_tokens=0, archive_unresolved_turns=0,
                 archive_unresolved_tokens=0, archive_pending_turns=0, archive_pending_tokens=0)
    claimed = {}
    by_id = {}
    for turn in turns:
        members = [prefix + m for m in turn["message_ids"]]
        by_id[turn["id"]] = turn
        for member in members:
            if claimed.setdefault(member, turn["id"]) != turn["id"]:
                raise ValueError("同一 CodeBuddy 模型消息出现在多个历史回合中。")
        exists = db.execute("""
            SELECT 1 FROM main.session_usage_dedup WHERE data_source=? AND request_id=?
        """, (ARCHIVE_SOURCE, turn["id"])).fetchone()
        if not exists:
            _add_turn(db, turn, members, source, provider, app, stats)
    pending = db.execute("SELECT request_id,semantic_id FROM main.session_usage_dedup WHERE data_source=?",
                         (PENDING_SOURCE,)).fetchall()
    for identity, raw in pending:
        state = json.loads(raw)
        if not _settle_count(db, by_id.get(identity), identity, state, app, provider, stats):
            _conflict(stats, state["total"])
    for (raw,) in db.execute("SELECT semantic_id FROM main.session_usage_dedup WHERE data_source=?",
                             (PENDING_SOURCE,)):
        stats["archive_pending_turns"] += 1
        stats["archive_pending_tokens"] += json.loads(raw)["total"]
    stats["seen"] += stats["archived_requests"]
    stats["inserted"] += stats["archived_requests"]
    stats["tokens"] += stats["archived_tokens"]
