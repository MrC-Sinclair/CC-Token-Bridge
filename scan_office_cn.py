"""Read exact per-request usage from Qianwen Office session diagnostics."""

import json
from datetime import datetime
from pathlib import Path


def _completed_usage(event):
    if not isinstance(event, dict) or event.get("type") != "model.response.completed":
        return None
    data = event.get("data")
    identity, timestamp = event.get("request_id"), event.get("ts")
    if not isinstance(data, dict) or not isinstance(identity, str) or not identity.strip():
        return None
    if not isinstance(timestamp, str):
        return None
    try:
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if when.tzinfo is None:
            return None
        stamp = int(when.timestamp())
    except (ValueError, OSError, OverflowError):
        return None
    # The engine adds these four disjoint fields to compute the request total.
    # QwenWorkCN masks them all to zero for its managed provider.
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    counts = [data.get(field) for field in fields]
    if any(type(value) is not int or not 0 <= value <= 2**32 - 1 for value in counts):
        return None
    if not any(counts) or stamp < 0:
        return None
    fresh, output, cached, written = counts
    return dict(id=identity.strip(), timestamp=stamp, model=data.get("model") or "unknown",
                input=fresh, output=output, cache_read=cached, cache_write=written)


def scan_qianwen(root: Path):
    """Scan ~/.qwenworkcn/logs. Skip summaries and masked provider counters."""
    for path in sorted((root / "sessions").glob("*/*/segments/*.jsonl")):
        try:
            with path.open("rb") as lines:
                for raw in lines:
                    if not raw.endswith(b"\n") or b"model.response.completed" not in raw:
                        continue
                    try:
                        event = json.loads(raw)
                    except (UnicodeError, ValueError):
                        continue
                    row = _completed_usage(event)
                    if row is not None:
                        yield row
        except FileNotFoundError:
            # The native client can rotate its diagnostics during a scan.
            continue
