"""Postgres: Connection-Pool und nummerierte SQL-Migrationen (Abschnitt 9.2)."""

from __future__ import annotations

import threading
from importlib import resources
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from standbild.config import get_settings

_LOCK = 7_826_352_114  # beliebiger fester Schlüssel für pg_advisory_lock
_pool: ConnectionPool[psycopg.Connection[dict[str, Any]]] | None = None
_pool_lock = threading.Lock()


def get_pool() -> ConnectionPool[psycopg.Connection[dict[str, Any]]]:
    global _pool
    with _pool_lock:  # Worker-Threads starten gleichzeitig; es darf nur ein Pool entstehen
        if _pool is None:
            s = get_settings()
            _pool = ConnectionPool(
                s.conninfo(),
                connection_class=psycopg.Connection[dict[str, Any]],
                kwargs={"row_factory": dict_row, "autocommit": True},
                min_size=1,
                max_size=s.workers * 2 + 4,
                open=True,
            )
        return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def migrate() -> list[int]:
    """Wendet alle noch fehlenden Migrationen in einer Transaktion an; parallele Starts warten auf die Sperre."""
    s = get_settings()
    files = sorted(
        (
            f
            for f in resources.files("standbild.db").joinpath("migrations").iterdir()
            if f.name.endswith(".sql")
        ),
        key=lambda f: f.name,
    )
    applied: list[int] = []
    with psycopg.connect(s.conninfo(), autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK,))
        try:
            with conn.transaction():
                if s.db_schema != "public":
                    conn.execute(
                        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(s.db_schema))
                    )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
                )
                done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
                for f in files:
                    number = int(f.name.split("_")[0])
                    if number not in done:
                        conn.execute(
                            f.read_text(encoding="utf-8").encode()
                        )  # bytes: mehrere Anweisungen, keine Parameter
                        conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (number,))
                        applied.append(number)
        except psycopg.errors.InsufficientPrivilege as exc:
            # Häufig ab Postgres 15: im Schema public darf nur noch der Eigentümer der Datenbank Tabellen anlegen.
            raise RuntimeError(
                f"Der Datenbankbenutzer {s.db_user} darf im Schema {s.db_schema} keine Tabellen anlegen ({exc.diag.message_primary}). "
                f"Als Administrator beheben, z. B.: ALTER DATABASE {s.db_name} OWNER TO {s.db_user};  "
                f"oder: GRANT CREATE ON SCHEMA {s.db_schema} TO {s.db_user};"
            ) from exc
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK,))
    return applied
