"""Interfaz de línea de comandos del pipeline."""

import argparse
import json
import sqlite3
import sys

from .collect import collect, load_queries
from .db import init_db
from .retrieval import _curl_transport, retrieve_pending


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
        elif args.command == "collect":
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
