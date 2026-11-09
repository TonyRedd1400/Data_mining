"""Catálogo BQ, normalización y recolección con Brave Search."""

import csv
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "queries.csv"
CONFIG_COLUMNS = (
    "bq_id", "company", "family", "query", "country", "language",
    "max_results", "provider",
)
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


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
