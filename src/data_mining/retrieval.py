"""Recuperación segura, transporte curl y extracción de HTML."""

import ipaddress
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import urlsplit

from .db import migrate_db

USER_AGENT = "DirectorioLaboralMVP/0.1"
MAX_HTML_BYTES = 1024 * 1024


class RetrievalIssue(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


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
