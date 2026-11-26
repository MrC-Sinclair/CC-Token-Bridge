import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_agents_cn import scan_marvis, scan_zcode


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

    def test_zcode_billed_attempts_cache_and_verified_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with closing(sqlite3.connect(root / "db.sqlite")) as db, db:
                db.execute("""CREATE TABLE model_usage(
                    id TEXT PRIMARY KEY, completed_at, model_id, input_tokens, output_tokens,
                    reasoning_tokens, cache_read_input_tokens, cache_creation_input_tokens,
                    provider_total_tokens, computed_total_tokens, status)""")
                db.executemany("INSERT INTO model_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
                    ("one", 1700000000123, "inner", 120, 30, 5, 70, 10, 150, 150, "completed"),
                    ("two", 1700000000321, "outer", 40, 25, 5, 70, 10, None, 150, "cancelled"),
                    ("three", 1700000000321, "unknown", 0, 0, 0, 0, 0, None, 0, "error"),
                    ("four", None, "inner", 120, 30, 5, 70, 10, 150, 150, "running"),
                ])
            rows = list(scan_zcode(root))
            self.assertEqual([r["id"] for r in rows], ["one", "two"])
            for row in rows:
                self.assertEqual([row[k] for k in ("input", "cache_read", "cache_write", "output")],
                                 [40, 70, 10, 30])
                self.assertEqual(row["timestamp"], 1700000000)
            with closing(sqlite3.connect(root / "db.sqlite")) as db, db:
                db.execute("UPDATE model_usage SET provider_total_tokens=999 WHERE id='one'")
            with self.assertRaises(ValueError):
                list(scan_zcode(root))

    def test_missing_db_and_schema_do_not_create_or_change_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                list(scan_marvis(root))
            self.assertFalse((root / "data.db").exists())
            with closing(sqlite3.connect(root / "db.sqlite")) as db, db:
                db.execute("CREATE TABLE some_other_table(id INTEGER)")
            with self.assertRaises(ValueError):
                list(scan_zcode(root))
