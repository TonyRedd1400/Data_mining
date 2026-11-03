import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pipeline


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
