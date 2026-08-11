#!/usr/bin/env python3
"""Small SQLite opener/query shell using only Python's standard library."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Sequence


MAX_CELL_WIDTH = 48
DEFAULT_LIMIT = 20


def quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def connect(db_path: Path, readonly: bool) -> sqlite3.Connection:
    if readonly:
        uri_path = db_path.resolve().as_posix()
        return sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
    return sqlite3.connect(str(db_path))


def get_objects(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute(
        """
        SELECT type, name
        FROM sqlite_master
        WHERE type IN ('table', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()


def print_objects(conn: sqlite3.Connection) -> None:
    rows = get_objects(conn)
    if not rows:
        print("No tables or views found.")
        return

    print("Tables/views:")
    for obj_type, name in rows:
        print(f"  {obj_type:5} {name}")


def schema(conn: sqlite3.Connection, name: str | None = None) -> None:
    if name:
        rows = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type IN ('table', 'view', 'index', 'trigger')
              AND name = ?
            ORDER BY type, name
            """,
            (name,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type IN ('table', 'view', 'index', 'trigger')
              AND sql IS NOT NULL
            ORDER BY type, name
            """
        ).fetchall()

    if not rows:
        print("No schema found.")
        return

    for (sql,) in rows:
        if sql:
            print(sql.rstrip(";") + ";\n")


def columns(conn: sqlite3.Connection, table: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({quote_identifier(table)})").fetchall()
    if not rows:
        print(f"No columns found for {table!r}.")
        return

    data = [(row[1], row[2], "NOT NULL" if row[3] else "", row[4] or "", "PK" if row[5] else "") for row in rows]
    print_table(["name", "type", "null", "default", "key"], data)


def preview(conn: sqlite3.Connection, table: str, limit: int = DEFAULT_LIMIT) -> None:
    limit = max(1, min(limit, 500))
    cursor = conn.execute(f"SELECT * FROM {quote_identifier(table)} LIMIT ?", (limit,))
    print_result(cursor)


def count_rows(conn: sqlite3.Connection, table: str) -> None:
    try:
        count = conn.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0]
        print(f"{table}: {count}")
    except sqlite3.Error as exc:
        print(f"Error: {exc}")


def stringify(value: object) -> str:
    if value is None:
        text = "NULL"
    elif isinstance(value, bytes):
        text = f"<{len(value)} bytes>"
    else:
        text = str(value)

    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > MAX_CELL_WIDTH:
        return text[: MAX_CELL_WIDTH - 1] + "~"
    return text


def print_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    row_list = [[stringify(value) for value in row] for row in rows]
    headers = [stringify(header) for header in headers]
    widths = [len(header) for header in headers]

    for row in row_list:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    if not row_list:
        print("(no rows)")
        return

    header_line = " | ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    rule = "-+-".join("-" * width for width in widths)
    print(header_line)
    print(rule)
    for row in row_list:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def print_result(cursor: sqlite3.Cursor) -> None:
    if cursor.description is None:
        print(f"OK, {cursor.rowcount if cursor.rowcount != -1 else 0} rows affected.")
        return

    headers = [column[0] for column in cursor.description]
    print_table(headers, cursor.fetchall())


def print_help() -> None:
    print(
        """
Commands:
  .tables                 List tables and views
  .schema [name]          Show full schema or one object schema
  .columns <table>        Show columns for a table
  .preview <table> [n]    Show first n rows, default 20
  .count <table>          Count rows in a table
  .help                   Show this help
  .quit / .exit           Close

SQL:
  Type any SQL statement ending with a semicolon.
  Default mode is read-only. Start with --write to allow INSERT/UPDATE/DELETE.
""".strip()
    )


def parse_preview_args(parts: list[str]) -> tuple[str | None, int]:
    if len(parts) < 2:
        return None, DEFAULT_LIMIT

    table = parts[1]
    limit = DEFAULT_LIMIT
    if len(parts) >= 3:
        try:
            limit = int(parts[2])
        except ValueError:
            print("Limit must be a number.")
    return table, limit


def run_meta_command(conn: sqlite3.Connection, line: str) -> bool:
    parts = line.split()
    command = parts[0].lower()

    if command in {".quit", ".exit"}:
        return False
    if command == ".help":
        print_help()
    elif command == ".tables":
        print_objects(conn)
    elif command == ".schema":
        schema(conn, parts[1] if len(parts) > 1 else None)
    elif command == ".columns":
        if len(parts) < 2:
            print("Usage: .columns <table>")
        else:
            columns(conn, parts[1])
    elif command == ".preview":
        table, limit = parse_preview_args(parts)
        if not table:
            print("Usage: .preview <table> [limit]")
        else:
            preview(conn, table, limit)
    elif command == ".count":
        if len(parts) < 2:
            print("Usage: .count <table>")
        else:
            count_rows(conn, parts[1])
    else:
        print(f"Unknown command: {command}. Type .help")

    return True


def repl(conn: sqlite3.Connection, readonly: bool) -> None:
    print_objects(conn)
    print("\nType .help for commands, .quit to close.")

    buffer: list[str] = []
    while True:
        prompt = "sqlite> " if not buffer else "   ...> "
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        stripped = line.strip()
        if not buffer and not stripped:
            continue
        if not buffer and stripped.startswith("."):
            if not run_meta_command(conn, stripped):
                break
            continue

        buffer.append(line)
        statement = "\n".join(buffer)
        if not sqlite3.complete_statement(statement):
            continue

        try:
            cursor = conn.execute(statement)
            print_result(cursor)
            if not readonly:
                conn.commit()
        except sqlite3.Error as exc:
            print(f"Error: {exc}")
        finally:
            buffer.clear()


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Open and query a SQLite database.")
    parser.add_argument("database", help="Path to .sqlite/.db file")
    parser.add_argument("--write", action="store_true", help="Open read/write instead of safe read-only mode")
    parser.add_argument("--tables", action="store_true", help="Print tables/views and exit")
    parser.add_argument("--schema", nargs="?", const="", help="Print schema and exit; optionally pass one object name")
    parser.add_argument("--preview", metavar="TABLE", help="Preview a table and exit")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Rows for --preview, default 20")
    parser.add_argument("--sql", help="Run one SQL statement and exit")
    args = parser.parse_args(argv)

    db_path = Path(args.database)
    if not args.write and not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    try:
        conn = connect(db_path, readonly=not args.write)
    except sqlite3.Error as exc:
        print(f"Could not open database: {exc}", file=sys.stderr)
        return 1

    with conn:
        if args.tables:
            print_objects(conn)
        elif args.schema is not None:
            schema(conn, args.schema or None)
        elif args.preview:
            preview(conn, args.preview, args.limit)
        elif args.sql:
            cursor = conn.execute(args.sql)
            print_result(cursor)
            if args.write:
                conn.commit()
        else:
            db_name = os.path.relpath(db_path, Path.cwd())
            mode = "read/write" if args.write else "read-only"
            print(f"Opened {db_name} ({mode})")
            print(f"SQLite {sqlite3.sqlite_version}\n")
            repl(conn, readonly=not args.write)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
