#!/usr/bin/env python3
"""Small AWS SSM helpers for local OMEGA release operations.

These helpers intentionally shell out to the AWS CLI instead of adding a boto3
runtime dependency to the repo scripts. They also redact command output before
anything is persisted under docs/release-evidence.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_REGION = (
    os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
)
TERMINAL_STATUSES = {"Success", "Cancelled", "TimedOut", "Failed", "Cancelling"}
MAX_INLINE_SCRIPT_BYTES = int(
    os.environ.get("OMEGA_SSM_MAX_INLINE_SCRIPT_BYTES", "12000")
)
SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(authorization\s*:\s*)([^\r\n]+)"),
    re.compile(r"(?i)(cookie\s*:\s*)([^\r\n]+)"),
    re.compile(r"(?i)(set-cookie\s*:\s*)([^\r\n]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(?i)(://[^:/\s]+:)([^@\s]+)(@)"),
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|ACCESS_KEY|DATABASE_URL|DSN)[A-Z0-9_]*\s*=\s*)([^\s]+)"
    ),
]


@dataclass(frozen=True)
class SsmResult:
    command_id: str
    instance_id: str
    region: str
    status: str
    response_code: int | None
    stdout: str
    stderr: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_stamp() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def redact(text: str | None) -> str:
    redacted = text or ""
    for pattern in SENSITIVE_PATTERNS:
        if pattern.pattern.startswith("(?i)(://"):
            redacted = pattern.sub(r"\1<redacted>\3", redacted)
        else:
            redacted = pattern.sub(r"\1<redacted>", redacted)
    return redacted


def run_local(cmd: list[str], *, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )


def aws_json(
    args: list[str],
    *,
    region: str = DEFAULT_REGION,
    timeout: int = 60,
    attempts: int = 1,
) -> dict[str, Any]:
    cmd = ["aws", "--region", region, *args, "--output", "json"]
    last_error = ""
    for attempt in range(1, max(1, attempts) + 1):
        try:
            result = run_local(cmd, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            last_error = f"AWS CLI timed out after {timeout} seconds"
            if attempt >= attempts:
                raise RuntimeError(last_error) from exc
            time.sleep(min(2.0 * attempt, 8.0))
            continue
        if result.returncode == 0:
            try:
                return json.loads(result.stdout or "{}")
            except json.JSONDecodeError as exc:
                last_error = (
                    f"AWS CLI returned non-JSON output: {redact(result.stdout)[:500]}"
                )
                if attempt >= attempts:
                    raise RuntimeError(last_error) from exc
        else:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part)
            last_error = (
                redact(output) or f"AWS CLI failed with exit code {result.returncode}"
            )
            if attempt >= attempts:
                raise RuntimeError(last_error)
        time.sleep(min(2.0 * attempt, 8.0))
    raise RuntimeError(last_error or "AWS CLI failed")


def resolve_instance_id(
    region: str = DEFAULT_REGION, explicit: str | None = None
) -> str:
    if explicit:
        return explicit
    env_value = os.environ.get("AWS_APP_INSTANCE_ID") or os.environ.get(
        "OMEGA_AWS_APP_INSTANCE_ID"
    )
    if env_value:
        return env_value
    tag_name = os.environ.get("OMEGA_AWS_APP_INSTANCE_TAG_NAME", "modecissions-app")
    payload = aws_json(
        [
            "ec2",
            "describe-instances",
            "--filters",
            f"Name=tag:Name,Values={tag_name}",
            "Name=instance-state-name,Values=running",
            "--query",
            "Reservations[].Instances[].InstanceId",
        ],
        region=region,
    )
    values = payload if isinstance(payload, list) else []
    if len(values) != 1:
        raise RuntimeError(
            f"Expected exactly one running EC2 instance with tag Name={tag_name}; found {len(values)}. "
            "Set AWS_APP_INSTANCE_ID explicitly."
        )
    return str(values[0])


def _encoded_bash_commands(script: str, *, chunk_size: int = 3500) -> list[str]:
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    commands = [
        "set -eu",
        'tmp_script="/tmp/omega-ssm-script.$$"',
        'tmp_b64="/tmp/omega-ssm-script.$$.b64"',
        'trap \'rm -f "$tmp_script" "$tmp_b64"\' EXIT',
        ': > "$tmp_b64"',
    ]
    for index in range(0, len(encoded), chunk_size):
        chunk = encoded[index : index + chunk_size]
        commands.append(
            "cat >> \"$tmp_b64\" <<'OMEGA_SSM_CHUNK'\n" + chunk + "\nOMEGA_SSM_CHUNK"
        )
    commands.extend(
        [
            'base64 -d "$tmp_b64" > "$tmp_script"',
            'bash "$tmp_script"',
        ]
    )
    return commands


def _encoded_bash_command(script: str) -> str:
    return "\n".join(_encoded_bash_commands(script))


def _run_ssm_commands(
    *,
    region: str,
    instance_id: str,
    commands: list[str],
    comment: str,
    timeout_seconds: int = 600,
    poll_seconds: float = 2.0,
    send_attempts: int = 1,
) -> SsmResult:
    params_file = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
    try:
        with params_file:
            json.dump(
                {"commands": commands, "executionTimeout": [str(timeout_seconds)]},
                params_file,
            )
        sent = aws_json(
            [
                "ssm",
                "send-command",
                "--document-name",
                "AWS-RunShellScript",
                "--instance-ids",
                instance_id,
                "--comment",
                comment[:100],
                "--parameters",
                f"file://{params_file.name}",
                "--timeout-seconds",
                str(timeout_seconds),
            ],
            region=region,
            timeout=60,
            attempts=send_attempts,
        )
    finally:
        try:
            os.unlink(params_file.name)
        except FileNotFoundError:
            pass
    command_id = str(sent["Command"]["CommandId"])
    deadline = time.monotonic() + timeout_seconds + 60
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last = aws_json(
                [
                    "ssm",
                    "get-command-invocation",
                    "--command-id",
                    command_id,
                    "--instance-id",
                    instance_id,
                ],
                region=region,
                timeout=60,
                attempts=3,
            )
        except RuntimeError as exc:
            if "InvocationDoesNotExist" not in str(exc):
                raise
            time.sleep(poll_seconds)
            continue
        if str(last.get("Status")) in TERMINAL_STATUSES:
            return SsmResult(
                command_id=command_id,
                instance_id=instance_id,
                region=region,
                status=str(last.get("Status") or "Unknown"),
                response_code=last.get("ResponseCode"),
                stdout=redact(str(last.get("StandardOutputContent") or "")),
                stderr=redact(str(last.get("StandardErrorContent") or "")),
            )
        time.sleep(poll_seconds)
    raise TimeoutError(f"SSM command {command_id} did not finish before timeout")


def _send_large_ssm_script(
    *,
    region: str,
    instance_id: str,
    script: str,
    comment: str,
    timeout_seconds: int,
    poll_seconds: float,
    send_attempts: int,
) -> SsmResult:
    token = f"{int(time.time())}-{os.getpid()}"
    remote_script = f"/tmp/omega-ssm-large-{token}.sh"
    remote_b64 = f"{remote_script}.b64"
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")

    upload = _run_ssm_commands(
        region=region,
        instance_id=instance_id,
        comment=f"{comment[:80]} upload-init",
        timeout_seconds=120,
        poll_seconds=poll_seconds,
        send_attempts=3,
        commands=[
            "set -eu",
            f"rm -f {remote_script} {remote_b64} {remote_b64}.part-*",
            f": > {remote_b64}",
        ],
    )
    if upload.status != "Success" or upload.response_code != 0:
        return upload

    chunk_size = 4000
    for index in range(0, len(encoded), chunk_size):
        chunk_no = index // chunk_size + 1
        chunk_name = f"{chunk_no:04d}"
        chunk = encoded[index : index + chunk_size]
        upload = _run_ssm_commands(
            region=region,
            instance_id=instance_id,
            comment=f"{comment[:80]} upload-{chunk_no}",
            timeout_seconds=120,
            poll_seconds=poll_seconds,
            send_attempts=3,
            commands=[
                "set -eu",
                f"cat > {remote_b64}.part-{chunk_name} <<'OMEGA_SSM_LARGE_CHUNK'\n{chunk}\nOMEGA_SSM_LARGE_CHUNK",
            ],
        )
        if upload.status != "Success" or upload.response_code != 0:
            return upload

    return _run_ssm_commands(
        region=region,
        instance_id=instance_id,
        comment=comment,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        send_attempts=send_attempts,
        commands=[
            "set -eu",
            f"trap 'rm -f {remote_script} {remote_b64} {remote_b64}.part-*' EXIT",
            f"cat {remote_b64}.part-* > {remote_b64}",
            f"base64 -d {remote_b64} > {remote_script}",
            f"chmod 700 {remote_script}",
            f"bash {remote_script}",
        ],
    )


def send_ssm_script(
    *,
    region: str,
    instance_id: str,
    script: str,
    comment: str,
    timeout_seconds: int = 600,
    poll_seconds: float = 2.0,
    send_attempts: int = 1,
) -> SsmResult:
    if len(script.encode("utf-8")) > MAX_INLINE_SCRIPT_BYTES:
        return _send_large_ssm_script(
            region=region,
            instance_id=instance_id,
            script=script,
            comment=comment,
            timeout_seconds=timeout_seconds,
            poll_seconds=poll_seconds,
            send_attempts=send_attempts,
        )
    return _run_ssm_commands(
        region=region,
        instance_id=instance_id,
        commands=_encoded_bash_commands(script),
        comment=comment,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        send_attempts=send_attempts,
    )


def send_ssm_command_lines(
    *,
    region: str,
    instance_id: str,
    commands: list[str],
    comment: str,
    timeout_seconds: int = 600,
    poll_seconds: float = 2.0,
    send_attempts: int = 1,
) -> SsmResult:
    return _run_ssm_commands(
        region=region,
        instance_id=instance_id,
        commands=commands,
        comment=comment,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        send_attempts=send_attempts,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
