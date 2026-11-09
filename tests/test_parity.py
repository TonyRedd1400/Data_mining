import importlib.util
import io
import ipaddress
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from data_mining import cli as modular_cli
from data_mining import retrieval as modular_retrieval


BASELINE = "c6d1d86d0f87c2c99a12f261eeef16bc3e2a0975"
TIMESTAMPS = {"started_at", "finished_at", "searched_at", "retrieval_attempted_at"}
TABLES = ("runs", "search_results", "evidence")


class FakeResponse:
    def __init__(self, status=200, body=b"<p>Visible text</p>", headers=None):
        self.status = status
        self.body = body
        self.headers = headers or {"Content-Type": "text/html"}

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def close(self):
        pass


def git_file(path):
    return subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "--filters", f"--path={path}",
         f"{BASELINE}:{path}"],
        check=True, capture_output=True,
    ).stdout


def baseline_hash(path):
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", f"{BASELINE}:{path}"],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


def working_hash(path):
    relative = Path(path).relative_to(ROOT).as_posix()
    return subprocess.run(
        ["git", "-C", str(ROOT), "hash-object", "--path", relative, relative],
        check=True, text=True, capture_output=True,
    ).stdout.strip()


def normalize_output(text, temporary_root):
    return text.replace(str(temporary_root), "<TEMP>")


def run_cli(entry, cwd, temporary_root, *args):
    environment = os.environ.copy()
    environment.pop("BRAVE_SEARCH_API_KEY", None)
    result = subprocess.run(
        [sys.executable, str(entry), *map(str, args)], cwd=cwd, env=environment,
        text=True, capture_output=True, check=False,
    )
    return (
        result.returncode,
        normalize_output(result.stdout, temporary_root),
        normalize_output(result.stderr, temporary_root),
    )


def schema(path):
    with closing(sqlite3.connect(path)) as database:
        master = database.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        pragmas = {}
        for table in TABLES:
            pragmas[table] = {
                "columns": database.execute(f"PRAGMA table_info({table})").fetchall(),
                "indexes": database.execute(f"PRAGMA index_list({table})").fetchall(),
                "foreign_keys": database.execute(f"PRAGMA foreign_key_list({table})").fetchall(),
            }
    return master, pragmas


def data(path):
    result = {}
    with closing(sqlite3.connect(path)) as database:
        for table in TABLES:
            columns = [row[1] for row in database.execute(f"PRAGMA table_info({table})")]
            rows = database.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            result[table] = [
                tuple("<TIMESTAMP>" if columns[index] in TIMESTAMPS and value else value
                      for index, value in enumerate(row))
                for row in rows
            ]
    return result


def public_resolver(host, port, type=socket.SOCK_STREAM):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def retrieve(module, database_path):
    calls = []

    def transport(url, headers, timeout, pinned_ip):
        calls.append((url, headers, timeout, str(pinned_ip)))
        return FakeResponse()

    first = module.retrieve_pending("BQ-002", database_path, public_resolver, transport)
    second = module.retrieve_pending(
        "BQ-002", database_path, public_resolver,
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("retrieval repeated")),
    )
    return first, second, calls


def blocked_destination(module):
    def mixed_resolver(host, port, type=socket.SOCK_STREAM):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    try:
        module._public_destination("https://mixed.example/page", mixed_resolver)
    except module.RetrievalIssue as issue:
        return issue.code
    raise AssertionError("mixed public/private destination was accepted")


def retrieve_cli(module):
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(module, "_curl_transport", return_value=object()), \
         patch.object(module, "retrieve_pending",
                      return_value={"retrieved": 2, "limited": 1}), \
         redirect_stdout(stdout), redirect_stderr(stderr):
        code = module.main(["retrieve", "--bq", "BQ-002", "--db", "unused.sqlite"])
    return code, stdout.getvalue(), stderr.getvalue()


def curl_command(module):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[command.index("--output") + 1]).write_bytes(b"<p>Visible text</p>")
        Path(command[command.index("--dump-header") + 1]).write_text(
            "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n", encoding="ascii",
        )
        return SimpleNamespace(returncode=0, stderr=b"")

    with patch.object(module.shutil, "which", return_value="C:/curl.exe"), \
         patch.object(module.subprocess, "run", side_effect=fake_run):
        response = module._curl_transport()(
            "https://public.example/page", {"User-Agent": module.USER_AGENT}, 10,
            ipaddress.ip_address("93.184.216.34"),
        )
        response.close()
    command = commands[0][:]
    command[command.index("--output") + 1] = "<TEMP>"
    command[command.index("--dump-header") + 1] = "<TEMP>"
    return command


class ParityTests(unittest.TestCase):
    def test_modular_pipeline_matches_immutable_baseline(self):
        with tempfile.TemporaryDirectory(prefix="dm-parity-") as folder:
            temporary = Path(folder)
            baseline_root = temporary / "baseline"
            baseline_root.mkdir()
            for source, target in (
                ("pipeline.py", "pipeline.py"),
                ("queries.csv", "queries.csv"),
                ("fixtures/brave_bq002.json", "fixtures/brave_bq002.json"),
            ):
                destination = baseline_root / target
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(git_file(source))

            self.assertEqual(baseline_hash("queries.csv"),
                             working_hash(ROOT / "config" / "queries.csv"))
            self.assertEqual(baseline_hash("fixtures/brave_bq002.json"),
                             working_hash(ROOT / "tests" / "fixtures" / "brave_bq002.json"))

            modular_temporary = temporary / "modular"
            modular_temporary.mkdir()
            baseline_db = baseline_root / "parity.sqlite"
            modular_db = modular_temporary / "parity.sqlite"
            baseline_entry = baseline_root / "pipeline.py"
            modular_entry = ROOT / "pipeline.py"
            fixture_old = baseline_root / "fixtures" / "brave_bq002.json"
            fixture_new = ROOT / "tests" / "fixtures" / "brave_bq002.json"

            command_pairs = (
                (("validate-config",), ("validate-config",)),
                (("init-db", "--db", baseline_db), ("init-db", "--db", modular_db)),
                (("collect", "--bq", "BQ-002", "--db", baseline_db, "--fixture", fixture_old),
                 ("collect", "--bq", "BQ-002", "--db", modular_db, "--fixture", fixture_new)),
                (("collect", "--bq", "BQ-002", "--db", baseline_db, "--fixture", fixture_old),
                 ("collect", "--bq", "BQ-002", "--db", modular_db, "--fixture", fixture_new)),
            )
            for old_args, new_args in command_pairs:
                self.assertEqual(
                    run_cli(baseline_entry, baseline_root, baseline_root, *old_args),
                    run_cli(modular_entry, ROOT, modular_temporary, *new_args),
                )

            spec = importlib.util.spec_from_file_location("baseline_pipeline_parity", baseline_entry)
            baseline = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(baseline)

            self.assertEqual(retrieve_cli(baseline), retrieve_cli(modular_cli))
            self.assertEqual(retrieve(baseline, baseline_db),
                             retrieve(modular_retrieval, modular_db))
            self.assertEqual(blocked_destination(baseline), "blocked_destination")
            self.assertEqual(blocked_destination(baseline),
                             blocked_destination(modular_retrieval))
            old_curl = curl_command(baseline)
            new_curl = curl_command(modular_retrieval)
            self.assertEqual(old_curl, new_curl)
            for option, value in (("--noproxy", "*"), ("--proto", "=http,https"),
                                  ("--max-redirs", "0")):
                self.assertEqual(new_curl[new_curl.index(option) + 1], value)

            self.assertEqual(schema(baseline_db), schema(modular_db))
            self.assertEqual(data(baseline_db), data(modular_db))


if __name__ == "__main__":
    unittest.main()
