import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_mining import db as pipeline_db


class DatabaseTests(unittest.TestCase):
    def test_init_db_creates_only_three_required_tables(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            db_path = Path(folder) / "test.db"
            pipeline_db.init_db(db_path)
            with closing(sqlite3.connect(db_path)) as db:
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )}
                evidence_columns = {row[1]: row[3] for row in db.execute("PRAGMA table_info(evidence)")}
            self.assertEqual(tables, {"runs", "search_results", "evidence"})
            self.assertEqual(evidence_columns["url"], 1)
