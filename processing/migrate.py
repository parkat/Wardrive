#!/usr/bin/env python3
"""
Database migration runner.
Applies SQL migration files in order, tracking applied versions in schema_version.
Safe to run repeatedly — already-applied migrations are skipped.
"""

import logging
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")

    # Bootstrap schema_version if it doesn't exist
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            applied_at REAL NOT NULL DEFAULT (unixepoch('now', 'subsec'))
        )
    """)
    conn.commit()

    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}

    migration_files = sorted(_MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)
    for mf in migration_files:
        # Extract version number from filename prefix (e.g., "001_...")
        try:
            version = int(mf.stem.split("_")[0])
        except (ValueError, IndexError):
            log.warning("skipping migration with non-numeric prefix: %s", mf.name)
            continue

        if version in applied:
            continue

        log.info("applying migration %s", mf.name)
        sql = mf.read_text()
        try:
            conn.executescript(sql)
            # executescript auto-commits; ensure version is recorded
            if not conn.execute(
                "SELECT 1 FROM schema_version WHERE version=?", (version,)
            ).fetchone():
                conn.execute(
                    "INSERT OR IGNORE INTO schema_version (version) VALUES (?)", (version,)
                )
                conn.commit()
            log.info("migration %d applied", version)
        except sqlite3.Error as exc:
            log.error("migration %s failed: %s", mf.name, exc)
            conn.close()
            raise

    conn.close()
    log.info("database at %s is up to date", db_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("processing/wardrive.db")
    run_migrations(db_path)
