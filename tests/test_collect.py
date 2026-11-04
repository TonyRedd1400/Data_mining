import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_mining import collect, db


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.db_path = Path(self.temp.name) / "test.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def fixture_path(self):
        return Path(__file__).resolve().parent / "fixtures" / "brave_bq002.json"

    def test_configuration_has_24_bqs_and_exact_bq002_query(self):
        rows = collect.load_queries()
        self.assertEqual(len(rows), 24)
        self.assertEqual(len({row["bq_id"] for row in rows}), 24)
        bq002 = next(row for row in rows if row["bq_id"] == "BQ-002")
        self.assertEqual(bq002["query"], "opiniones área de TI Liverpool")

    def test_normalization_removes_tracking_and_sorts_parameters(self):
        first = collect.normalize_url("HTTPS://Example.com/path/?z=2&utm_source=x&a=1#section")
        second = collect.normalize_url("https://example.com/path?a=1&z=2&fbclid=y")
        self.assertEqual(first, "https://example.com/path?a=1&z=2")
        self.assertEqual(first, second)

    def test_fixture_inserts_two_unique_results(self):
        collect.collect("BQ-002", self.db_path, fixture=self.fixture_path())
        with closing(sqlite3.connect(self.db_path)) as database:
            count = database.execute("SELECT COUNT(*) FROM search_results").fetchone()[0]
        self.assertEqual(count, 2)

    def test_fixture_preserves_position_and_snippet(self):
        collect.collect("BQ-002", self.db_path, fixture=self.fixture_path())
        with closing(sqlite3.connect(self.db_path)) as database:
            rows = database.execute("SELECT position,snippet FROM search_results ORDER BY position").fetchall()
        self.assertEqual(rows[0], (1, "Fixture: reseña de una experiencia laboral en el área de TI."))

    def test_fixture_mode_never_uses_network(self):
        with patch.object(collect, "urlopen", side_effect=AssertionError("network called")):
            collect.collect("BQ-002", self.db_path, fixture=self.fixture_path())

    def test_repeating_fixture_adds_run_but_not_results(self):
        fixture = self.fixture_path()
        collect.collect("BQ-002", self.db_path, fixture=fixture)
        collect.collect("BQ-002", self.db_path, fixture=fixture)
        with closing(sqlite3.connect(self.db_path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM search_results").fetchone()[0], 2)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)

    def test_missing_api_key_fails_without_network_or_results(self):
        with patch.dict(os.environ, {"BRAVE_SEARCH_API_KEY": ""}), \
             patch.object(collect, "urlopen", side_effect=AssertionError("network called")):
            with self.assertRaisesRegex(RuntimeError, "Falta BRAVE_SEARCH_API_KEY"):
                collect.collect("BQ-002", self.db_path)
        with closing(sqlite3.connect(self.db_path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM search_results").fetchone()[0], 0)
            self.assertEqual(database.execute(
                "SELECT COUNT(*) FROM runs WHERE status='failed' AND finished_at IS NOT NULL"
            ).fetchone()[0], 1)

    def test_invalid_fixture_leaves_one_failed_run_and_no_results(self):
        invalid_fixture = Path(self.temp.name) / "invalid.json"
        invalid_fixture.write_text("{invalid json", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            collect.collect("BQ-002", self.db_path, fixture=invalid_fixture)
        with closing(sqlite3.connect(self.db_path)) as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(database.execute(
                "SELECT COUNT(*) FROM runs WHERE status='failed' AND finished_at IS NOT NULL"
            ).fetchone()[0], 1)
            self.assertEqual(database.execute("SELECT COUNT(*) FROM search_results").fetchone()[0], 0)
