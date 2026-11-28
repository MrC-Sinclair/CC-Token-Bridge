import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_agents_cn import scan_marvis


class AgentUsageTest(unittest.TestCase):
    def test_marvis_usage_rows_with_one_response_and_copied_db(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with closing(sqlite3.connect(root / "data.db")) as db, db:
                db.execute("""CREATE TABLE llm_token_usage(
                    id INTEGER PRIMARY KEY, conversation_id, response_id, model_id,
                    created_at, input_tokens, output_tokens, cached_tokens, total_tokens,
                    thinking_tokens)""")
                db.executemany("INSERT INTO llm_token_usage VALUES (?,?,?,?,?,?,?,?,?,?)", [
                    (i, "chat1", "answer1", "modelA", "2026-11-05T13:30:00.000802",
                     120, 30, 80, 150, 15) for i in (1, 2)])
            rows = list(scan_marvis(root))
            self.assertEqual(len({r["id"] for r in rows}), 2)
            self.assertEqual([rows[0][k] for k in ("input", "cache_read", "output")], [40, 80, 30])
            self.assertEqual(rows[0]["timestamp"], int(datetime(2026, 11, 5, 13, 30).timestamp()))
            copy = root / "copy"
            copy.mkdir()
            shutil.copy2(root / "data.db", copy / "data.db")
            self.assertEqual(list(scan_marvis(copy)), rows)

    def test_missing_db_does_not_create_the_table(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                list(scan_marvis(Path(folder)))
            self.assertFalse((Path(folder) / "data.db").exists())
