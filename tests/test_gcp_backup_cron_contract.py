from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CRON_FILE = REPO / "infra" / "terraform-gcp" / "files" / "omega-backup.cron"
STARTUP_TFTPL = REPO / "infra" / "terraform-gcp" / "templates" / "startup.sh.tftpl"
BACKUP_SH = REPO / "infra" / "terraform" / "deploy" / "backup.sh"


def _job_lines() -> list[str]:
    lines = CRON_FILE.read_text(encoding="utf-8").splitlines()
    return [
        line
        for line in lines
        if line.strip()
        and not line.lstrip().startswith("#")
        and not re.match(r"^[A-Z_]+=", line)
    ]


def test_cron_file_shape():
    text = CRON_FILE.read_text(encoding="utf-8")
    assert text.endswith("\n"), "cron.d files must end with a newline"
    assert "SHELL=/bin/bash" in text
    assert re.search(r"^PATH=\S+$", text, re.M)
    assert len(_job_lines()) == 1, "exactly one scheduled job"


def test_cron_schedule_is_valid_daily():
    fields = _job_lines()[0].split()
    minute, hour, dom, month, dow, user = fields[:6]
    assert 0 <= int(minute) <= 59
    assert 0 <= int(hour) <= 23
    assert (dom, month, dow) == ("*", "*", "*"), "daily schedule"
    assert user == "root"


def test_cron_command_contract():
    command = " ".join(_job_lines()[0].split()[6:])
    assert "BACKUP_STORAGE_BACKEND=gcs" in command, "GCS backend must be pinned"
    assert "bash /opt/modecissions/current/infra/terraform/deploy/backup.sh" in command
    assert "BACKUP_ENV_FILE=infra/.env" in command
    assert "-f infra/docker-compose.yml -f infra/docker-compose.gcp.yml" in command
    assert command.endswith(">> /var/log/omega-backup.log 2>&1"), "must log"


def test_provisioning_installs_cron():
    text = STARTUP_TFTPL.read_text(encoding="utf-8")
    assert "infra/terraform-gcp/files/omega-backup.cron" in text
    assert "/etc/cron.d/omega-backup" in text
    assert re.search(r"install -m 0644 -o root -g root", text)
    target = "/etc/cron.d/omega-backup"
    assert "." not in target.rsplit("/", 1)[-1], "cron.d ignores filenames with dots"


def test_backup_script_supports_cron_invocation():
    text = BACKUP_SH.read_text(encoding="utf-8")
    assert "BACKUP_ENV_FILE" in text
    assert "BACKUP_COMPOSE_FILES" in text
    assert "gcloud storage cp" in text
    assert 'GCS_BUCKET:?GCS_BUCKET is required' in text
