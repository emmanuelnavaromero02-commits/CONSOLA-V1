#!/usr/bin/env python3
"""Strict private-beta gate for a running local OMEGA stack.

This is intentionally stricter than ``make smoke``. The classic smoke target
proves the platform is alive and locked down; beta-smoke proves the product is
honest enough for a private beta: release identity is aligned, strict data
readiness passes, Gold has scoped rows with native RLS, lineage exists, Superset
is up, and Intelligence has generated at least one persisted signal.

The script is read-only. It does not seed, migrate, start, stop, or repair the
stack. Failures are meant to be actionable blockers, not papered-over demos.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_ROOT = REPO / "docs" / "release-evidence" / "beta-smoke"

PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"


@dataclass
class Check:
    name: str
    status: str
    evidence: str
    unblock: str = ""


def _run(cmd: list[str], *, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )


def _short(text: str, limit: int = 800) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _http(
    url: str, *, expect_json: bool = False, timeout: int = 8
) -> tuple[int, str, Any | None]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            payload = json.loads(body) if expect_json and body else None
            return int(response.status), body, payload
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        payload = None
        if expect_json and body:
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
        return int(exc.code), body, payload
    except Exception as exc:  # noqa: BLE001 - evidence, not app logic.
        return 0, type(exc).__name__, None


class BetaSmoke:
    def __init__(self, evidence_dir: Path, require_exact_tag: bool) -> None:
        self.evidence_dir = evidence_dir
        self.require_exact_tag = require_exact_tag
        self.checks: list[Check] = []

    def add(self, name: str, status: str, evidence: str, unblock: str = "") -> None:
        self.checks.append(
            Check(name=name, status=status, evidence=_short(evidence), unblock=unblock)
        )
        print(f"[beta-smoke {status}] {name}: {self.checks[-1].evidence}")

    def check_version(self) -> None:
        version_path = REPO / "VERSION"
        version = (
            version_path.read_text(encoding="utf-8").strip()
            if version_path.exists()
            else ""
        )
        if version.endswith("-beta") and version != "1.0.0":
            self.add("VERSION remains beta", PASS, f"VERSION={version}")
        else:
            self.add(
                "VERSION remains beta",
                FAIL,
                f"VERSION={version or '<missing>'}",
                "Keep VERSION beta until v1.0 gates are green.",
            )

        # Release identity uses `git describe --tags --exact-match HEAD`
        # when the operator wants a tagged release-candidate gate.
        result = _run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"], timeout=10
        )
        if result.returncode == 0:
            tag = result.stdout.strip()
            normalized = tag[1:] if tag.startswith("v") else tag
            status = PASS if normalized == version else FAIL
            self.add(
                "git tag aligns with VERSION",
                status,
                f"tag={tag} VERSION={version}",
                "Align VERSION with the release tag or retag the release candidate.",
            )
        elif self.require_exact_tag:
            self.add(
                "git tag aligns with VERSION",
                BLOCKED,
                _short(result.stdout or "HEAD is not exactly tagged"),
                "Run beta-smoke from the tagged release candidate, or unset OMEGA_BETA_SMOKE_REQUIRE_TAG.",
            )
        else:
            self.add(
                "git tag aligns with VERSION",
                PASS,
                "HEAD is not exactly tagged; local dev mode allows this.",
            )

        dirty_entries = self._release_dirty_entries()
        allow_dirty = os.environ.get(
            "OMEGA_BETA_SMOKE_ALLOW_DIRTY", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not dirty_entries:
            self.add(
                "release tree is clean",
                PASS,
                "no tracked or release-relevant untracked changes",
            )
        elif allow_dirty:
            self.add(
                "release tree is clean",
                PASS,
                f"dirty tree allowed for local dev; entries={len(dirty_entries)} sample={dirty_entries[:5]}",
                "Commit/stash release-relevant changes before tagging an actual beta candidate.",
            )
        else:
            self.add(
                "release tree is clean",
                FAIL,
                f"entries={len(dirty_entries)} sample={dirty_entries[:8]}",
                "Commit or stash release-relevant changes, or set OMEGA_BETA_SMOKE_ALLOW_DIRTY=1 for local-only validation.",
            )

        status, body, payload = _http("http://127.0.0.1:8000/healthz", expect_json=True)
        health_version = payload.get("version") if isinstance(payload, dict) else None
        if status == 200 and health_version == version:
            self.add(
                "console /healthz version aligns",
                PASS,
                f"/healthz version={health_version}",
            )
        else:
            self.add(
                "console /healthz version aligns",
                FAIL if status else BLOCKED,
                f"status={status} health_version={health_version} body={_short(body)} VERSION={version}",
                "Rebuild/restart console after updating VERSION, and keep healthz on app.version.app_version().",
            )

    def _release_dirty_entries(self) -> list[str]:
        result = _run(["git", "status", "--porcelain=v1"], timeout=10)
        if result.returncode != 0:
            return ["git status failed"]
        entries: list[str] = []
        for line in result.stdout.splitlines():
            path = line[3:] if len(line) > 3 else line
            if path.startswith("docs/release-evidence/"):
                continue
            entries.append(line)
        return entries

    def check_compose_config(self) -> None:
        cmd = ["docker", "compose"]
        env_file = REPO / "infra" / ".env"
        if env_file.exists():
            cmd.extend(["--env-file", str(env_file)])
        cmd.extend(
            [
                "-f",
                "infra/docker-compose.yml",
                "-f",
                "infra/docker-compose.dev.yml",
                "--profile",
                "sap",
                "config",
                "--quiet",
            ]
        )
        result = _run(cmd, timeout=60)
        self.add(
            "docker compose full SAP config validates",
            PASS if result.returncode == 0 else BLOCKED,
            result.stdout or "compose config returned 0",
            "Fix compose/env interpolation before claiming beta startup is reproducible.",
        )

    def check_http_surfaces(self) -> None:
        endpoints = {
            "console healthz": "http://127.0.0.1:8000/healthz",
            "console readyz": "http://127.0.0.1:8000/readyz",
            "workspace healthz": "http://127.0.0.1:8001/healthz",
            "mcp-infra healthz": "http://127.0.0.1:8010/healthz",
            "vault healthz": "http://127.0.0.1:8300/healthz",
            "refinement healthz": "http://127.0.0.1:8500/healthz",
            "replicon healthz": "http://127.0.0.1:8201/healthz",
            "hubspot healthz": "http://127.0.0.1:8210/healthz",
            "salesforce healthz": "http://127.0.0.1:8205/healthz",
            "sap-hcm healthz": "http://127.0.0.1:8202/healthz",
            "sap-successfactors healthz": "http://127.0.0.1:8203/healthz",
            "sap-s4hana healthz": "http://127.0.0.1:8204/healthz",
            "sap-b1 healthz": "http://127.0.0.1:8206/healthz",
            "airflow health": "http://127.0.0.1:8082/health",
            "superset health": "http://127.0.0.1:8088/health",
        }
        for name, url in endpoints.items():
            status, body, _payload = _http(url, expect_json=False)
            self.add(
                name,
                PASS if 200 <= status < 300 else BLOCKED,
                f"status={status} body={_short(body, 160)}",
                "Start the full SAP profile stack and wait for healthchecks.",
            )

        status, body, payload = _http(
            "http://127.0.0.1:8000/readyz?require_data=1&require_intelligence=1",
            expect_json=True,
        )
        ok = isinstance(payload, dict) and payload.get("ok") is True
        self.add(
            "strict data readiness",
            PASS if status == 200 and ok else FAIL,
            f"status={status} payload={payload if payload is not None else _short(body)}",
            "Run OMEGA_BETA_SMOKE_WARM_ACCEPTANCE=1 make beta-smoke, or manually materialize Gold and run Intelligence.",
        )

    def _psql(
        self, container: str, database: str, sql: str, *, port: str | None = None
    ) -> tuple[bool, str]:
        cmd = ["docker", "exec", container, "psql", "-U", "postgres"]
        if port:
            cmd.extend(["-p", port])
        cmd.extend(["-d", database, "-tAc", sql])
        result = _run(cmd, timeout=30)
        return result.returncode == 0, result.stdout.strip()

    def check_operational_db(self) -> None:
        ok, out = self._psql(
            "mode_postgres",
            "modecissions",
            "SELECT COUNT(*) FROM pg_tables WHERE schemaname='public';",
        )
        table_count = int(out) if ok and out.isdigit() else 0
        self.add(
            "operational DB migrations",
            PASS if table_count > 10 else BLOCKED,
            f"public_tables={table_count} raw={out}",
            "Run migrations/bootstrap before beta-smoke.",
        )

        ok, out = self._psql(
            "mode_postgres_gold",
            "modecissions_gold",
            """
            SELECT COALESCE(COUNT(*),0)::text || '|' || COALESCE(SUM(row_count),0)::text
              FROM omega_publication.published_lineage
             WHERE layer='gold' AND row_count > 0;
            """,
            port="5433",
        )
        parts = out.split("|") if ok else []
        lineage_count = int(parts[0]) if len(parts) == 2 and parts[0].isdigit() else 0
        lineage_rows = int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else 0
        self.add(
            "Gold lineage exists",
            PASS if lineage_count > 0 and lineage_rows > 0 else FAIL,
            f"gold_lineage_entries={lineage_count} lineage_rows={lineage_rows} raw={out}",
            "Run make acceptance or another staged Gold publication with authoritative lineage.",
        )

        ok, out = self._psql(
            "mode_postgres",
            "modecissions",
            "SELECT COUNT(*) FROM control_room_items WHERE COALESCE(item_kind,'') <> 'source_state';",
        )
        items = int(out) if ok and out.isdigit() else 0
        self.add(
            "Control Room has operational items",
            PASS if items > 0 else FAIL,
            f"control_room_items={items} raw={out}",
            "Run Intelligence after Gold is ready so control_room_items are published from persisted signals.",
        )

        ok, out = self._psql(
            "mode_postgres",
            "modecissions",
            "SELECT COUNT(*) FROM intelligence_signals;",
        )
        signals = int(out) if ok and out.isdigit() else 0
        self.add(
            "Intelligence has persisted signals",
            PASS if signals > 0 else FAIL,
            f"intelligence_signals={signals} raw={out}",
            "Run /api/intelligence/run against ready Gold datasets and persist at least one signal.",
        )

    def check_gold_db(self) -> None:
        ok, out = self._psql(
            "mode_postgres_gold",
            "modecissions_gold",
            "SELECT COALESCE((SELECT rolbypassrls::text FROM pg_roles WHERE rolname='omega_refinement_gold'), 'missing');",
            port="5433",
        )
        self.add(
            "Gold role is NOBYPASSRLS",
            PASS if ok and out == "false" else FAIL,
            f"rolbypassrls={out}",
            "Apply infra/init_gold/35_gold_native_rls.sql and recreate/fix the omega_refinement_gold role.",
        )

        ok, out = self._psql(
            "mode_postgres_gold",
            "modecissions_gold",
            """
            SELECT relname
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public'
               AND c.relkind='r'
               AND c.relname LIKE 'gold\\_%' ESCAPE '\\'
             ORDER BY relname;
            """,
            port="5433",
        )
        tables = (
            [
                line.strip()
                for line in out.splitlines()
                if re.fullmatch(r"gold_[A-Za-z0-9_]+", line.strip())
            ]
            if ok
            else []
        )
        self.add(
            "Postgres Gold has materialized tables",
            PASS if tables else FAIL,
            f"gold_tables={tables[:20]} count={len(tables)}",
            "Materialize at least the private-beta golden path into Postgres Gold.",
        )

        total_rows = 0
        for table in tables:
            ok_count, out_count = self._psql(
                "mode_postgres_gold",
                "modecissions_gold",
                f'SELECT COUNT(*) FROM public."{table}";',
                port="5433",
            )
            if ok_count and out_count.isdigit():
                total_rows += int(out_count)
        self.add(
            "Postgres Gold has rows",
            PASS if total_rows > 0 else FAIL,
            f"gold_rows={total_rows}",
            "Materialized Gold tables must contain scoped rows, not just empty schemas.",
        )

        ok, out = self._psql(
            "mode_postgres_gold",
            "modecissions_gold",
            """
            SELECT COUNT(*)
              FROM pg_class c
              JOIN pg_namespace n ON n.oid=c.relnamespace
             WHERE n.nspname='public'
               AND c.relkind='r'
               AND c.relname LIKE 'gold\\_%' ESCAPE '\\'
               AND (NOT c.relrowsecurity OR NOT c.relforcerowsecurity);
            """,
            port="5433",
        )
        weak = int(out) if ok and out.isdigit() else -1
        self.add(
            "Postgres Gold tables have native FORCE RLS",
            PASS if weak == 0 and bool(tables) else FAIL,
            f"weak_gold_tables={weak} total_gold_tables={len(tables)}",
            "Call omega_apply_gold_rls_for_table for every public.gold_* table and verify FORCE RLS.",
        )

    def check_external_writeback_blocked(self) -> None:
        result = _run(
            [
                "docker",
                "exec",
                "mode_console",
                "printenv",
                "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK",
            ],
            timeout=10,
        )
        value = result.stdout.strip().lower() if result.returncode == 0 else ""
        allowed = os.environ.get(
            "OMEGA_BETA_SMOKE_ALLOW_EXTERNAL_WRITEBACK", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        blocked = value not in {"1", "true", "yes", "on"}
        self.add(
            "external write-back disabled by default",
            PASS if blocked or allowed else FAIL,
            f"CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK={value or '<unset>'}",
            "Keep external write-back disabled for beta unless this run is an explicitly approved live adapter test.",
        )

    def write_evidence(self) -> None:
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "status": self.status,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": [asdict(check) for check in self.checks],
            "pass": sum(1 for check in self.checks if check.status == PASS),
            "fail": sum(1 for check in self.checks if check.status == FAIL),
            "blocked": sum(1 for check in self.checks if check.status == BLOCKED),
        }
        (self.evidence_dir / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        lines = [
            "# Beta Smoke Evidence",
            "",
            f"- status: `{summary['status']}`",
            f"- generated_at: `{summary['generated_at']}`",
            "",
            "| Check | Status | Evidence | Unblock |",
            "|---|---|---|---|",
        ]
        for check in self.checks:
            evidence = check.evidence.replace("|", "\\|")
            unblock = check.unblock.replace("|", "\\|")
            lines.append(f"| {check.name} | {check.status} | {evidence} | {unblock} |")
        (self.evidence_dir / "REPORT.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    @property
    def status(self) -> str:
        if any(check.status == FAIL for check in self.checks):
            return FAIL
        if any(check.status == BLOCKED for check in self.checks):
            return BLOCKED
        return PASS

    def run(self) -> int:
        self.check_version()
        self.check_compose_config()
        self.check_http_surfaces()
        self.check_operational_db()
        self.check_gold_db()
        self.check_external_writeback_blocked()
        self.write_evidence()
        print(
            json.dumps(
                {"status": self.status, "evidence_dir": str(self.evidence_dir)},
                indent=2,
            )
        )
        if self.status == FAIL:
            return 1
        if self.status == BLOCKED:
            return 2
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run strict private-beta smoke checks against a running local OMEGA stack."
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=DEFAULT_EVIDENCE_ROOT
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    )
    parser.add_argument(
        "--require-exact-tag",
        action="store_true",
        default=os.environ.get("OMEGA_BETA_SMOKE_REQUIRE_TAG", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Require HEAD to be exactly tagged and aligned with VERSION.",
    )
    args = parser.parse_args(argv)
    return BetaSmoke(args.evidence_dir, args.require_exact_tag).run()


if __name__ == "__main__":
    raise SystemExit(main())
