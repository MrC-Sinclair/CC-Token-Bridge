"""Example scanner. Read application request logs and print usage events.

Example input line:
{"request_id":"a1","time":1794800400,"model":"demo","usage":{"prompt":120,"completion":20,"cached":80}}
"""

import json
import sys
from pathlib import Path


def scan(root):
    for path in sorted(root.rglob("*.jsonl")):
        with path.open("rb") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.endswith(b"\n"):
                    break  # An active writer may still be writing the event.
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    if "usage" not in item:
                        continue
                    usage = item["usage"]
                    cached = usage.get("cached", 0)
                    yield {
                        "id": item["request_id"],
                        "timestamp": item["time"],
                        "input": usage["prompt"] - cached,
                        "output": usage["completion"],
                        "cache_read": cached,
                        "model": item.get("model", "unknown"),
                    }
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError(f"{path.name} line {line_number}: {exc}") from exc


if __name__ == "__main__":
    for row in scan(Path(sys.argv[1])):
        print(json.dumps(row, ensure_ascii=False))
