import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_office_cn import scan_qianwen


class QianwenOfficeTests(unittest.TestCase):
    def test_completed_custom_provider_call_and_masked_managed_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            logs = root / "sessions" / "project" / "session" / "segments"
            logs.mkdir(parents=True)
            data = dict(input_tokens=56, output_tokens=20, cache_read_input_tokens=80,
                        cache_creation_input_tokens=10, model="example", provider="custom")
            event = dict(type="model.response.completed", ts="2026-11-04T09:00:00.100+08:00",
                         request_id="req-1", data=data)
            masked = dict.fromkeys(("input_tokens", "output_tokens", "cache_read_input_tokens",
                                    "cache_creation_input_tokens"), 0)
            cases = [dict(event, type="turn.finished"), dict(event, type="model.request.started"),
                     dict(event, data=dict(masked, provider="qoder")), dict(event, data=dict(data, input_tokens=True)),
                     dict(event, ts="2026-11-04T09:00:00.100"), event]
            body = "\n".join(json.dumps(item) for item in cases) + "\n" + json.dumps(event)
            (logs / "part.jsonl").write_text(body, encoding="utf-8")
            self.assertEqual(list(scan_qianwen(root)), [dict(id="req-1", timestamp=1793754000,
                             input=56, output=20, cache_read=80, cache_write=10, model="example")])
