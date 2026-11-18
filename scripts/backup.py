"""Consistent online backup of the SQLite database (safe while the API is running).

    python -m scripts.backup                 # -> storage/backups/keystrokeid-<UTC time>.db
    docker compose exec app python -m scripts.backup

Copy the resulting file off the server (it contains biometric data - keep it private).
Old backups beyond KEYSTROKEID_BACKUP_KEEP (default 14) are removed.
"""
import os
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    src = Path(os.environ.get("KEYSTROKEID_DB", "storage/keystrokeid.db"))
    if not src.exists():
        print(f"database not found: {src}", file=sys.stderr)
        return 1
    out_dir = src.parent / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = out_dir / f"keystrokeid-{stamp}.db"
    with closing(sqlite3.connect(src)) as a, closing(sqlite3.connect(dest)) as b:
        a.backup(b)                       # SQLite online backup API
    keep = int(os.environ.get("KEYSTROKEID_BACKUP_KEEP", "14"))
    for old in sorted(out_dir.glob("keystrokeid-*.db"))[:-keep]:
        old.unlink()
    print(dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
