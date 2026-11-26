import concurrent.futures
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge_db import import_rows


SCHEMA = """
PRAGMA user_version = 28;
CREATE TABLE proxy_request_logs (
    request_id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, app_type TEXT NOT NULL, model TEXT NOT NULL,
    request_model TEXT, pricing_model TEXT, input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens INTEGER NOT NULL DEFAULT 0, input_token_semantics INTEGER NOT NULL DEFAULT 0,
    input_cost_usd TEXT NOT NULL DEFAULT '0', output_cost_usd TEXT NOT NULL DEFAULT '0',
    cache_read_cost_usd TEXT NOT NULL DEFAULT '0', cache_creation_cost_usd TEXT NOT NULL DEFAULT '0',
    total_cost_usd TEXT NOT NULL DEFAULT '0', latency_ms INTEGER NOT NULL, first_token_ms INTEGER,
    duration_ms INTEGER, status_code INTEGER NOT NULL, error_message TEXT, session_id TEXT,
    provider_type TEXT, is_streaming INTEGER NOT NULL DEFAULT 0, cost_multiplier TEXT NOT NULL DEFAULT '1.0',
    created_at INTEGER NOT NULL, data_source TEXT NOT NULL DEFAULT 'proxy'
);
CREATE TABLE session_usage_dedup (
    data_source TEXT NOT NULL, request_id TEXT NOT NULL, semantic_id TEXT NOT NULL,
    has_entry_id INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(data_source, request_id)
);
"""


class ImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cc-switch.db"
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript(SCHEMA)
        self.sample = {"id": "msg-1", "timestamp": 1730000000, "input": 10, "output": 20,
                       "cache_read": 30, "cache_write": 1, "model": "example"}
        self.source = {"kind": "custom", "id": "first", "app_type": "Qoder"}

    def run_import(self, rows, source=None):
        return import_rows(self.path, source or self.source, rows)

    def test_app_identity_survives_reconfiguration_and_pruning(self):
        self.assertEqual(self.run_import([self.sample, self.sample]),
                         {"seen": 2, "inserted": 1, "skipped": 1, "tokens": 122})
        self.assertEqual(self.db.execute(
            "SELECT app_type, provider_id, model, created_at, input_tokens, output_tokens, cache_read_tokens,"
            " cache_creation_tokens, input_token_semantics FROM proxy_request_logs"
        ).fetchone(), ("Qoder", "Qoder (Token Bridge)", "example", 1730000000, 10, 20, 30, 1, 2))
        self.db.execute("DELETE FROM proxy_request_logs")
        self.db.commit()
        self.assertEqual(self.run_import([self.sample], dict(self.source, id="new"))["inserted"], 0)
        self.assertEqual(self.run_import([self.sample], dict(self.source, app_type="Trae"))["inserted"], 1)

    def test_previous_codebuddy_patch_receipts_and_existing_detail(self):
        source = {"kind": "codebuddy"}
        self.db.execute("INSERT INTO session_usage_dedup VALUES("
                        "'codebuddy_session','codebuddy-ide:msg-1','codebuddy-ide:msg-1',1)")
        self.db.execute("INSERT INTO proxy_request_logs(request_id,provider_id,app_type,model,"
                        "latency_ms,status_code,created_at) VALUES("
                        "'codebuddy-ide:msg-2','_codebuddy_session','codebuddy','unknown',0,200,1)")
        self.db.commit()
        rows = [dict(self.sample, id=f"msg-{i}") for i in (1, 2, 3)]
        self.assertEqual(self.run_import(rows, source)["inserted"], 1)
        self.assertEqual(self.run_import(rows, source)["inserted"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM session_usage_dedup").fetchone()[0], 3)

    def test_scanning_does_not_block_main_writer_and_a_partial_scan_cannot_commit(self):
        def failed_scan():
            yield self.sample
            with closing(sqlite3.connect(self.path, timeout=0)) as other:
                other.execute("BEGIN IMMEDIATE")
                other.rollback()
            raise RuntimeError("script returned an error")

        with self.assertRaisesRegex(RuntimeError, "script returned an error"):
            self.run_import(failed_scan())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM proxy_request_logs").fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, "output"):
            self.run_import([self.sample, dict(self.sample, output=-1)])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM session_usage_dedup").fetchone()[0], 0)

    def test_concurrent_imports(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            result = list(pool.map(self.run_import, [[self.sample]] * 3))
        self.assertEqual(sum(r["inserted"] for r in result), 1)

    def test_future_additive_schema_and_an_incompatible_schema(self):
        self.db.execute("ALTER TABLE proxy_request_logs ADD COLUMN extra TEXT")
        self.db.execute("PRAGMA user_version=29")
        self.assertEqual(self.run_import([self.sample])["inserted"], 1)
        self.db.execute("ALTER TABLE session_usage_dedup RENAME COLUMN semantic_id TO changed")
        with self.assertRaisesRegex(ValueError, "session_usage_dedup"):
            self.run_import([dict(self.sample, id="new")])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM proxy_request_logs").fetchone()[0], 1)

    def test_failed_main_write_rolls_back_the_previous_table(self):
        self.db.execute("""
            CREATE TRIGGER reject BEFORE INSERT ON session_usage_dedup
            BEGIN SELECT RAISE(ABORT, 'incompatible receipt insert'); END
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_import([self.sample])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM proxy_request_logs").fetchone()[0], 0)

    def create_rollups(self):
        self.db.execute("""
            CREATE TABLE usage_daily_rollups(
                date TEXT, app_type TEXT, provider_id TEXT, model TEXT, request_model TEXT, pricing_model TEXT,
                request_count INTEGER, success_count INTEGER, input_tokens INTEGER, output_tokens INTEGER,
                cache_read_tokens INTEGER, cache_creation_tokens INTEGER, input_token_semantics INTEGER,
                PRIMARY KEY(date,app_type,provider_id,model,request_model,pricing_model))
        """)

    def archive_turn(self):
        return dict(id="turn", timestamp=self.sample["timestamp"], input=80, output=100,
                    cache_read=120, cache_write=5, message_ids=["msg-1", "msg-2", "aux"],
                    request_count=2)

    def test_archive_subtracts_prior_calls_and_reserves_future_member_ids(self):
        self.create_rollups()
        source = {"kind": "codebuddy"}
        turn = self.archive_turn()
        result = import_rows(self.path, source, [self.sample], [turn])
        self.assertEqual((result["inserted"], result["archived_requests"], result["archived_tokens"]),
                         (2, 1, 244))
        self.assertEqual(self.db.execute("""
            SELECT request_count,input_tokens,output_tokens,cache_read_tokens,cache_creation_tokens
            FROM usage_daily_rollups
        """).fetchone(), (1, 70, 80, 90, 4))
        later = dict(self.sample, id="msg-2", input=70, output=80, cache_read=90, cache_write=4)
        self.assertEqual(import_rows(self.path, source, [self.sample, later], [turn])["inserted"], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM proxy_request_logs").fetchone()[0], 1)

    def test_pruned_detail_can_be_reconciled_only_with_its_original_counters(self):
        self.create_rollups()
        source = {"kind": "codebuddy"}
        import_rows(self.path, source, [self.sample])
        self.db.execute("DELETE FROM proxy_request_logs")
        self.db.commit()
        turn = self.archive_turn()
        uncertain = import_rows(self.path, source, [], [turn])
        self.assertEqual((uncertain["inserted"], uncertain["archive_unresolved_turns"]), (0, 1))
        recovered = import_rows(self.path, source, [self.sample], [turn])
        self.assertEqual((recovered["archived_requests"], recovered["archived_tokens"]), (1, 244))

    def test_unknown_count_can_be_revisited_and_known_detail_is_never_counted_again(self):
        self.create_rollups()
        source = {"kind": "codebuddy"}
        turn = self.archive_turn()
        uncertain = import_rows(self.path, source, [], [dict(turn, request_count=None)])
        self.assertEqual((uncertain["inserted"], uncertain["archive_pending_tokens"],
                          uncertain["archived_tokens"], uncertain["archive_unresolved_tokens"]),
                         (0, 305, 305, 0))
        repeat = import_rows(self.path, source, [], [dict(turn, request_count=None)])
        self.assertEqual((repeat["archived_tokens"], repeat["archive_pending_turns"]), (0, 1))
        confirmed = import_rows(self.path, source, [], [turn])
        self.assertEqual((confirmed["archived_requests"], confirmed["archived_tokens"],
                          confirmed["archive_pending_turns"]), (2, 0, 0))
        self.assertEqual(import_rows(self.path, source, [self.sample], [turn])["inserted"], 0)

    def test_partial_new_details_add_counts_without_requiring_archive_to_still_exist(self):
        self.create_rollups()
        source = {"kind": "codebuddy"}
        turn = dict(self.archive_turn(), request_count=None)
        import_rows(self.path, source, [], [turn])
        found = import_rows(self.path, source, [self.sample])
        self.assertEqual((found["archived_requests"], found["archived_tokens"], found["archive_pending_turns"]),
                         (1, 0, 1))
        self.assertEqual(import_rows(self.path, source, [self.sample])["inserted"], 0)
        other = dict(self.sample, id="msg-2", input=70, output=80, cache_read=90, cache_write=4)
        last = import_rows(self.path, source, [self.sample, other])
        self.assertEqual((last["inserted"], last["archived_tokens"], last["archive_pending_turns"]), (1, 0, 0))
        self.assertEqual(self.db.execute("""
            SELECT request_count,input_tokens+output_tokens+cache_read_tokens+cache_creation_tokens
            FROM usage_daily_rollups
        """).fetchone(), (2, 305))

    def test_unknown_count_subtracts_preexisting_details_and_conflicts_do_not_adjust_counts(self):
        self.create_rollups()
        source = {"kind": "codebuddy"}
        turn = dict(self.archive_turn(), request_count=None)
        first = import_rows(self.path, source, [self.sample], [turn])
        self.assertEqual((first["inserted"], first["archived_tokens"]), (1, 244))
        mismatch = import_rows(self.path, source, [], [dict(turn, input=81, request_count=2)])
        self.assertEqual((mismatch["archive_unresolved_turns"], mismatch["inserted"]), (1, 0))
        bad_detail = dict(self.sample, id="msg-2", input=1000)
        self.assertEqual(import_rows(self.path, source, [bad_detail])["archive_unresolved_turns"], 1)
        correct = import_rows(self.path, source, [], [dict(turn, request_count=2)])
        self.assertEqual((correct["inserted"], correct["archived_tokens"], correct["archive_pending_turns"]),
                         (1, 0, 0))

    def test_archive_conflict_rolls_back_detail_and_aggregate(self):
        self.create_rollups()
        turn = self.archive_turn()
        with self.assertRaisesRegex(ValueError, "多个历史回合"):
            import_rows(self.path, {"kind": "codebuddy"}, [self.sample],
                        [turn, dict(turn, id="other")])
        for table in ("usage_daily_rollups", "session_usage_dedup", "proxy_request_logs"):
            self.assertEqual(self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
