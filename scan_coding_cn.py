"""Read per-request usage recorded in Qoder CN project transcripts."""

import json
from datetime import datetime
from pathlib import Path


def _qoder_event(record):
    if not isinstance(record, dict):
        return None
    if record.get("type") == "assistant":
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            return None
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return None
        request_id = usage.get("request_id")
        model = message.get("model")
    elif record.get("type") == "system" and record.get("subtype") == "compact_boundary":
        request_ids = record.get("modelRequestIds")
        # A compaction can cover several model requests with one aggregate.
        if not isinstance(request_ids, list) or len(request_ids) != 1:
            return None
        request_id = request_ids[0]
        usage = record.get("compactUsage")
        model = None
    else:
        return None
    if not isinstance(usage, dict) or not isinstance(request_id, str) or not request_id.strip():
        return None
    prompt = usage.get("input_tokens")
    output = usage.get("output_tokens")
    read = usage.get("cache_read_input_tokens", 0)
    write = usage.get("cache_creation_input_tokens", 0)
    if any(type(n) is not int or not 0 <= n < 2**32 for n in (prompt, output, read, write)):
        return None
    cached = read + write
    if prompt == output == cached == 0:
        return None
    fresh = prompt
    if record["type"] == "system":
        # CN compaction's raw input is the whole prompt, including cached input.
        fresh = prompt - cached
    elif cached:
        # Regular assistant transcripts can use SDK-specific counting schemes.
        # Only an explicit total distinguishes inclusive from exclusive input.
        total = usage.get("total_tokens")
        if type(total) is not int:
            return None
        if total == prompt + output:
            fresh = prompt - cached
        elif total != prompt + cached + output:
            return None
    if fresh < 0:
        return None
    try:
        stamp = datetime.fromisoformat(record["timestamp"])
        if stamp.tzinfo is None:
            return None
        timestamp = int(stamp.timestamp())
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return None
    model = model if isinstance(model, str) and model.strip() else "unknown"
    return dict(id=request_id.strip(), timestamp=timestamp, model=model, input=fresh,
                output=output, cache_read=read, cache_write=write)


def scan_qoder(root: Path):
    """Scan the project's local JSONL sessions, excluding work files and runtime summaries."""
    for path in sorted(Path(root).glob("*/*.jsonl")):
        try:
            with path.open("rb") as stream:
                for raw in stream:
                    if not raw.endswith(b"\n"):
                        break  # An active session can still be appending this line.
                    if b'"usage"' not in raw and b'"compactUsage"' not in raw:
                        continue
                    try:
                        event = _qoder_event(json.loads(raw))
                    except (UnicodeError, ValueError):
                        continue
                    if event is not None:
                        yield event
        except FileNotFoundError:
            continue
