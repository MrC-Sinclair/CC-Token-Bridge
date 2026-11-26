import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge_scan


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="自定义 测试 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_codebuddy_filters_duplicates_by_logger_and_waits_for_complete_lines(self):
        stamp = "2026/11/17 11:14:23.721"
        base = (f"[{stamp}] [INFO] [BaseAgent:craft] [turn] notifyStepEnd, "
                'requestId: shared, messageId: one, usage: {"inputTokens":120,'
                '"outputTokens":20,"cacheTokens":80,"cachedWriteTokens":10}, flag: false')
        day = self.root / "2026-11-17"
        day.mkdir()
        log = day / "w.log"
        log.write_bytes((base + "\r\n" + base.replace("BaseAgent:craft", "AgentReporter")
                         + "\n" + base.replace("one", "two")).encode())
        source = {"root": self.root, "kind": "codebuddy"}
        result = list(bridge_scan.scan(source))
        self.assertEqual(result, [dict(id="one", input=30, output=20, cache_read=80,
                                       cache_write=10, model="unknown",
                                       timestamp=int(datetime(2026, 11, 17, 11, 14, 23).timestamp()))])
        with log.open("ab") as writer:
            writer.write(b"\n")
        self.assertEqual([r["id"] for r in bridge_scan.scan(source)], ["one", "two"])

    def test_codebuddy_mirrored_log_and_additional_desktop_event(self):
        extension = self.root / "local" / "CodeBuddyExtension" / "Logs" / "CodeBuddyIDE"
        output = self.root / "roaming" / "CodeBuddy CN" / "logs"
        day = extension / "2026-11-16"
        target = output / "20261116T080000" / "window1" / "exthost" / "Tencent-Cloud.coding-copilot"
        target.mkdir(parents=True)
        day.mkdir(parents=True)
        content = '[BaseAgent:craft] [turn] notifyStepEnd, messageId: m1, usage: {"inputTokens":4,"outputTokens":1}\n'
        (day / "common.log").write_text("[2026/11/16 09:01:02.123] [INFO] " + content, encoding="utf-8")
        line = "2026-11-16 09:01:02.124 [info] " + content
        (target / "trace.log").write_text(line + line.replace("m1", "m2"), encoding="utf-8")
        with patch.dict(os.environ, LOCALAPPDATA=str(self.root / "local"), APPDATA=str(self.root / "roaming")):
            rows = list(bridge_scan.scan({"kind": "codebuddy", "root": extension}))
            self.assertEqual([row["id"] for row in rows], ["m1", "m2"])
            self.assertEqual(sum(row["input"] + row["output"] for row in rows), 10)
            exported = self.root / "imported"
            shutil.copytree(extension, exported)
            self.assertEqual(len(list(bridge_scan.scan({"kind": "codebuddy", "root": exported}))), 1)
            extension.rename(self.root / "old")
            self.assertEqual(len(list(bridge_scan.scan({"kind": "codebuddy", "root": extension}))), 2)

    def test_example_plugin_and_exit_failure(self):
        record = {"request_id": "中文", "time": 1794800400, "model": "测试",
                  "usage": {"prompt": 120, "completion": 20, "cached": 80}}
        (self.root / "test.jsonl").write_text(json.dumps(record)+"\n", encoding="utf-8")
        script = Path(bridge_scan.__file__).resolve().parent / "examples/scan_jsonl.py"
        source = {"kind": "custom", "root": self.root, "script": script}
        self.assertEqual(list(bridge_scan.scan(source)),
                         [dict(id="中文", timestamp=1794800400, input=40, output=20,
                               cache_read=80, cache_write=0, model="测试")])
        failed = self.root / "出错.py"
        failed.write_text('import sys\nprint("{}", flush=True)\nsys.exit("custom failure")',
                          encoding="utf-8")
        source["script"] = failed
        with self.assertRaisesRegex(ValueError, "custom failure"):
            list(bridge_scan.scan(source))

    def test_invalid_event_marks_line(self):
        script = self.root / "invalid.py"
        script.write_text('print(\'{"id":"x","timestamp":1794800400,"input":true,"output":1}\')',
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "第 1 行.*input"):
            list(bridge_scan.scan({"kind": "custom", "root": self.root, "script": script}))

    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell")
    def test_powershell_script_receives_literal_path_and_emits_unicode(self):
        script = self.root / "scanner [x] & sample.ps1"
        script.write_text("param([string]$LogPath)\n"
                          "if (-not (Test-Path -LiteralPath $LogPath)) { throw 'root' }\n"
                          "@{ id = '多语言'; timestamp = 100; input = 6; output = 4 } | ConvertTo-Json -Compress",
                          encoding="utf-8-sig")
        self.assertEqual(list(bridge_scan.scan(
            {"kind": "custom", "root": self.root, "script": script}))[0]["id"], "多语言")


if __name__ == "__main__":
    unittest.main()
