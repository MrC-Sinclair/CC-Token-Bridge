import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_coding_cn import scan_qoder


class QoderTests(unittest.TestCase):
    def test_per_request_compaction_and_assistant_but_no_aggregate_or_placeholder(self):
        usage = dict(input_tokens=110, output_tokens=13, cache_read_input_tokens=70,
                     cache_creation_input_tokens=10)
        timestamp = "2025-01-01T08:00:00+08:00"
        compact = dict(type="system", subtype="compact_boundary", timestamp=timestamp,
                       compactUsage=usage, modelRequestIds=["one"])
        single = dict(type="assistant", timestamp=timestamp,
                      message=dict(role="assistant", model="local-model",
                                   usage=dict(usage, total_tokens=123, request_id="two")))
        def assistant(changes):
            return dict(single, message=dict(single["message"], usage=dict(usage, **changes)))
        hidden = dict(type="assistant", timestamp=timestamp, message=dict(
            role="assistant", usage=dict(input_tokens=0, output_tokens=0, credits=2.5, request_id="zero")))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "example"
            project.mkdir()
            with (project / "session.jsonl").open("wb") as stream:
                rows = [compact, single, hidden, dict(compact, modelRequestIds=["one", "two"]),
                        dict(compact, compactUsage=dict(usage, input_tokens=20)),
                        dict(compact, modelRequestIds=[]), dict(compact, timestamp="2025-01-01"),
                        dict(compact, type="user"), assistant(dict(request_id="ambiguous")),
                        assistant(dict(total_tokens=203, request_id="separate")),
                        assistant(dict(total_tokens=999, request_id="invalid"))]
                for row in rows:
                    stream.write(json.dumps(row).encode() + b"\n")
                stream.write(json.dumps(dict(compact, modelRequestIds=["writing"])).encode())
            result = list(scan_qoder(root))
        self.assertEqual([e["id"] for e in result], ["one", "two", "separate"])
        self.assertEqual([e["input"] for e in result], [30, 30, 110])
        self.assertEqual([e["model"] for e in result], ["unknown", "local-model", "local-model"])
        self.assertEqual(result[0]["timestamp"], 1735689600)
        self.assertEqual([result[0][k] for k in ("input", "output", "cache_read", "cache_write")],
                         [30, 13, 70, 10])
