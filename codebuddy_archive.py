"""Read completed CodeBuddy IDE turn totals from its per-profile history."""

import json
import os
import re
from pathlib import Path


def _history_paths(root):
    root = Path(root).resolve()
    # Electron can create archive paths beyond MAX_PATH. On Windows some Python
    # directory and file operations otherwise silently omit these message files.
    if os.name == "nt":
        name = str(root)
        if not name.startswith("\\\\?\\"):
            root = Path("\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name)
    if root.name.casefold() == "history":
        return root.glob("*/*/index.json")
    return root.glob("*/CodeBuddyIDE/*/history/*/*/index.json")


def _read_json(path):
    try:
        return json.loads(path.read_bytes())
    except (FileNotFoundError, UnicodeError, ValueError):
        return None


def _request_count(path, message_id, request_id, totals):
    # A turn can include auxiliary assistant messages. CodeBuddy records its
    # actual model request count on the final message with a cumulative snapshot.
    message = _read_json(path.parent / "messages" / (message_id + ".json"))
    if not isinstance(message, dict) or message.get("id") != message_id or message.get("role") != "assistant":
        return None
    extra = message.get("extra")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except ValueError:
            return None
    if not isinstance(extra, dict) or extra.get("requestId") != request_id:
        return None
    snapshot, value = extra.get("statsSnapshot"), extra.get("agentMessageCount")
    if not isinstance(snapshot, dict) or type(value) is not int or value < 1:
        return None
    fields = ("inputTokens", "outputTokens", "cachedInputTokens", "cacheWriteTokens")
    if tuple(snapshot.get(k, 0) for k in fields) == totals:
        return value
    return None


def _archive_turn(path, request, roles):
    if not isinstance(request, dict) or request.get("state") != "complete":
        return None
    identity, millis = request.get("id"), request.get("startedAt")
    if not isinstance(identity, str) or not identity or type(millis) is not int or not 0 <= millis < 253402300800000:
        return None
    usage, members = request.get("usage"), request.get("messages")
    if not isinstance(usage, dict) or not isinstance(members, list) or not members:
        return None
    # Every assistant identity is required for deduplication against current and
    # future detail logs, even when this member emitted no independent counters.
    if any(not isinstance(m, str) or m not in roles for m in members):
        return None
    ids = tuple(m for m in members if roles[m] == "assistant")
    if not ids or len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", m) for m in ids):
        return None
    prompt, output = usage.get("inputTokens"), usage.get("outputTokens")
    cached, written = usage.get("cacheTokens", 0), usage.get("cachedWriteTokens", 0)
    counts = prompt, output, cached, written
    if any(type(n) is not int or n < 0 for n in counts):
        return None
    total = usage.get("totalTokens")
    if type(total) is not int or total <= 0 or prompt + output != total or prompt < cached + written:
        return None
    return dict(id=identity, timestamp=millis // 1000, input=prompt - cached - written,
                output=output, cache_read=cached, cache_write=written, message_ids=ids,
                request_count=_request_count(path, ids[-1], identity, counts))


def scan_codebuddy_archive(data_root):
    """Yield per-turn totals and all member assistant IDs, keyed by native turn ID."""
    turns = {}
    for path in sorted(_history_paths(data_root)):
        doc = _read_json(path)
        if not isinstance(doc, dict) or not isinstance(doc.get("messages"), list) or not isinstance(doc.get("requests"), list):
            continue
        roles = {m["id"]: m.get("role") for m in doc["messages"]
                 if isinstance(m, dict) and isinstance(m.get("id"), str)}
        for request in doc["requests"]:
            row = _archive_turn(path, request, roles)
            if row is None:
                continue
            prev = turns.get(row["id"])
            if prev is not None and prev != row:
                # Copies can retain the same index but only one copy of the
                # final message. Keep its verified native count when available.
                if any(prev[key] != row[key] for key in row if key != "request_count"):
                    raise ValueError("CodeBuddy 历史中同一回合出现不同的计量记录。")
                if row["request_count"] is None:
                    continue
                if prev["request_count"] is not None:
                    raise ValueError("CodeBuddy 历史中同一回合出现不同的模型请求数。")
            turns[row["id"]] = row
    yield from turns.values()
