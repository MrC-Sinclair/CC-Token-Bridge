import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codebuddy_archive import scan_codebuddy_archive


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.history = self.data / "profile" / "CodeBuddyIDE" / "workspace" / "history"
        self.session = self.history / "project" / "session"
        (self.session / "messages").mkdir(parents=True)
        u = {"inputTokens": 12, "outputTokens": 3, "totalTokens": 15,
             "cacheTokens": 5, "cachedWriteTokens": 1}
        self.req = {"id": "r1", "state": "complete", "startedAt": 1790000000123,
                    "messages": ["user", "aux", "last"], "usage": u}
        self.doc = {"requests": [self.req], "messages": [
            {"id": mid, "role": role} for mid, role in
            [("user", "user"), ("aux", "assistant"), ("last", "assistant")]]}
        self.snapshot = {"inputTokens": 12, "outputTokens": 3, "cachedInputTokens": 5, "cacheWriteTokens": 1}
        self.write()

    def write(self):
        (self.session / "index.json").write_text(json.dumps(self.doc), encoding="utf-8")
        extra = {"requestId": "r1", "agentMessageCount": 1, "statsSnapshot": self.snapshot}
        msg = {"id": "last", "role": "assistant", "extra": json.dumps(extra)}
        (self.session / "messages" / "last.json").write_text(json.dumps(msg), encoding="utf-8")

    def test_mirrored_turns_keep_native_request_count_without_counting_messages(self):
        duplicate = self.history / "project" / "copy"
        shutil.copytree(self.session, duplicate)
        (duplicate / "messages" / "last.json").unlink()
        expected = [{"id": "r1", "timestamp": 1790000000, "input": 6, "output": 3,
                     "cache_read": 5, "cache_write": 1, "message_ids": ("aux", "last"),
                     "request_count": 1}]
        self.assertEqual(list(scan_codebuddy_archive(self.data)), expected)
        self.assertEqual(list(scan_codebuddy_archive(self.history)), expected)

    def test_disagrees_with_snapshot_or_turn_total(self):
        self.snapshot["inputTokens"] = 13
        self.write()
        self.assertIsNone(next(scan_codebuddy_archive(self.data))["request_count"])
        self.req["usage"]["totalTokens"] = 14
        self.write()
        self.assertEqual(list(scan_codebuddy_archive(self.data)), [])

    def test_conflicting_copies_raise_and_incomplete_requests_are_skipped(self):
        conflict = copy.deepcopy(self.req)
        conflict["usage"].update(inputTokens=9, totalTokens=12)
        self.doc["requests"].append(conflict)
        self.write()
        with self.assertRaisesRegex(ValueError, "同一回合"):
            list(scan_codebuddy_archive(self.data))
        conflict["state"] = "streaming"
        self.req["messages"].append("missing")
        self.write()
        self.assertEqual(list(scan_codebuddy_archive(self.data)), [])
