"""MVP local para configurar BQ e importar resultados de Brave Search."""

import argparse
import csv
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "queries.csv"
CONFIG_COLUMNS = (
    "bq_id", "company", "family", "query", "country", "language",
    "max_results", "provider",
)
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
USER_AGENT = "DirectorioLaboralMVP/0.1"
MAX_HTML_BYTES = 1024 * 1024
RETRIEVAL_COLUMNS = {"retrieval_attempted_at": "TEXT"}


class RetrievalIssue(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def load_queries(path=CONFIG):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != CONFIG_COLUMNS:
            raise ValueError("queries.csv debe tener exactamente las ocho columnas requeridas y en orden.")
        rows = list(reader)
    if len(rows) != 24:
        raise ValueError(f"Se requieren exactamente 24 BQ; hay {len(rows)}.")
    for number, row in enumerate(rows, 1):
        expected = f"BQ-{number:03d}"
        if row["bq_id"] != expected:
            raise ValueError(f"Se esperaba {expected} en la fila {number}.")
        if any(not row.get(key, "").strip() for key in CONFIG_COLUMNS):
            raise ValueError(f"Hay un campo obligatorio vacío en {expected}.")
        try:
            maximum = int(row["max_results"])
        except ValueError as exc:
            raise ValueError(f"max_results no es un entero en {expected}.") from exc
        if not 1 <= maximum <= 20:
            raise ValueError(f"max_results debe estar entre 1 y 20 en {expected}.")
        if (row["country"], row["language"], row["provider"]) != ("mx", "es", "brave"):
            raise ValueError(f"{expected} debe usar country=mx, language=es y provider=brave.")
    return rows


def normalize_url(url):
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise ValueError(f"URL no válida: {url!r}")
    host = parts.hostname
    if not host:
        raise ValueError(f"URL no válida: {url!r}")
    userinfo = parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else ""
    host = f"[{host}]" if ":" in host else host
    netloc = userinfo + host
    if parts.port is not None:
        netloc += f":{parts.port}"
    query = [
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path,
                       urlencode(sorted(query)), ""))


def init_db(path):
    db = sqlite3.connect(path)
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                provider TEXT NOT NULL,
                country TEXT NOT NULL,
                language TEXT NOT NULL,
                config_version TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS search_results (
                id INTEGER PRIMARY KEY,
                bq_id TEXT NOT NULL,
                run_id INTEGER NOT NULL REFERENCES runs(id),
                company TEXT NOT NULL,
                query_literal TEXT NOT NULL,
                position INTEGER NOT NULL,
                provider TEXT NOT NULL,
                searched_at TEXT NOT NULL,
                country TEXT NOT NULL,
                mexico_match TEXT,
                url_original TEXT NOT NULL,
                url_norm TEXT NOT NULL,
                title TEXT NOT NULL,
                snippet TEXT NOT NULL,
                content_retrieved TEXT,
                eligibility TEXT,
                retrieval_status TEXT NOT NULL DEFAULT 'pending',
                retrieval_attempted_at TEXT,
                limitation TEXT,
                exclusion_reason TEXT,
                UNIQUE (bq_id, provider, url_norm)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY,
                search_result_id INTEGER NOT NULL REFERENCES search_results(id),
                url TEXT NOT NULL,
                nature TEXT,
                hypothesis_relation TEXT,
                labor_moment TEXT,
                object TEXT,
                topic TEXT,
                relation_type TEXT,
                valuation TEXT,
                confidence REAL,
                impact TEXT,
                consequence TEXT,
                information_provided TEXT,
                information_missing TEXT,
                research_question TEXT,
                exact_excerpt TEXT,
                hypothesis TEXT
            );
        """)
        _ensure_retrieval_columns(db)
        db.commit()
    finally:
        db.close()


def _ensure_retrieval_columns(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(search_results)")}
    for name, column_type in RETRIEVAL_COLUMNS.items():
        if name not in columns:
            db.execute(f"ALTER TABLE search_results ADD COLUMN {name} {column_type}")


def migrate_db(path):
    db = sqlite3.connect(path)
    try:
        _ensure_retrieval_columns(db)
        db.commit()
    finally:
        db.close()


def _read_brave(query, api_key):
    params = urlencode({"q": query["query"], "country": "mx",
                        "search_lang": "es", "count": 20})
    request = Request(
        f"{BRAVE_ENDPOINT}?{params}",
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Falló la consulta a Brave Search API: {exc}") from exc


def collect(bq_id, db_path, fixture=None, config_path=CONFIG):
    queries = load_queries(config_path)
    query = next((row for row in queries if row["bq_id"] == bq_id), None)
    if query is None:
        raise ValueError(f"BQ desconocida: {bq_id}")
    api_key = None if fixture else os.environ.get("BRAVE_SEARCH_API_KEY")
    db_path = Path(db_path)
    if not db_path.is_file():
        raise RuntimeError("La base no existe; ejecuta init-db --db RUTA antes de collect.")
    db = sqlite3.connect(db_path)
    run_id = None
    try:
        started_at = datetime.now(timezone.utc).isoformat()
        cur = db.execute(
            "INSERT INTO runs(started_at,status,provider,country,language,config_version) "
            "VALUES(?, 'running', 'brave', 'mx', 'es', ?)",
            (started_at, hashlib.sha256(Path(config_path).read_bytes()).hexdigest()[:16]),
        )
        run_id = cur.lastrowid
        db.commit()

        if not fixture and not api_key:
            raise RuntimeError("Falta BRAVE_SEARCH_API_KEY; configúrala para recolección real o usa --fixture.")
        if fixture:
            with open(fixture, encoding="utf-8") as stream:
                response_data = json.load(stream)
        else:
            response_data = _read_brave(query, api_key)
        results = response_data.get("web", {}).get("results", [])
        if not isinstance(results, list):
            raise ValueError("La respuesta Brave debe incluir web.results como lista.")
        searched_at = datetime.now(timezone.utc).isoformat()
        for position, item in enumerate(results[:20], 1):
            url = item.get("url", "")
            title = item.get("title", "")
            snippet = item.get("description", "")
            if not url or not title:
                continue
            normalized = normalize_url(url)
            db.execute("""
                INSERT OR IGNORE INTO search_results
                (bq_id,run_id,company,query_literal,position,provider,searched_at,country,
                 url_original,url_norm,title,snippet)
                VALUES(?,?,?,?,?,'brave',?,'mx',?,?,?,?)
            """, (bq_id, run_id, query["company"], query["query"], position,
                  searched_at, url, normalized, title, snippet))
        db.execute("UPDATE runs SET status='completed', finished_at=? WHERE id=?",
                   (datetime.now(timezone.utc).isoformat(), run_id))
        db.commit()
        return run_id
    except Exception:
        db.rollback()
        if run_id is not None:
            db.execute("UPDATE runs SET status='failed', finished_at=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(), run_id))
            db.commit()
        raise
    finally:
        db.close()


def _destination_parts(url):
    try:
        parts = urlsplit(url)
        port = parts.port if parts.port is not None else (443 if parts.scheme.lower() == "https" else 80)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or port not in {80, 443}):
            raise RetrievalIssue("blocked_destination")
    except ValueError as exc:
        raise RetrievalIssue("blocked_destination") from exc
    return parts, port


def _public_destination(url, resolver=None):
    parts, port = _destination_parts(url)
    try:
        addresses = (resolver or socket.getaddrinfo)(parts.hostname, port, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if not ips or any(not ip.is_global for ip in ips):
            raise RetrievalIssue("blocked_destination")
        return ips[0]
    except RetrievalIssue:
        raise
    except (OSError, ValueError, IndexError) as exc:
        raise RetrievalIssue("network_error") from exc


def _open_once(url, transport, pinned_ip):
    if transport is None:
        raise RuntimeError("No hay transporte de egreso verificado para iniciar la recuperación.")
    try:
        return transport(url, headers={"User-Agent": USER_AGENT,
                                       "Accept-Encoding": "identity"}, timeout=10,
                        pinned_ip=pinned_ip)
    except (TimeoutError, socket.timeout) as exc:
        raise RetrievalIssue("timeout") from exc
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise RetrievalIssue("timeout") from exc
        raise RetrievalIssue("network_error") from exc
    except OSError as exc:
        raise RetrievalIssue("network_error") from exc


def _curl_transport():
    executable = shutil.which("curl.exe") or shutil.which("curl")
    if not executable:
        raise RuntimeError("No se encontró curl; las filas siguen pendientes.")

    def transport(url, headers, timeout, pinned_ip):
        parts, port = _destination_parts(url)
        resolve_ip = f"[{pinned_ip}]" if pinned_ip.version == 6 else str(pinned_ip)
        resolve_host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        with tempfile.TemporaryDirectory(prefix="dm-retrieve-") as folder:
            body_path = os.path.join(folder, "body")
            headers_path = os.path.join(folder, "headers")
            command = [
                executable, "--disable", "--silent", "--show-error", "--noproxy", "*",
                "--proto", "=http,https", "--no-location", "--max-redirs", "0", "--max-time", "10",
                "--max-filesize", str(MAX_HTML_BYTES), "--resolve",
                f"{resolve_host}:{port}:{resolve_ip}", "--header", "Accept-Encoding: identity",
                "--user-agent", headers["User-Agent"], "--output", body_path,
                "--dump-header", headers_path, url,
            ]
            try:
                result = subprocess.run(command, capture_output=True, timeout=10, check=False)
            except FileNotFoundError as exc:
                raise RuntimeError("No se encontró curl; las filas siguen pendientes.") from exc
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise RetrievalIssue("network_error") from exc
            if result.returncode:
                if result.returncode == 28:
                    raise RetrievalIssue("timeout")
                if result.returncode == 63:
                    raise RetrievalIssue("too_large")
                raise RetrievalIssue("network_error")
            with open(headers_path, "rb") as stream:
                raw_headers = stream.read().decode("iso-8859-1")
            blocks = [block for block in raw_headers.split("\r\n\r\n") if block.strip()]
            lines = blocks[-1].splitlines()
            status = int(lines[0].split()[1])
            response_headers = {}
            for line in lines[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    response_headers[name.strip()] = value.strip()
            with open(body_path, "rb") as stream:
                body = stream.read(MAX_HTML_BYTES + 1)
            return CurlResponse(status, body, response_headers)

    return transport


class CurlResponse:
    def __init__(self, status, body, headers):
        self.status, self.body, self.headers = status, body, headers

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def close(self):
        pass


def _status(response):
    return response.getcode() if hasattr(response, "getcode") else response.status


def _header(response, name, default=""):
    return next((value for key, value in response.headers.items()
                 if key.lower() == name.lower()), default)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _retrieve_html(url, resolver=None, transport=None):
    if transport is None:
        raise RuntimeError("No hay transporte de egreso verificado para iniciar la recuperación.")
    pinned_ip = _public_destination(url, resolver)
    response = _open_once(url, transport, pinned_ip)
    try:
        status = _status(response)
        if status != 200:
            raise RetrievalIssue(f"http_{status}")
        if _header(response, "Content-Type").split(";", 1)[0].strip().lower() != "text/html":
            raise RetrievalIssue("unsupported_type")
        if _header(response, "Content-Encoding", "identity").lower() not in {"", "identity"}:
            raise RetrievalIssue("unsupported_type")
        length = _header(response, "Content-Length")
        if length and int(length) > MAX_HTML_BYTES:
            raise RetrievalIssue("too_large")
        body = response.read(MAX_HTML_BYTES + 1)
        if len(body) > MAX_HTML_BYTES:
            raise RetrievalIssue("too_large")
        parser = _VisibleText()
        parser.feed(body.decode("utf-8", errors="replace"))
        parser.close()
        text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        if not text:
            raise RetrievalIssue("empty_text")
        return text
    except ValueError as exc:
        raise RetrievalIssue("network_error") from exc
    finally:
        response.close()


def retrieve_pending(bq_id, db_path, resolver=None, transport=None):
    migrate_db(db_path)
    db = sqlite3.connect(db_path)
    counts = {"retrieved": 0, "limited": 0}
    try:
        rows = db.execute(
            "SELECT id,url_original FROM search_results "
            "WHERE bq_id=? AND retrieval_status='pending' ORDER BY position,id",
            (bq_id,),
        ).fetchall()
        if rows and transport is None:
            raise RuntimeError("No hay transporte de egreso verificado; las filas siguen pendientes.")
        for result_id, url in rows:
            attempted_at = datetime.now(timezone.utc).isoformat()
            try:
                content = _retrieve_html(url, resolver, transport)
                db.execute("""
                    UPDATE search_results SET content_retrieved=?, retrieval_status='retrieved',
                    limitation=NULL, retrieval_attempted_at=? WHERE id=?
                """, (content, attempted_at, result_id))
                counts["retrieved"] += 1
            except RetrievalIssue as issue:
                db.execute("""
                    UPDATE search_results SET content_retrieved=NULL, retrieval_status='limited',
                    limitation=?, retrieval_attempted_at=? WHERE id=?
                """, (issue.code, attempted_at, result_id))
                counts["limited"] += 1
            except RuntimeError:
                raise
            except Exception:
                db.execute("""
                    UPDATE search_results SET content_retrieved=NULL, retrieval_status='limited',
                    limitation='network_error', retrieval_attempted_at=? WHERE id=?
                """, (attempted_at, result_id))
                counts["limited"] += 1
            db.commit()
        return counts
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recolector mínimo de resultados Brave")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-config")
    init = commands.add_parser("init-db")
    init.add_argument("--db", required=True)
    collect_cmd = commands.add_parser("collect")
    collect_cmd.add_argument("--bq", required=True)
    collect_cmd.add_argument("--db", required=True)
    collect_cmd.add_argument("--fixture")
    retrieve_cmd = commands.add_parser("retrieve")
    retrieve_cmd.add_argument("--bq", required=True)
    retrieve_cmd.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-config":
            rows = load_queries()
            print(f"Configuración válida: {len(rows)} BQ.")
        elif args.command == "init-db":
            init_db(args.db)
            print(f"Base inicializada: {args.db}")
        else:
            if args.command == "collect":
                run_id = collect(args.bq, args.db, args.fixture)
                print(f"Recolección completada; run_id={run_id}.")
            else:
                counts = retrieve_pending(args.bq, args.db, transport=_curl_transport())
                print(f"Recuperación completada: {counts['retrieved']} recuperadas, "
                      f"{counts['limited']} limitadas.")
    except (OSError, sqlite3.Error, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
