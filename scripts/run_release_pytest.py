#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]


def _load_harness_verifier():
    path = REPO / "scripts" / "verify_release_test_harness.py"
    spec = importlib.util.spec_from_file_location("_omega_harness_verifier", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("release harness verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HARNESS = _load_harness_verifier()
HarnessSealError = _HARNESS.HarnessSealError
verify = _HARNESS.verify
try:
    verify()
except HarnessSealError as exc:
    print(f"RELEASE PYTEST BLOCKED: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.verify_test_skip_policy import DEFAULT_POLICY, PYTEST_ROOTS  # noqa: E402

FORBIDDEN_ARGUMENTS = {
    "-k",
    "-m",
    "-p",
    "-c",
    "--collect-only",
    "--co",
    "--pyargs",
    "--lf",
    "--last-failed",
    "--ff",
    "--failed-first",
    "--nf",
    "--new-first",
    "--sw",
    "--stepwise",
    "--stepwise-skip",
    "--continue-on-collection-errors",
    "--ignore",
    "--ignore-glob",
    "--deselect",
    "--confcutdir",
    "--rootdir",
    "--override-ini",
    "--config-file",
    "--disable-plugin-autoload",
}
FORBIDDEN_PREFIXES = (
    "-p",
    "-c",
    "-o",
    "--ignore=",
    "--ignore-glob=",
    "--deselect=",
    "--confcutdir=",
    "--rootdir=",
    "--override-ini=",
    "--disable-plugin-autoload",
    "--config-file",
)
REPORT_KEYS = {
    "schema_version",
    "kind",
    "nonce",
    "mode",
    "blocked",
    "errors",
    "selected",
    "deselected",
    "collection_skips",
    "reports",
}
REPORT_ENTRY_KEYS = {"nodeid", "phase", "outcome", "xfail"}
ALLOWED_OPTIONS = {"-q", "-v", "-vv", "-ra", "--import-mode=importlib"}
ALLOWED_PARENT_PYTHONPATHS = {"", ".", "console", ".:console", "refinement", "vault", "workspace"}
CHILD_BOOTSTRAP = """
import json
import sys
from pathlib import Path

import pytest
import anyio.pytest_plugin
import pytest_asyncio.plugin

roots = json.loads(sys.argv.pop(1))
if not isinstance(roots, list) or not roots:
    raise SystemExit("release pytest import roots are invalid")
for raw in reversed(roots):
    root = Path(raw).resolve(strict=True)
    sys.path.insert(0, str(root))
raise SystemExit(pytest.console_main())
"""
PYTHON_CONTROL_VARIABLES = {
    "PYTHONBREAKPOINT",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "PYTHONWARNINGS",
}


class ReleasePytestError(RuntimeError):
    pass


def _import_roots(args: list[str]) -> list[Path]:
    families: set[Path] = set()
    for argument in args:
        if argument.startswith("-"):
            continue
        relative = (REPO / argument.split("::", 1)[0]).resolve().relative_to(REPO)
        parts = relative.parts
        if not parts or parts[0] in {"tests", "airflow"}:
            continue
        if parts[0] == "cartridges" and len(parts) >= 2:
            families.add(REPO / "cartridges" / parts[1])
        elif parts[0] in {"console", "refinement", "vault", "workspace", "mcp-infra"}:
            families.add(REPO / parts[0])
    if len(families) > 1:
        joined = ", ".join(sorted(path.relative_to(REPO).as_posix() for path in families))
        raise ReleasePytestError(
            f"incompatible component import roots must run separately: {joined}"
        )
    return [REPO, *sorted(families)]


def _validate_arguments(args: list[str]) -> None:
    targets = 0
    allowed_roots = [(REPO / root).resolve() for root in PYTEST_ROOTS]
    for argument in args:
        option = argument.split("=", 1)[0]
        if option in FORBIDDEN_ARGUMENTS or argument.startswith(FORBIDDEN_PREFIXES):
            raise ReleasePytestError(f"selection/configuration argument is forbidden: {option}")
        if argument.startswith("-") and argument not in ALLOWED_OPTIONS:
            raise ReleasePytestError(
                f"unreviewed pytest option is forbidden: {argument}"
            )
        if not argument.startswith("-"):
            raw_path = argument.split("::", 1)[0]
            candidate = (REPO / raw_path).resolve()
            if not candidate.exists() or not any(
                candidate == root or root in candidate.parents for root in allowed_roots
            ):
                raise ReleasePytestError(
                    f"release test target is outside the sealed roots: {raw_path}"
                )
            targets += 1
    if targets == 0:
        raise ReleasePytestError("no sealed release test targets")


def _load_report(path: Path, *, nonce: str, mode: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleasePytestError(f"{mode} report missing or malformed") from exc
    if (
        not isinstance(value, dict)
        or set(value) != REPORT_KEYS
        or value.get("schema_version") != 1
        or value.get("kind") != "omega-release-pytest-report"
        or value.get("nonce") != nonce
        or value.get("mode") != mode
        or value.get("blocked") is not False
        or value.get("errors") != []
    ):
        raise ReleasePytestError(f"{mode} report identity or policy result is invalid")
    for key in ("selected", "deselected", "collection_skips"):
        items = value.get(key)
        if (
            not isinstance(items, list)
            or items != sorted(set(items))
            or not all(isinstance(item, str) and item for item in items)
        ):
            raise ReleasePytestError(f"{mode} report {key} inventory is invalid")
    reports = value.get("reports")
    if not isinstance(reports, list):
        raise ReleasePytestError(f"{mode} report outcome inventory is invalid")
    for item in reports:
        if (
            not isinstance(item, dict)
            or set(item) != REPORT_ENTRY_KEYS
            or not isinstance(item.get("nodeid"), str)
            or not item["nodeid"]
            or item.get("phase") not in {"setup", "call", "teardown"}
            or item.get("outcome") not in {"passed", "failed", "skipped"}
            or not isinstance(item.get("xfail"), bool)
        ):
            raise ReleasePytestError(f"{mode} report contains an invalid outcome")
    return value


def _run(
    *,
    args: list[str],
    config: Path,
    report: Path,
    mode: str,
    import_roots: list[Path],
) -> tuple[subprocess.CompletedProcess[bytes], dict[str, Any]]:
    nonce = secrets.token_hex(32)
    environment = dict(os.environ)
    for key in PYTHON_CONTROL_VARIABLES:
        environment.pop(key, None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONSAFEPATH"] = "1"
    environment.pop("PYTEST_ADDOPTS", None)
    environment.pop("PYTEST_PLUGINS", None)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["OMEGA_RELEASE_PYTEST_REPORT"] = str(report)
    environment["OMEGA_RELEASE_PYTEST_NONCE"] = nonce
    environment["OMEGA_RELEASE_PYTEST_MODE"] = mode
    environment["OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT"] = "release"
    environment["OMEGA_RELEASE_TEST_SKIP_POLICY"] = str(DEFAULT_POLICY)
    command = [
        sys.executable,
        "-I",
        "-c",
        CHILD_BOOTSTRAP,
        json.dumps([str(path) for path in import_roots]),
        "-c",
        str(config),
        "--rootdir",
        str(REPO),
        "-p",
        "scripts.verify_test_skip_policy",
        "-p",
        "pytest_asyncio.plugin",
        "-p",
        "anyio.pytest_plugin",
        *args,
    ]
    if mode == "collect":
        command.append("--collect-only")
    completed = subprocess.run(command, env=environment, check=False)
    value = _load_report(report, nonce=nonce, mode=mode)
    if completed.returncode != 0:
        raise ReleasePytestError(f"pytest {mode} returned {completed.returncode}")
    return completed, value


def _reconcile(collection: dict[str, Any], execution: dict[str, Any]) -> None:
    selected = collection["selected"]
    if not selected:
        raise ReleasePytestError("collection selected no release tests")
    if collection["deselected"] or execution["deselected"]:
        raise ReleasePytestError("deselected release tests are forbidden")
    if execution["selected"] != selected:
        raise ReleasePytestError("execution inventory differs from collection")
    if execution["collection_skips"] != collection["collection_skips"]:
        raise ReleasePytestError("collection skip inventory changed during execution")
    if collection["reports"]:
        raise ReleasePytestError("collection unexpectedly executed release tests")
    by_node: dict[str, list[dict[str, Any]]] = {nodeid: [] for nodeid in selected}
    for report in execution["reports"]:
        if report["outcome"] == "failed" or (
            report["xfail"] and report["outcome"] == "passed"
        ):
            raise ReleasePytestError("execution report contains a failed test or XPASS")
        nodeid = report["nodeid"]
        if nodeid not in by_node:
            raise ReleasePytestError("execution reported a non-collected test")
        by_node[nodeid].append(report)
    for nodeid, reports in by_node.items():
        phases = {report["phase"]: report for report in reports}
        if len(phases) != len(reports) or "setup" not in phases or "teardown" not in phases:
            raise ReleasePytestError(f"incomplete phase inventory: {nodeid}")
        if phases["setup"]["outcome"] == "passed" and "call" not in phases:
            raise ReleasePytestError(f"collected test did not execute: {nodeid}")
        if phases["setup"]["outcome"] != "skipped" and "call" not in phases:
            raise ReleasePytestError(f"collected test has no terminal outcome: {nodeid}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args:
            raise ReleasePytestError("no test targets")
        _validate_arguments(args)
        import_roots = _import_roots(args)
        configured_environment = os.environ.get("OMEGA_RELEASE_TEST_SKIP_ENVIRONMENT")
        if configured_environment not in {None, "release"}:
            raise ReleasePytestError("release skip environment override is forbidden")
        configured_policy = os.environ.get("OMEGA_RELEASE_TEST_SKIP_POLICY")
        if configured_policy is not None and Path(configured_policy).resolve() != DEFAULT_POLICY.resolve():
            raise ReleasePytestError("release skip policy override is forbidden")
        configured_pythonpath = os.environ.get("PYTHONPATH", "")
        if configured_pythonpath not in ALLOWED_PARENT_PYTHONPATHS:
            raise ReleasePytestError("unreviewed parent PYTHONPATH is forbidden")
        verify()
        with tempfile.TemporaryDirectory(prefix="omega-release-pytest-") as directory:
            root = Path(directory)
            config = root / "pytest.ini"
            config.write_text("[pytest]\n", encoding="utf-8")
            _, collection = _run(
                args=args,
                config=config,
                report=root / "collection.json",
                mode="collect",
                import_roots=import_roots,
            )
            verify()
            _, execution = _run(
                args=args,
                config=config,
                report=root / "execution.json",
                mode="execute",
                import_roots=import_roots,
            )
            verify()
            _reconcile(collection, execution)
            verify()
    except (HarnessSealError, ReleasePytestError) as exc:
        print(f"RELEASE PYTEST BLOCKED: {exc}", file=sys.stderr)
        return 1
    print(f"RELEASE PYTEST PASS: {len(execution['selected'])} tests reconciled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
