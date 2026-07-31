#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Version, migrate, back up and restore the application database.

Production workers never run DDL automatically. Run this command as a distinct
release job before starting or rolling application replicas.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import get_config, setup_env  # noqa: E402
from src.storage.migrations import (  # noqa: E402
    SCHEMA_VERSION,
    assert_schema_compatible,
    ensure_compatible_schema,
    get_schema_version,
)


def _engine():
    return create_engine(get_config().get_db_url(), pool_pre_ping=True)


def _write_manifest(backup_path: Path, *, backend: str) -> Path:
    digest = hashlib.sha256()
    with backup_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "backend": backend,
                "schema_version": SCHEMA_VERSION,
                "backup_file": backup_path.name,
                "sha256": digest.hexdigest(),
                "size_bytes": backup_path.stat().st_size,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def migrate() -> dict[str, object]:
    engine = _engine()
    try:
        ensure_compatible_schema(
            engine,
            engine.url.get_backend_name() == "sqlite",
        )
        assert_schema_compatible(engine)
        return {
            "ok": True,
            "backend": engine.url.get_backend_name(),
            "schema_version": get_schema_version(engine),
        }
    finally:
        engine.dispose()


def check() -> dict[str, object]:
    engine = _engine()
    try:
        assert_schema_compatible(engine)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "ok": True,
            "backend": engine.url.get_backend_name(),
            "schema_version": get_schema_version(engine),
        }
    finally:
        engine.dispose()


def _postgres_environment(url) -> dict[str, str]:
    environment = os.environ.copy()
    if url.password:
        environment["PGPASSWORD"] = url.password
    return environment


def _postgres_connection_args(url) -> list[str]:
    arguments: list[str] = []
    if url.host:
        arguments.extend(["--host", url.host])
    if url.port:
        arguments.extend(["--port", str(url.port)])
    if url.username:
        arguments.extend(["--username", url.username])
    if url.database:
        arguments.extend(["--dbname", url.database])
    return arguments


def backup(output: Path) -> dict[str, object]:
    url = make_url(get_config().get_db_url())
    backend = url.get_backend_name()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"backup target already exists: {output}")

    if backend == "sqlite":
        source_path = Path(str(url.database or "")).resolve()
        if not source_path.is_file():
            raise RuntimeError(f"SQLite database does not exist: {source_path}")
        with sqlite3.connect(source_path) as source:
            with sqlite3.connect(output) as destination:
                source.backup(destination)
                integrity = destination.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError(f"backup integrity check failed: {integrity}")
    elif backend.startswith("postgresql"):
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise RuntimeError("pg_dump is required for PostgreSQL backups")
        subprocess.run(
            [
                pg_dump,
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(output),
                *_postgres_connection_args(url),
            ],
            check=True,
            env=_postgres_environment(url),
        )
    else:
        raise RuntimeError(f"unsupported backup backend: {backend}")

    manifest = _write_manifest(output, backend=backend)
    return {
        "ok": True,
        "backend": backend,
        "backup": str(output),
        "manifest": str(manifest),
    }


def _verify_manifest(backup_path: Path) -> None:
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
    if not manifest_path.is_file():
        raise RuntimeError(f"backup manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    if digest != manifest.get("sha256"):
        raise RuntimeError("backup checksum does not match its manifest")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(
            "backup schema is incompatible with this release: " f"{manifest.get('schema_version')} != {SCHEMA_VERSION}"
        )


def restore(backup_path: Path, *, confirmation: str) -> dict[str, object]:
    if confirmation != "RESTORE":
        raise RuntimeError("restore requires --confirm RESTORE")
    backup_path = backup_path.resolve()
    if not backup_path.is_file():
        raise RuntimeError(f"backup file is missing: {backup_path}")
    _verify_manifest(backup_path)

    url = make_url(get_config().get_db_url())
    backend = url.get_backend_name()
    if backend == "sqlite":
        destination = Path(str(url.database or "")).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup_path) as source:
            integrity = source.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RuntimeError(f"source backup is corrupt: {integrity}")
        with tempfile.NamedTemporaryFile(
            prefix=destination.name + ".restore-",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        try:
            shutil.copy2(backup_path, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    elif backend.startswith("postgresql"):
        pg_restore = shutil.which("pg_restore")
        if not pg_restore:
            raise RuntimeError("pg_restore is required for PostgreSQL restores")
        subprocess.run(
            [
                pg_restore,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                *_postgres_connection_args(url),
                str(backup_path),
            ],
            check=True,
            env=_postgres_environment(url),
        )
    else:
        raise RuntimeError(f"unsupported restore backend: {backend}")
    return {"ok": True, "backend": backend, "restored_from": str(backup_path)}


def pitr_check() -> dict[str, object]:
    engine = _engine()
    try:
        backend = engine.url.get_backend_name()
        if not backend.startswith("postgresql"):
            return {
                "ok": False,
                "backend": backend,
                "reason": "PITR requires PostgreSQL WAL archiving or a managed equivalent",
            }
        with engine.connect() as connection:
            values = {
                name: connection.execute(text(f"SHOW {name}")).scalar()
                for name in ("wal_level", "archive_mode", "archive_command")
            }
        archive_enabled = str(values["archive_mode"]).lower() in {"on", "always"}
        return {
            "ok": archive_enabled,
            "backend": backend,
            **values,
            "note": (
                "Managed PostgreSQL may expose provider PITR separately; "
                "verify retention and perform a restore drill."
            ),
        }
    finally:
        engine.dispose()


def main() -> int:
    setup_env()
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("migrate")
    subcommands.add_parser("check")
    backup_parser = subcommands.add_parser("backup")
    backup_parser.add_argument("--output", type=Path, required=True)
    restore_parser = subcommands.add_parser("restore")
    restore_parser.add_argument("--input", type=Path, required=True)
    restore_parser.add_argument("--confirm", default="")
    subcommands.add_parser("pitr-check")
    args = parser.parse_args()
    try:
        if args.command == "migrate":
            result = migrate()
        elif args.command == "check":
            result = check()
        elif args.command == "backup":
            result = backup(args.output)
        elif args.command == "restore":
            result = restore(args.input, confirmation=args.confirm)
        else:
            result = pitr_check()
    except Exception as exc:
        result = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
