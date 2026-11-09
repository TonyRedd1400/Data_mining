import io
import socket
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from data_mining import cli, db, retrieval


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


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.db_path = Path(self.temp.name) / "test.db"
        db.init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def connect(self):
        database = sqlite3.connect(self.db_path)
        try:
            yield database
        finally:
            database.close()

    def add_pending(self, position, url):
        with self.connect() as database:
            run_id = database.execute(
                "INSERT INTO runs(started_at,status,provider,country,language,config_version) "
                "VALUES('now','completed','brave','mx','es','test')"
            ).lastrowid
            result_id = database.execute("""
                INSERT INTO search_results
                (bq_id,run_id,company,query_literal,position,provider,searched_at,country,
                 url_original,url_norm,title,snippet)
                VALUES('BQ-002',?, 'Liverpool','opiniones área de TI Liverpool',?,
                       'brave','now','mx',?,?, 'título', 'snippet original')
            """, (run_id, position, url, f"{url}#{position}")).lastrowid
            database.commit()
        return result_id

    @staticmethod
    def public_resolver(host, port, type=socket.SOCK_STREAM):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    def test_retrieval_persistence_rerun_and_repeatable_migration(self):
        limited_id = self.add_pending(1, "https://limited.example/page")
        success_id = self.add_pending(2, "https://public.example/page")
        with self.connect() as database:
            database.execute("ALTER TABLE search_results DROP COLUMN retrieval_attempted_at")
        db.migrate_db(self.db_path)
        db.migrate_db(self.db_path)
        html = b"<style>hidden css</style><script>hidden js</script><p>Visible  text</p>"
        calls = []

        def transport(url, headers, timeout, pinned_ip):
            calls.append(url)
            if url == "https://limited.example/page":
                return FakeResponse(404)
            return FakeResponse(200, html, {"Content-Type": "text/html"})

        counts = retrieval.retrieve_pending("BQ-002", self.db_path, self.public_resolver, transport)
        self.assertEqual(counts, {"retrieved": 1, "limited": 1})
        with self.connect() as database:
            rows = database.execute("""
                SELECT id,content_retrieved,retrieval_status,limitation,retrieval_attempted_at,snippet
                FROM search_results ORDER BY position
            """).fetchall()
        self.assertEqual(rows[0][:4], (limited_id, None, "limited", "http_404"))
        self.assertTrue(rows[0][4])
        self.assertEqual(rows[1][:4], (success_id, "Visible text", "retrieved", None))
        self.assertTrue(rows[1][4])
        self.assertEqual(rows[1][5], "snippet original")
        self.assertEqual(calls, ["https://limited.example/page", "https://public.example/page"])
        self.assertEqual(retrieval.retrieve_pending(
            "BQ-002", self.db_path, self.public_resolver,
            lambda url, headers, timeout, pinned_ip: self.fail("retrieval repeated"),
        ), {"retrieved": 0, "limited": 0})

    def test_private_blocked_before_get_and_no_transport_leaves_pending(self):
        private_id = self.add_pending(1, "http://private.example/page")
        calls = []

        def resolver(host, port, type=socket.SOCK_STREAM):
            ip = "127.0.0.1" if host == "private.example" else "93.184.216.34"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

        def transport(url, headers, timeout, pinned_ip):
            calls.append(url)
            return FakeResponse(200, b"<p>unexpected</p>", {"Content-Type": "text/html"})

        counts = retrieval.retrieve_pending("BQ-002", self.db_path, resolver, transport)
        self.assertEqual(counts, {"retrieved": 0, "limited": 1})
        self.assertEqual(calls, [])
        with self.connect() as database:
            reason = database.execute(
                "SELECT limitation FROM search_results WHERE id=?", (private_id,)
            ).fetchone()[0]
        self.assertEqual(reason, "blocked_destination")

        public_id = self.add_pending(2, "https://public.example/page")
        with patch.object(retrieval.shutil, "which", return_value=None), redirect_stderr(io.StringIO()):
            exit_code = cli.main(["retrieve", "--bq", "BQ-002", "--db", str(self.db_path)])
        self.assertEqual(exit_code, 2)
        with self.connect() as database:
            status, attempted = database.execute(
                "SELECT retrieval_status,retrieval_attempted_at FROM search_results WHERE id=?",
                (public_id,),
            ).fetchone()
        self.assertEqual((status, attempted), ("pending", None))

    def test_curl_pins_public_ip_disables_proxy_and_redirects(self):
        commands = []

        def run(command, **kwargs):
            commands.append(command)
            Path(command[command.index("--output") + 1]).write_bytes(b"<p>simulated HTML</p>")
            Path(command[command.index("--dump-header") + 1]).write_text(
                "HTTP/1.1 200 OK\r\ncontent-type: text/html\r\ncontent-length: 21\r\n\r\n",
                encoding="ascii",
            )
            return SimpleNamespace(returncode=0, stderr=b"")

        with patch.object(retrieval.shutil, "which", return_value="C:/curl.exe"), \
             patch.object(retrieval.tempfile, "tempdir", self.temp.name), \
             patch.object(retrieval.subprocess, "run", side_effect=run):
            text = retrieval._retrieve_html(
                "https://public.example/page", self.public_resolver,
                retrieval._curl_transport(),
            )
        self.assertEqual(text, "simulated HTML")
        command = commands[0]
        self.assertIn("public.example:443:93.184.216.34", command)
        self.assertEqual(command[command.index("--noproxy") + 1], "*")
        self.assertEqual(command[command.index("--proto") + 1], "=http,https")
        self.assertEqual(command[command.index("--max-redirs") + 1], "0")
        self.assertIn("Accept-Encoding: identity", command)
