import json
import io
import os
import socket
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pipeline


class FakeResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def getcode(self):
        return self.status

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def close(self):
        pass


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.db_path = Path(self.temp.name) / "test.db"
        pipeline.init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path)
        try:
            yield db
        finally:
            db.close()

    def test_configuration_has_24_bqs_and_exact_bq002_query(self):
        rows = pipeline.load_queries()
        self.assertEqual(len(rows), 24)
        self.assertEqual(len({row["bq_id"] for row in rows}), 24)
        bq002 = next(row for row in rows if row["bq_id"] == "BQ-002")
        self.assertEqual(bq002["query"], "opiniones área de TI Liverpool")

    def test_normalization_removes_tracking_and_sorts_parameters(self):
        first = pipeline.normalize_url("HTTPS://Example.com/path/?z=2&utm_source=x&a=1#section")
        second = pipeline.normalize_url("https://example.com/path?a=1&z=2&fbclid=y")
        self.assertEqual(first, "https://example.com/path?a=1&z=2")
        self.assertEqual(first, second)

    def fixture_path(self):
        return Path(__file__).resolve().parents[1] / "fixtures" / "brave_bq002.json"

    def add_pending(self, position, url):
        with self.connect() as db:
            run_id = db.execute(
                "INSERT INTO runs(started_at,status,provider,country,language,config_version) "
                "VALUES('now','completed','brave','mx','es','test')"
            ).lastrowid
            result_id = db.execute("""
                INSERT INTO search_results
                (bq_id,run_id,company,query_literal,position,provider,searched_at,country,
                 url_original,url_norm,title,snippet)
                VALUES('BQ-002',?, 'Liverpool','opiniones área de TI Liverpool',?,
                       'brave','now','mx',?,?, 'título', 'snippet original')
            """, (run_id, position, url, f"{url}#{position}")).lastrowid
            db.commit()
        return result_id

    @staticmethod
    def public_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    def test_retrieval_persistence_rerun_and_repeatable_migration(self):
        limited_id = self.add_pending(1, "https://limited.example/page")
        success_id = self.add_pending(2, "https://public.example/page")
        with self.connect() as db:
            db.execute("ALTER TABLE search_results DROP COLUMN retrieval_attempted_at")
        pipeline.migrate_db(self.db_path)
        pipeline.migrate_db(self.db_path)
        html = b"<style>hidden css</style><script>hidden js</script><p>Visible  text</p>"
        calls = []

        def transport(url, headers, timeout):
            calls.append(url)
            if url == "https://limited.example/page":
                return FakeResponse(404)
            return FakeResponse(200, html, {"Content-Type": "text/html"})

        counts = pipeline.retrieve_pending("BQ-002", self.db_path, self.public_resolver, transport)
        self.assertEqual(counts, {"retrieved": 1, "limited": 1})
        with self.connect() as db:
            rows = db.execute("""
                SELECT id,content_retrieved,retrieval_status,limitation,retrieval_attempted_at,snippet
                FROM search_results ORDER BY position
            """).fetchall()
        self.assertEqual(rows[0][:4], (limited_id, None, "limited", "http_404"))
        self.assertTrue(rows[0][4])
        self.assertEqual(rows[1][:4], (success_id, "Visible text", "retrieved", None))
        self.assertTrue(rows[1][4])
        self.assertEqual(rows[1][5], "snippet original")
        self.assertEqual(calls, ["https://limited.example/page", "https://public.example/page"])
        self.assertEqual(pipeline.retrieve_pending(
            "BQ-002", self.db_path, self.public_resolver,
            lambda url, headers, timeout: self.fail("retrieval repeated"),
        ), {"retrieved": 0, "limited": 0})
        self.assertEqual(calls, ["https://limited.example/page", "https://public.example/page"])

    def test_private_blocked_before_get_and_no_transport_leaves_pending(self):
        private_id = self.add_pending(1, "http://private.example/page")
        calls = []

        def resolver(host, port, type=socket.SOCK_STREAM):
            ip = "127.0.0.1" if host == "private.example" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

        def transport(url, headers, timeout):
            calls.append(url)
            return FakeResponse(200, b"<p>unexpected</p>", {"Content-Type": "text/html"})

        counts = pipeline.retrieve_pending("BQ-002", self.db_path, resolver, transport)
        self.assertEqual(counts, {"retrieved": 0, "limited": 1})
        self.assertEqual(calls, [])
        with self.connect() as db:
            reason = db.execute(
                "SELECT limitation FROM search_results WHERE id=?", (private_id,)
            ).fetchone()[0]
        self.assertEqual(reason, "blocked_destination")

        public_id = self.add_pending(2, "https://public.example/page")
        with redirect_stderr(io.StringIO()):
            exit_code = pipeline.main(["retrieve", "--bq", "BQ-002", "--db", str(self.db_path)])
        self.assertEqual(exit_code, 2)
        self.assertEqual(calls, [])
        with self.connect() as db:
            status, attempted = db.execute(
                "SELECT retrieval_status,retrieval_attempted_at FROM search_results WHERE id=?",
                (public_id,),
            ).fetchone()
        self.assertEqual((status, attempted), ("pending", None))

    def test_init_db_creates_only_three_required_tables(self):
        with self.connect() as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )}
            evidence_columns = {row[1]: row[3] for row in db.execute("PRAGMA table_info(evidence)")}
        self.assertEqual(tables, {"runs", "search_results", "evidence"})
        self.assertEqual(evidence_columns["url"], 1)

    def test_fixture_inserts_two_unique_results(self):
        pipeline.collect("BQ-002", self.db_path, fixture=self.fixture_path())
        with self.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM search_results").fetchone()[0]
        self.assertEqual(count, 2)

    def test_fixture_preserves_position_and_snippet(self):
        pipeline.collect("BQ-002", self.db_path, fixture=self.fixture_path())
        with self.connect() as db:
            rows = db.execute("SELECT position,snippet FROM search_results ORDER BY position").fetchall()
        self.assertEqual(rows[0], (1, "Fixture: reseña de una experiencia laboral en el área de TI."))

    def test_fixture_mode_never_uses_network(self):
        with patch.object(pipeline, "urlopen", side_effect=AssertionError("network called")):
            pipeline.collect("BQ-002", self.db_path, fixture=self.fixture_path())

    def test_repeating_fixture_adds_run_but_not_results(self):
        fixture = self.fixture_path()
        pipeline.collect("BQ-002", self.db_path, fixture=fixture)
        pipeline.collect("BQ-002", self.db_path, fixture=fixture)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM search_results").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)

    def test_missing_api_key_fails_without_network_or_results(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}), \
             patch.object(pipeline, "urlopen", side_effect=AssertionError("network called")):
            with self.assertRaisesRegex(RuntimeError, "Falta BRAVE_SEARCH_API_KEY"):
                pipeline.collect("BQ-002", self.db_path)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM search_results").fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM runs WHERE status='failed' AND finished_at IS NOT NULL"
            ).fetchone()[0], 1)

    def test_invalid_fixture_leaves_one_failed_run_and_no_results(self):
        invalid_fixture = Path(self.temp.name) / "invalid.json"
        invalid_fixture.write_text("{invalid json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            pipeline.collect("BQ-002", self.db_path, fixture=invalid_fixture)
        with self.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM runs WHERE status='failed' AND finished_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM search_results").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
