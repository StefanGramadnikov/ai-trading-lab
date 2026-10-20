"""Bounded PostgreSQL archives and non-destructive isolated restore verification."""

import argparse
import hashlib
import io
import json
import os
import re
import selectors
import stat
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, Protocol

from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from ai_trading_lab.backtesting.models import runtime_fingerprint
from ai_trading_lab.config import Settings

RESTORE_DATABASE = "ai_trading_lab_restore_test"
SUMMARY_SQL = """SELECT json_build_object(
'migrations', (SELECT json_agg(version_num ORDER BY version_num) FROM alembic_version),
'raw_source_events', (SELECT count(*) FROM raw_source_events),
'paper_portfolios', (SELECT count(*) FROM paper_portfolios),
'paper_events', (SELECT count(*) FROM paper_events),
'experiments', (SELECT count(*) FROM experiments),
'runtime_sessions', (SELECT count(*) FROM runtime_sessions),
'runtime_events', (SELECT count(*) FROM runtime_events))::text;"""


class BackupError(ValueError):
    """Safe diagnostic which never contains subprocess output or database credentials."""


class Runner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        output: BinaryIO,
        timeout: float,
        max_bytes: int,
        source: BinaryIO | None = None,
    ) -> None: ...


def run_command(
    argv: Sequence[str],
    env: Mapping[str, str],
    output: BinaryIO,
    timeout: float,
    max_bytes: int,
    source: BinaryIO | None = None,
) -> None:
    """Stream stdout under byte/deadline caps; discard potentially sensitive stderr."""
    try:
        with subprocess.Popen(
            argv,
            env=env,
            stdin=source or subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            assert process.stdout is not None
            try:
                deadline = time.monotonic() + timeout
                size = 0
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise BackupError("PostgreSQL tool deadline exceeded")
                        chunk = os.read(process.stdout.fileno(), min(65536, max_bytes - size + 1))
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > max_bytes:
                            raise BackupError("PostgreSQL tool output exceeded byte limit")
                        output.write(chunk)
                if process.wait(timeout=max(0.001, deadline - time.monotonic())) != 0:
                    raise BackupError("PostgreSQL tool failed; inspect local server privately")
            except BaseException:
                process.kill()
                process.wait()
                raise
    except (OSError, subprocess.SubprocessError):
        raise BackupError("PostgreSQL tool could not complete") from None


class BackupTool:
    def __init__(
        self,
        database_url: SecretStr,
        container: str,
        *,
        timeout: float = 60,
        max_bytes: int = 64 * 1024 * 1024,
        runner: Runner = run_command,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
            raise BackupError("Invalid PostgreSQL container identifier")
        if not 0 < timeout <= 300 or not 1024 <= max_bytes <= 1024 * 1024 * 1024:
            raise BackupError("Invalid backup resource limits")
        try:
            url = make_url(database_url.get_secret_value())
            if (
                url.drivername != "postgresql+psycopg"
                or not url.host
                or not url.database
                or not url.username
                or url.query
            ):
                raise ValueError
            self._connection = {
                "PGHOST": url.host,
                "PGPORT": str(url.port or 5432),
                "PGUSER": url.username,
                "PGPASSWORD": url.password or "",
                "PGDATABASE": url.database,
                "PGCONNECT_TIMEOUT": str(max(1, int(timeout))),
            }
        except (ValueError, TypeError, ArgumentError):
            raise BackupError("Expected a plain PostgreSQL psycopg URL") from None
        if url.database == RESTORE_DATABASE:
            raise BackupError("Source must differ from isolated restore database")
        self.container, self.timeout, self.max_bytes, self.runner = (
            container,
            timeout,
            max_bytes,
            runner,
        )

    def _run(
        self,
        tool: str,
        args: Sequence[str],
        output: BinaryIO,
        *,
        database: str | None = None,
        source: BinaryIO | None = None,
        max_bytes: int = 65536,
    ) -> None:
        connection = dict(self._connection)
        connection["PGDATABASE"] = database or connection["PGDATABASE"]
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "HOME",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_CONFIG",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
                "TMPDIR",
            }
        }
        env.update(connection)
        argv = ["docker", "exec", "-i"]
        for key in connection:
            argv.extend(["--env", key])
        argv.extend([self.container, "timeout", "--signal=KILL", str(self.timeout), tool, *args])
        self.runner(argv, env, output, self.timeout, max_bytes, source)

    def _query(self, sql: str, database: str | None = None) -> str:
        output = io.BytesIO()
        self._run(
            "psql",
            ["-X", "--no-password", "-v", "ON_ERROR_STOP=1", "-At", "-c", sql],
            output,
            database=database,
        )
        try:
            return output.getvalue().decode("utf-8").strip()
        except UnicodeError:
            raise BackupError("Invalid verification output") from None

    def _summary(self, database: str | None = None) -> object:
        try:
            value: object = json.loads(self._query(SUMMARY_SQL, database))
            if (
                not isinstance(value, dict)
                or not isinstance(value.get("migrations"), list)
                or not value["migrations"]
                or not all(isinstance(version, str) for version in value["migrations"])
                or any(
                    type(count) is not int or count < 0
                    for key, count in value.items()
                    if key != "migrations"
                )
            ):
                raise BackupError("Invalid database verification summary")
            return value
        except json.JSONDecodeError:
            raise BackupError("Invalid database verification summary") from None

    def backup(self, destination: Path) -> dict[str, object]:
        _safe_parents(destination)
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError:
            raise BackupError("Backup destination already exists") from None
        partial = destination / "archive.partial"
        try:
            before = self._summary()
            with open(partial, "xb", opener=_private_file) as output:
                self._run(
                    "pg_dump",
                    ["--no-password", "--format=custom", "--no-owner", "--no-privileges"],
                    output,
                    max_bytes=self.max_bytes,
                )
                output.flush()
                os.fsync(output.fileno())
            with partial.open("rb") as header:
                magic = header.read(5)
            if magic != b"PGDMP":
                raise BackupError("Archive is not PostgreSQL custom format")
            if self._summary() != before:
                raise BackupError("Source changed during backup; stop writers and retry")
            manifest: dict[str, object] = {
                "format": 1,
                "created_at_utc": datetime.now(UTC).isoformat(),
                "archive_sha256": _digest(partial),
                "archive_bytes": partial.stat().st_size,
                "source_database": self._connection["PGDATABASE"],
                "runtime_fingerprint": runtime_fingerprint(),
                "summary": before,
            }
            manifest_path = destination / "manifest.json"
            with open(manifest_path, "x", encoding="utf-8", opener=_private_file) as handle:
                json.dump(manifest, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            partial.rename(destination / "archive.dump")
            return manifest
        except BaseException:
            for name in ("archive.partial", "manifest.json"):
                (destination / name).unlink(missing_ok=True)
            destination.rmdir()
            raise

    def restore(self, destination: Path, *, database: str) -> dict[str, object]:
        if database != RESTORE_DATABASE:
            raise BackupError("Restore requires dedicated ai_trading_lab_restore_test database")
        _safe_parents(destination)
        directory_metadata = destination.lstat()
        if (
            not stat.S_ISDIR(directory_metadata.st_mode)
            or directory_metadata.st_uid != os.getuid()
            or stat.S_IMODE(directory_metadata.st_mode) != 0o700
        ):
            raise BackupError("Backup directory requires owner-only permissions")
        archive, manifest_path = destination / "archive.dump", destination / "manifest.json"
        for path, cap in ((archive, self.max_bytes), (manifest_path, 65536)):
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > cap
            ):
                raise BackupError("Backup requires owner-only regular files within byte limits")
        try:
            manifest = json.loads(manifest_path.read_text())
            if (
                not isinstance(manifest, dict)
                or manifest.get("format") != 1
                or manifest.get("archive_sha256") != _digest(archive)
                or manifest.get("archive_bytes") != archive.stat().st_size
            ):
                raise ValueError
        except (ValueError, UnicodeError):
            raise BackupError("Backup manifest or checksum invalid") from None
        exists = self._query(
            "SELECT 1 FROM pg_database WHERE datname = 'ai_trading_lab_restore_test';", "postgres"
        )
        if exists:
            raise BackupError("Restore database already exists; nothing was modified")
        # CREATE DATABASE itself refuses races. Never DROP, CLEAN or restore elsewhere.
        self._query('CREATE DATABASE "ai_trading_lab_restore_test" TEMPLATE template0;', "postgres")
        with archive.open("rb") as source:
            self._run(
                "pg_restore",
                [
                    "--no-password",
                    "--single-transaction",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    "--dbname",
                    database,
                ],
                io.BytesIO(),
                database=database,
                source=source,
            )
        summary = self._summary(database)
        if summary != manifest.get("summary"):
            raise BackupError(
                "Restored schema/count summary differs; preserve database for inspection"
            )
        return {
            "database": database,
            "archive_sha256": manifest["archive_sha256"],
            "summary": summary,
            "source_runtime_fingerprint": manifest.get("runtime_fingerprint"),
        }


def _private_file(path: str, flags: int) -> int:
    return os.open(path, flags | os.O_NOFOLLOW, 0o600)


def _safe_parents(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise BackupError("Symlink backup paths are refused")


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--container", required=True)
    parser.add_argument("--database", default=RESTORE_DATABASE)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024)
    args = parser.parse_args()
    try:
        tool = BackupTool(
            Settings().database_url, args.container, timeout=args.timeout, max_bytes=args.max_bytes
        )
        if args.action == "backup":
            result = tool.backup(args.directory)
        else:
            result = tool.restore(args.directory, database=args.database)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, OSError):
        parser.exit(1, "Backup/restore refused or failed; preserve source and inspect locally.\n")


if __name__ == "__main__":
    main()
