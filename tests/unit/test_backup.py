"""No Docker, database, network, or operational activation in these tests."""

import io
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest
from pydantic import SecretStr

from ai_trading_lab.operations.backup import (
    RESTORE_DATABASE,
    BackupError,
    BackupTool,
    run_command,
)

SUMMARY = {"migrations": ["0010_runtime"], "runtime_sessions": 1, "runtime_events": 2}
URL = SecretStr("postgresql+psycopg://owner:secret%20value@db:5432/ai_trading_lab")


class Tools:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, str]]] = []
        self.exists = False
        self.fail_dump = False
        self.fail_restore = False
        self.changed = False
        self.queries = 0

    def __call__(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        output: BinaryIO,
        timeout: float,
        max_bytes: int,
        source: BinaryIO | None = None,
    ) -> None:
        self.calls.append((list(argv), dict(env)))
        assert 0 < timeout <= 300 and 0 < max_bytes <= 1024 * 1024 * 1024
        if "pg_dump" in argv:
            output.write(b"PGDMPfixture")
            if self.fail_dump:
                raise BackupError("tool failed")
        elif "pg_restore" in argv:
            assert source is not None and source.read() == b"PGDMPfixture"
            if self.fail_restore:
                raise BackupError("tool failed")
        elif "pg_database" in argv[-1]:
            output.write(b"1" if self.exists else b"")
        elif "CREATE DATABASE" in argv[-1]:
            self.exists = True
        else:
            self.queries += 1
            output.write(
                json.dumps(SUMMARY if not self.changed or self.queries == 1 else {}).encode()
            )


def test_archive_restore_and_secret_free_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PGOPTIONS", "unsafe inherited options")
    tools = Tools()
    tool = BackupTool(URL, "atl-db-1", runner=tools)
    destination = tmp_path / "archive"
    manifest = tool.backup(destination)
    assert manifest["summary"] == SUMMARY
    assert (destination / "archive.dump").stat().st_mode & 0o777 == 0o600
    assert (destination / "manifest.json").stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_mode & 0o777 == 0o700
    result = tool.restore(destination, database=RESTORE_DATABASE)
    assert result["summary"] == SUMMARY and tools.exists
    for argv, env in tools.calls:
        assert "owner" not in argv and "secret value" not in " ".join(argv)
        assert env["PGPASSWORD"] == "secret value" and env["PGUSER"] == "owner"
        assert "PGOPTIONS" not in env
        assert "timeout" in argv and "--signal=KILL" in argv
        assert not any(flag in argv for flag in ("--clean", "DROP", "--create"))
    assert "--format=custom" in tools.calls[1][0]
    restore = next(argv for argv, _ in tools.calls if "pg_restore" in argv)
    assert "--single-transaction" in restore and "--no-owner" in restore
    assert "--no-privileges" in restore


def test_exclusive_destination_preserves_existing_data(tmp_path: Path) -> None:
    tools = Tools()
    destination = tmp_path / "archive"
    destination.mkdir()
    valuable = destination / "manifest.json"
    valuable.write_text("keep")
    with pytest.raises(BackupError, match="exists"):
        BackupTool(URL, "db", runner=tools).backup(destination)
    assert valuable.read_text() == "keep" and not tools.calls


@pytest.mark.parametrize("kind", ["symlink", "parent_symlink"])
def test_refuse_symlink_paths(tmp_path: Path, kind: str) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(BackupError, match="Symlink"):
        BackupTool(URL, "db").backup(link if kind == "symlink" else link / "new")


@pytest.mark.parametrize("failure", ["dump", "source_changed"])
def test_partial_backup_is_removed(tmp_path: Path, failure: str) -> None:
    tools = Tools()
    tools.fail_dump = failure == "dump"
    tools.changed = failure == "source_changed"
    destination = tmp_path / "archive"
    with pytest.raises(BackupError):
        BackupTool(URL, "db", runner=tools).backup(destination)
    assert not destination.exists()


def test_restore_refuses_wrong_or_existing_database(tmp_path: Path) -> None:
    tools = Tools()
    tool = BackupTool(URL, "db", runner=tools)
    destination = tmp_path / "archive"
    tool.backup(destination)
    tools.calls.clear()
    with pytest.raises(BackupError, match="dedicated"):
        tool.restore(destination, database="ai_trading_lab")
    assert not tools.calls
    tools.exists = True
    with pytest.raises(BackupError, match="already exists"):
        tool.restore(destination, database=RESTORE_DATABASE)
    assert len(tools.calls) == 1


def test_failed_restore_preserves_archive_and_created_database(tmp_path: Path) -> None:
    tools = Tools()
    tool = BackupTool(URL, "db", runner=tools)
    destination = tmp_path / "archive"
    tool.backup(destination)
    tools.fail_restore = True
    with pytest.raises(BackupError):
        tool.restore(destination, database=RESTORE_DATABASE)
    assert tools.exists and (destination / "archive.dump").exists()
    assert not any("DROP" in " ".join(argv) for argv, _ in tools.calls)


@pytest.mark.parametrize("damage", ["checksum", "mode", "symlink", "size"])
def test_restore_validates_artifacts_before_database(tmp_path: Path, damage: str) -> None:
    tools = Tools()
    tool = BackupTool(URL, "db", runner=tools, max_bytes=1024)
    destination = tmp_path / "archive"
    tool.backup(destination)
    archive = destination / "archive.dump"
    if damage == "mode":
        archive.chmod(0o644)
    elif damage == "symlink":
        archive.rename(destination / "real")
        archive.symlink_to(destination / "real")
    else:
        archive.write_bytes(b"tampered" if damage == "checksum" else b"x" * 1025)
    tools.calls.clear()
    with pytest.raises(BackupError):
        tool.restore(destination, database=RESTORE_DATABASE)
    assert not tools.calls


@pytest.mark.parametrize("kwargs", [{"timeout": 0}, {"timeout": 301}, {"max_bytes": 10}])
def test_bounds(kwargs: dict[str, int]) -> None:
    with pytest.raises(BackupError):
        BackupTool(
            URL, "db", timeout=kwargs.get("timeout", 60), max_bytes=kwargs.get("max_bytes", 65536)
        )


@pytest.mark.parametrize("container", ["--privileged", "db;anything", "db/exec", ""])
def test_container_identifier(container: str) -> None:
    with pytest.raises(BackupError):
        BackupTool(URL, container)


def test_source_restore_identity_guard() -> None:
    with pytest.raises(BackupError, match="Source"):
        BackupTool(SecretStr(f"postgresql+psycopg://owner:pw@db/{RESTORE_DATABASE}"), "db")


def test_runner_streams_and_bounds_bytes() -> None:
    output = io.BytesIO()
    run_command([sys.executable, "-c", "print('ok')"], dict(os.environ), output, 2, 1024)
    assert output.getvalue() == b"ok\n"
    with pytest.raises(BackupError, match="byte limit"):
        run_command(
            [sys.executable, "-c", "print('x'*2000)"], dict(os.environ), io.BytesIO(), 2, 1024
        )


def test_runner_bounds_silent_process_and_redacts_failure() -> None:
    with pytest.raises(BackupError, match="deadline"):
        run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            dict(os.environ),
            io.BytesIO(),
            0.05,
            1024,
        )
    with pytest.raises(BackupError) as error:
        run_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('secret'); sys.exit(1)"],
            dict(os.environ),
            io.BytesIO(),
            2,
            1024,
        )
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "sqlite:///a",
        "postgresql+psycopg://db/a",
        "postgresql+psycopg://u:p@db/a?options=x",
    ],
)
def test_invalid_connection_is_redacted(url: str) -> None:
    with pytest.raises(BackupError) as error:
        BackupTool(SecretStr(url), "db")
    assert url not in str(error.value)


def test_corrupt_manifest_refused_without_database_calls(tmp_path: Path) -> None:
    tools = Tools()
    tool = BackupTool(URL, "db", runner=tools)
    destination = tmp_path / "archive"
    tool.backup(destination)
    (destination / "manifest.json").write_text("[1]")
    tools.calls.clear()
    with pytest.raises(BackupError, match="manifest"):
        tool.restore(destination, database=RESTORE_DATABASE)
    assert not tools.calls


def test_restore_summary_mismatch_preserves_evidence(tmp_path: Path) -> None:
    tools = Tools()
    tool = BackupTool(URL, "db", runner=tools)
    destination = tmp_path / "archive"
    tool.backup(destination)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["summary"] = {"different": 1}
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(BackupError, match="differs"):
        tool.restore(destination, database=RESTORE_DATABASE)
    assert tools.exists and (destination / "archive.dump").exists()


def test_runner_missing_executable_and_closed_stdout_timeout() -> None:
    with pytest.raises(BackupError, match="could not complete"):
        run_command(["/nonexistent/pg_dump"], {}, io.BytesIO(), 1, 1024)
    with pytest.raises(BackupError, match="could not complete"):
        run_command(
            [sys.executable, "-c", "import os,time; os.close(1); time.sleep(30)"],
            dict(os.environ),
            io.BytesIO(),
            0.1,
            1024,
        )
