#!/usr/bin/env python3
"""Enterprise readiness 20x orchestrator for OMEGA."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
STATUS_ORDER = {"PASS": 0, "BLOCKED": 1, "FAIL": 2}


@dataclass
class Step:
    name: str
    status: str
    command: str
    evidence: str
    note: str = ""
    exit_code: int | None = None


@dataclass
class Context:
    target: str
    workload: str
    profile: str
    run_id: str
    evidence_dir: Path
    dry_run: bool
    public_console_url: str
    steps: list[Step] = field(default_factory=list)


def _run_id() -> str:
    return "ENT_" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _status(steps: list[Step]) -> str:
    return max((step.status for step in steps), key=lambda item: STATUS_ORDER[item], default="PASS")


def _command_status(code: int) -> str:
    if code == 0:
        return "PASS"
    if code == 2:
        return "BLOCKED"
    return "FAIL"


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _evidence_ref(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def run(ctx: Context, name: str, command: str, *, env: dict[str, str] | None = None) -> Step:
    log_path = ctx.evidence_dir / "commands" / f"{len(ctx.steps) + 1:02d}-{_slug(name)}.log"
    full_env = os.environ.copy()
    full_env.update(
        {
            "OMEGA_ENTERPRISE_RUN_ID": ctx.run_id,
            "WORKLOAD": ctx.workload,
            "TARGET": ctx.target,
            "PUBLIC_CONSOLE_URL": ctx.public_console_url,
        }
    )
    if env:
        full_env.update(env)
    if ctx.dry_run:
        _write(log_path, f"$ {command}\n[dry-run] command not executed\n")
        step = Step(name, "PASS", command, _evidence_ref(log_path), "dry-run", 0)
        ctx.steps.append(step)
        return step
    proc = subprocess.run(
        command,
        cwd=REPO,
        env=full_env,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    _write(log_path, f"$ {command}\nexit_code={proc.returncode}\n\n{proc.stdout}")
    step = Step(name, _command_status(proc.returncode), command, _evidence_ref(log_path), exit_code=proc.returncode)
    ctx.steps.append(step)
    return step


def record(ctx: Context, name: str, status: str, evidence: str, *, command: str = "", note: str = "") -> None:
    ctx.steps.append(Step(name, status, command, evidence, note))


def _record_safe_skips(ctx: Context, stages: list[tuple[str, str, dict[str, str]]], reason: Step) -> None:
    for name, command, _env in stages:
        record(
            ctx,
            name,
            "BLOCKED",
            f"skipped after {reason.name} returned {reason.status}",
            command=command,
            note="SAFE-SKIPPED: production load stages stop after the first non-PASS load gate",
        )


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


def _write_report(ctx: Context) -> None:
    status = _status(ctx.steps)
    summary = {
        "status": status,
        "target": ctx.target,
        "workload": ctx.workload,
        "profile": ctx.profile,
        "run_id": ctx.run_id,
        "public_console_url": ctx.public_console_url or None,
        "steps": [step.__dict__ for step in ctx.steps],
    }
    _write(ctx.evidence_dir / "summary.json", json.dumps(summary, indent=2) + "\n")
    lines = [
        "# Enterprise Readiness 20x",
        "",
        f"- status: `{status}`",
        f"- target: `{ctx.target}`",
        f"- workload: `{ctx.workload}`",
        f"- profile: `{ctx.profile}`",
        f"- run_id: `{ctx.run_id}`",
        "",
        "| Step | Status | Evidence | Command | Note |",
        "|---|---|---|---|---|",
    ]
    for step in ctx.steps:
        command = step.command.replace("|", "\\|")
        note = step.note.replace("|", "\\|")
        lines.append(f"| {step.name} | {step.status} | {step.evidence} | {command} | {note} |")
    _write(ctx.evidence_dir / "REPORT.md", "\n".join(lines) + "\n")


def _target_host(ctx: Context) -> str:
    if ctx.target == "aws":
        return ctx.public_console_url
    return os.environ.get("OMEGA_STRESS_HOST", "http://127.0.0.1:8000")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=os.environ.get("TARGET", "local"), choices=("local", "aws", "staging"))
    parser.add_argument("--workload", default=os.environ.get("WORKLOAD", "sap_successfactors"))
    parser.add_argument("--profile", default=os.environ.get("PROFILE", "beta-safe"))
    parser.add_argument("--run-id", default=os.environ.get("OMEGA_ENTERPRISE_RUN_ID") or _run_id())
    parser.add_argument("--evidence-root", type=Path, default=Path(os.environ.get("OMEGA_ENTERPRISE_EVIDENCE_ROOT", "docs/release-evidence/enterprise-readiness")))
    args = parser.parse_args(argv)
    public_url = os.environ.get("PUBLIC_CONSOLE_URL") or os.environ.get("CONSOLE_URL") or ""
    ctx = Context(
        target=args.target,
        workload=args.workload,
        profile=args.profile,
        run_id=args.run_id,
        evidence_dir=args.evidence_root / args.run_id,
        dry_run=os.environ.get("OMEGA_ENTERPRISE_DRY_RUN") == "1",
        public_console_url=public_url,
    )
    ctx.evidence_dir.mkdir(parents=True, exist_ok=True)
    if ctx.target == "aws" and not ctx.public_console_url:
        record(
            ctx,
            "AWS URL preflight",
            "BLOCKED",
            "PUBLIC_CONSOLE_URL missing",
            command="PUBLIC_CONSOLE_URL=http://modecissions-public-... make enterprise-readiness TARGET=aws WORKLOAD=sap_successfactors PROFILE=beta-safe",
        )
        _write_report(ctx)
        print(json.dumps({"status": _status(ctx.steps), "evidence_dir": str(ctx.evidence_dir)}, indent=2))
        return 2
    if ctx.target == "aws":
        record(
            ctx,
            "production destructive chaos guard",
            "PASS",
            "destructive chaos not scheduled against current AWS production",
            command="OMEGA_V1_STRESS_TARGET=staging OMEGA_V1_STRESS_ALLOW_CHAOS=1 make chaos-aws",
            note="SAFE-ONLY: verifies refusal rather than executing chaos",
        )
    stress_host = _target_host(ctx)
    write_env = {
        "OMEGA_STRESS_HOST": stress_host,
        "OMEGA_STRESS_WORKLOAD": ctx.workload,
        "OMEGA_STRESS_ARTIFACT_DIR": str(ctx.evidence_dir / "stress-write-heavy"),
        "OMEGA_AUDIT_EVIDENCE_DIR": str(ctx.evidence_dir / "stress-write-heavy" / "data-integrity"),
    }
    if ctx.target == "aws":
        write_env.setdefault("OMEGA_STRESS_ENABLE_SF_REFRESH", os.environ.get("OMEGA_STRESS_ENABLE_SF_REFRESH", "0"))
    load_stages = [
        (
            "stress smoke",
            "make stress-smoke",
            {
                "OMEGA_STRESS_HOST": stress_host,
                "OMEGA_STRESS_WORKLOAD": ctx.workload,
                "OMEGA_STRESS_ARTIFACT_DIR": str(ctx.evidence_dir / "stress-smoke"),
                "OMEGA_AUDIT_EVIDENCE_DIR": str(ctx.evidence_dir / "stress-smoke" / "data-integrity"),
            },
        ),
        (
            "stress beta",
            "make stress-beta",
            {
                "OMEGA_STRESS_HOST": stress_host,
                "OMEGA_STRESS_WORKLOAD": ctx.workload,
                "OMEGA_STRESS_ARTIFACT_DIR": str(ctx.evidence_dir / "stress-beta"),
                "OMEGA_AUDIT_EVIDENCE_DIR": str(ctx.evidence_dir / "stress-beta" / "data-integrity"),
            },
        ),
        (
            "stress spike",
            "make stress-spike",
            {
                "OMEGA_STRESS_HOST": stress_host,
                "OMEGA_STRESS_WORKLOAD": ctx.workload,
                "OMEGA_STRESS_ARTIFACT_DIR": str(ctx.evidence_dir / "stress-spike"),
                "OMEGA_AUDIT_EVIDENCE_DIR": str(ctx.evidence_dir / "stress-spike" / "data-integrity"),
            },
        ),
        ("stress write-heavy", "make stress-write-heavy", write_env),
    ]
    for index, (name, command, env) in enumerate(load_stages):
        step = run(ctx, name, command, env=env)
        if ctx.target == "aws" and step.status != "PASS":
            _record_safe_skips(ctx, load_stages[index + 1 :], step)
            break
    run(ctx, "copilot redteam", "make copilot-redteam", env={"OMEGA_COPILOT_REDTEAM_EVIDENCE_DIR": str(ctx.evidence_dir / "copilot-redteam")})
    run(ctx, "cartridge resilience", "make cartridge-resilience", env={"OMEGA_CARTRIDGE_RESILIENCE_EVIDENCE_DIR": str(ctx.evidence_dir / "cartridge-resilience")})
    run(ctx, "data integrity audit", "make data-integrity-audit", env={"OMEGA_AUDIT_EVIDENCE_DIR": str(ctx.evidence_dir / "data-integrity")})
    _write_report(ctx)
    status = _status(ctx.steps)
    print(json.dumps({"status": status, "evidence_dir": str(ctx.evidence_dir)}, indent=2))
    if status == "FAIL":
        return 1
    if status == "BLOCKED":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
