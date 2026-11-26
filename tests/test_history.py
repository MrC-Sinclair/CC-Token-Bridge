import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_history import Day, History, read_days, read_history


class HistoryTests(unittest.TestCase):
    def test_details_rollups_and_unavailable_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "用户 #2.db"
            common = "app_type,provider_id,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens"
            with closing(sqlite3.connect(path)) as db:
                db.execute(f"CREATE TABLE proxy_request_logs({common},created_at)")
                db.execute(f"CREATE TABLE usage_daily_rollups({common},date,request_count)")
                db.execute("INSERT INTO proxy_request_logs VALUES('Marvis','Marvis (Token Bridge)',1,2,3,4,1800000000)")
                db.execute("INSERT INTO proxy_request_logs VALUES('Marvis','other',101,102,103,104,1800000000)")
                db.execute("INSERT INTO usage_daily_rollups VALUES('Marvis','Marvis (Token Bridge)',10,20,30,40,'2025-12-31',5)")
                db.execute("INSERT INTO usage_daily_rollups VALUES('codebuddy','_codebuddy_session',10,2,3,1,'2025-12-31',3)")
                last_day = db.execute("SELECT date(1800000000,'unixepoch','localtime')").fetchone()[0]
                db.commit()
            rows = read_history(path, [
                {"id": "one", "kind": "custom", "app_type": "Marvis"},
                {"id": "two", "kind": "codebuddy"},
                {"id": "three", "kind": "desktop", "app_type": "TRAE CN"},
            ])
            self.assertEqual(rows["one"], History(6, 110, "2025-12-31", last_day))
            self.assertEqual(rows["two"], History(3, 16, "2025-12-31", "2025-12-31"))
            self.assertEqual(rows["three"], History(0, 0, None, None))
            source = {"kind": "custom", "app_type": "Marvis"}
            self.assertEqual(read_days(path, source), [Day("2025-12-31", 5, 100), Day(last_day, 1, 10)])
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("INSERT INTO usage_daily_rollups VALUES('Marvis','Marvis (Token Bridge)',1,1,1,1,?,2)", (last_day,))
            self.assertEqual(read_days(path, source)[-1], Day(last_day, 3, 14))

    def test_missing_and_incompatible_databases_raise_without_creating(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "missing.db"
            src = [{"id": "1", "kind": "codebuddy"}]
            with self.assertRaises(sqlite3.Error):
                read_history(path, src)
            self.assertFalse(path.exists())
            path.touch()
            with self.assertRaises(sqlite3.Error):
                read_history(path, src)
