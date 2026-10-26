"""The deployment files keep the runbook's promises: paper only, nothing published, no secrets."""

import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "compose.prod.yaml").read_text())
SERVICES = COMPOSE["services"]
SCRIPTS = sorted((ROOT / "deploy").glob("*.sh"))


def test_the_production_stack_publishes_no_port_at_all() -> None:
    assert set(SERVICES) == {"db", "migrate", "live"}
    for name, service in SERVICES.items():
        assert "ports" not in service, name
        assert "network_mode" not in service, name
        assert service.get("privileged") is not True, name


def test_the_loop_is_paper_only_whatever_the_env_file_says() -> None:
    environment = SERVICES["live"]["environment"]
    assert environment["ATL_EXECUTION_MODE"] == "PAPER_ONLY"
    assert SERVICES["migrate"]["environment"]["ATL_EXECUTION_MODE"] == "PAPER_ONLY"
    # Nothing is interpolated into the mode, so a typo or a stray variable cannot change it.
    assert "$" not in environment["ATL_EXECUTION_MODE"]


def test_the_ai_strategy_is_off_and_unfunded_unless_the_env_file_turns_it_on() -> None:
    environment = SERVICES["live"]["environment"]
    assert environment["ATL_AI_ENABLED"] == "${ATL_AI_ENABLED:-false}"
    assert environment["ATL_AI_TOTAL_LIMIT_USD"] == "${ATL_AI_TOTAL_LIMIT_USD:-0}"
    assert environment["ATL_AI_REVIEWED_PRICES"] == "${ATL_AI_REVIEWED_PRICES:-[]}"
    assert environment["ATL_OPENAI_API_KEY"] == "${ATL_OPENAI_API_KEY:-}"


def test_the_loop_restarts_checks_itself_and_is_locked_down() -> None:
    live = SERVICES["live"]
    assert live["command"] == ["/app/.venv/bin/atl-live", "run", "--until-stopped"]
    assert live["restart"] == "unless-stopped"
    assert live["read_only"] is True and live["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in live["security_opt"]
    probe = live["healthcheck"]["test"]
    assert probe[:3] == ["CMD", "/app/.venv/bin/atl-live", "health"]
    assert live["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert live["depends_on"]["db"]["condition"] == "service_healthy"
    assert str(live["stop_grace_period"]).endswith("s")


def test_the_database_password_is_mandatory_and_never_literal() -> None:
    text = (ROOT / "compose.prod.yaml").read_text()
    assert text.count("${POSTGRES_PASSWORD:?") == 2
    assert not re.search(r"PASSWORD:\s*[A-Za-z0-9]", text)
    assert "local_example_only" not in text
    assert SERVICES["db"]["restart"] == "unless-stopped"
    assert "volumes" in SERVICES["db"]


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_every_service_rotates_its_logs(name: str) -> None:
    options = SERVICES[name]["logging"]["options"]
    assert options["max-size"] and options["max-file"]


def test_the_image_provides_the_commands_the_stack_runs() -> None:
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert "atl-live" in scripts
    assert (ROOT / "alembic.ini").exists() and (ROOT / "migrations").is_dir()
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "alembic.ini" in dockerfile and "migrations" in dockerfile


def test_the_env_example_carries_no_secret_and_keeps_everything_off() -> None:
    text = (ROOT / "deploy" / "env.prod.example").read_text()
    assert not re.search(r"sk-[A-Za-z0-9_-]{8,}", text)
    values = dict(
        line.split("=", 1)
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["ATL_AI_ENABLED"] == "false" and values["ATL_AI_TOTAL_LIMIT_USD"] == "0"
    assert values["ATL_OPENAI_API_KEY"] == "" and values["ATL_AI_REVIEWED_PRICES"] == "[]"
    assert "EXECUTION_MODE" not in text.replace("PAPER", "")  # no way to set it from here


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_are_strict_executable_and_parse(script: Path) -> None:
    text = script.read_text()
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert os.access(script, os.X_OK)
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_backups_verify_before_replacing_and_prune_by_count_never_by_age() -> None:
    backup = (ROOT / "deploy" / "backup.sh").read_text()
    assert "exec -T db" in backup and "--env-file" in backup and "umask 077" in backup
    assert ".partial" in backup and "trap 'rm -f \"$partial\"' EXIT" in backup
    # The dump is listed as an archive and must contain our tables before it is kept.
    assert "pg_restore -l" in backup and "live_decisions" in backup
    assert backup.index("pg_restore -l") < backup.index('mv "$partial"')
    # Rotation keeps the newest N by name; a clock jump cannot delete good backups.
    assert "-mtime" not in backup and "tail -n +$((keep + 1))" in backup
    assert "free" in backup and "512" in backup
    # The checksum names the file relatively, so it verifies on the Mac as well as the server.
    assert '(cd "$backup_dir" && sha256sum "$(basename "$target")"' in backup


def test_the_restore_test_never_touches_live_data_and_fails_on_a_bad_restore() -> None:
    restore = (ROOT / "deploy" / "restore-test.sh").read_text()
    assert "sha256sum -c" in restore and "dropdb" in restore and "restore check ok" in restore
    (line,) = [ln for ln in restore.splitlines() if "pg_restore" in ln and "dc sh" in ln]
    assert "-d $scratch" in line and "POSTGRES_DB" not in line and "--exit-on-error" in line
    assert "restored -gt $live" in restore.replace('"', "") or "-gt" in restore
    assert "restored empty although" in restore


def test_the_bootstrap_opens_ssh_only_installs_the_timer_and_leaves_ssh_settings_alone() -> None:
    text = (ROOT / "deploy" / "bootstrap.sh").read_text()
    assert "ufw default deny incoming" in text and "ufw allow OpenSSH" in text
    assert "ufw allow 80" not in text and "ufw allow 443" not in text
    assert "sshd_config" not in text and "PasswordAuthentication" not in text
    assert "enable --now atl-backup.timer" in text and "force-confold" in text
    assert "sudoers" not in text  # the deployment account gets no sudo


def test_the_timer_runs_the_backup_as_the_deployment_account() -> None:
    service = (ROOT / "deploy" / "atl-backup.service").read_text()
    assert "ExecStart=/opt/atl/ai-trading-lab/deploy/backup.sh" in service
    assert "User=atl" in service and "Group=atl" in service
    timer = (ROOT / "deploy" / "atl-backup.timer").read_text()
    assert "OnCalendar=" in timer and "Persistent=true" in timer


def test_the_runbook_only_asks_for_commands_the_deployment_account_can_run() -> None:
    text = (ROOT / "docs" / "30-deployment-runbook.md").read_text()
    commands = [ln for ln in text.splitlines() if ln.strip().startswith("sudo ")]
    assert commands == []
    assert "scp -r deploy" in text and "run --rm live" not in text
