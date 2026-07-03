#!/usr/bin/env python3
"""Emit coarse CI impact flags from the files changed in a GitHub run.

The goal is to keep pull-request CI focused: run regression checks for the
areas touched by a diff, and reserve full-stack gates for changes that can
actually affect the full runtime surface.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


SERVICES = {
    "console": "./console",
    "workspace": "./workspace",
    "refinement": "./refinement",
    "vault": "./vault",
    "mcp-infra": "./mcp-infra",
    "airflow": "./infra/airflow",
    "replicon": "./cartridges/replicon",
    "hubspot": "./cartridges/hubspot",
    "sap_hcm": "./cartridges/sap_hcm",
    "sap_s4hana": "./cartridges/sap_s4hana",
    "sap_successfactors": "./cartridges/sap_successfactors",
    "salesforce": "./cartridges/salesforce",
}

CARTRIDGE_ROOT_TEST_PREFIXES = {
    "hubspot": "hubspot",
    "replicon": "replicon",
    "salesforce": "salesforce",
    "sap_hcm": "sap_hcm",
    "sap_s4hana": "sap_s4hana",
    "sap_successfactors": "sap_successfactors",
}

PY_RUNTIME_ROOTS = (
    "console/app/",
    "workspace/app/",
    "vault/app/",
    "refinement/app/",
    "mcp-infra/app/",
    "cartridges/",
    "airflow/",
    "infra/airflow/",
)

CONSOLE_SERVICE_RELEASE_EXCLUDE = (
    r"^console/app/services/db_pool\.py$",
    r"^console/app/services/operations_service\.py$",
    r"^console/app/services/mcp_payloads\.py$",
    r"^console/app/services/request_rate_limits\.py$",
    r"^console/app/services/readyz_dependencies\.py$",
    r"^console/app/services/runtime_calls\.py$",
    r"^console/app/services/security_headers\.py$",
    r"^console/app/services/service_urls\.py$",
    r"^console/app/services/status_pages\.py$",
    r"^console/app/services/startup_readiness\.py$",
    r"^console/app/services/vault_utils\.py$",
)


def _run(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def _zero_sha(value: str) -> bool:
    return bool(value) and set(value) == {"0"}


def _default_base_head() -> tuple[str, str]:
    head = os.environ.get("GITHUB_SHA", "HEAD")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_name == "pull_request" and event_path:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        return event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"]
    if event_name == "push":
        before = os.environ.get("GITHUB_EVENT_BEFORE") or ""
        if not before and event_path:
            event = json.loads(Path(event_path).read_text(encoding="utf-8"))
            before = event.get("before", "")
        if before and not _zero_sha(before):
            return before, head
    try:
        return _run(["git", "rev-parse", f"{head}^"]), head
    except subprocess.CalledProcessError:
        return head, head


def _changed_files(base: str, head: str) -> list[str]:
    if base == head:
        return []
    output = _run(["git", "diff", "--name-only", f"{base}...{head}"])
    return [line.strip() for line in output.splitlines() if line.strip()]


def _any(files: list[str], *patterns: str) -> bool:
    return any(any(re.search(pattern, path) for pattern in patterns) for path in files)


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, path) for pattern in patterns)


def _all_true_for_schedule(flags: dict[str, bool]) -> None:
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        for key in flags:
            flags[key] = True


def _service_changed(files: list[str], service: str, context: str) -> bool:
    root = context.removeprefix("./") + "/"
    if service == "airflow":
        return _any(files, r"^infra/airflow/", r"^airflow/", r"^infra/docker-compose")
    if service.startswith("sap_") or service in {"replicon", "hubspot", "salesforce"}:
        # Cartridge datasets and docs are mounted from the repo at runtime; the
        # service image only needs rebuilds for app code, deps, DAGs, or Docker.
        return _any(files, rf"^{re.escape(root)}(app|dags)/", rf"^{re.escape(root)}(Dockerfile|requirements\.txt)$")
    if service == "console":
        return _any(files, r"^console/(app|Dockerfile|requirements\.txt)", r"^console-next/")
    return any(path.startswith(root) for path in files)


def _build_matrix(files: list[str]) -> str:
    include = [
        {"service": service, "context": context}
        for service, context in SERVICES.items()
        if _service_changed(files, service, context)
    ]
    return json.dumps({"include": include}, separators=(",", ":"))


def _has_build_matrix(matrix: str) -> bool:
    return bool(json.loads(matrix).get("include"))


def _changed_cartridges(files: list[str]) -> set[str]:
    cartridges: set[str] = set()
    for path in files:
        match = re.match(r"^cartridges/([^/]+)/", path)
        if match:
            cartridges.add(match.group(1))
    return cartridges


def _cartridge_files(files: list[str], cartridge: str) -> list[str]:
    root = f"cartridges/{cartridge}/"
    return [path for path in files if path.startswith(root)]


def _cartridge_dataset_only(files: list[str], cartridge: str) -> bool:
    paths = [
        path
        for path in _cartridge_files(files, cartridge)
        if not path.startswith(f"cartridges/{cartridge}/tests/")
    ]
    return bool(paths) and all(re.match(rf"^cartridges/{re.escape(cartridge)}/datasets/.*\.sql$", path) for path in paths)


def _cartridge_needs_root_contracts(files: list[str], cartridge: str) -> bool:
    paths = [
        path
        for path in _cartridge_files(files, cartridge)
        if not path.startswith(f"cartridges/{cartridge}/tests/")
    ]
    return bool(paths) and not _cartridge_dataset_only(files, cartridge)


def _space_join(paths: set[str]) -> str:
    return " ".join(sorted(paths))


def _root_test_targets(files: list[str]) -> str:
    targets = {
        path
        for path in files
        if re.match(r"^tests/test.*\.py$", path) and Path(path).exists()
    }
    for cartridge in _changed_cartridges(files):
        prefix = CARTRIDGE_ROOT_TEST_PREFIXES.get(cartridge)
        if not prefix:
            continue
        if _cartridge_dataset_only(files, cartridge):
            dataset_contract = Path("tests") / f"test_{prefix}_datasets.py"
            if dataset_contract.exists():
                targets.add(str(dataset_contract))
        elif _cartridge_needs_root_contracts(files, cartridge):
            targets.update(str(path) for path in Path("tests").glob(f"test_{prefix}*.py"))
    if not targets and _any(files, r"^tests/", r"^airflow/", r"^infra/airflow/"):
        targets.add("tests")
    return _space_join(targets)


def _cartridge_test_targets(files: list[str]) -> str:
    targets = set()
    for cartridge in _changed_cartridges(files):
        tests_dir = Path("cartridges") / cartridge / "tests"
        if _cartridge_dataset_only(files, cartridge):
            dag_contract = tests_dir / "test_airflow_dag_contract.py"
            if dag_contract.exists():
                targets.add(str(dag_contract))
        elif tests_dir.exists():
            targets.add(str(tests_dir))
    return _space_join(targets)


def _cartridge_requirement_paths(files: list[str]) -> str:
    paths = set()
    for cartridge in _changed_cartridges(files):
        req = Path("cartridges") / cartridge / "requirements.txt"
        if req.exists():
            paths.add(str(req))
    return _space_join(paths)


def _flags(files: list[str]) -> dict[str, bool | str]:
    py_file = _any(files, r"\.py$")
    py_runtime = any(
        path.endswith(".py")
        and path.startswith(root)
        and "/tests/" not in path
        and not path.endswith("_test.py")
        for path in files
        for root in PY_RUNTIME_ROOTS
    )
    frontend = _any(files, r"^console-next/", r"^console/app/static/console-next/")
    infra = _any(files, r"^infra/", r"^\.github/workflows/", r"^scripts/(wait_for_health|smoke|production|run-e2e|v1_stress)")
    cartridge = _any(files, r"^cartridges/")
    dataset = _any(files, r"^cartridges/[^/]+/datasets/.*\.sql$", r"^tests/test_.*datasets.*\.py$")
    deps_python = _any(files, r"(^|/)requirements\.txt$")
    deps_node = _any(files, r"(^|/)(package\.json|package-lock\.json|npm-shrinkwrap\.json)$")
    e2e = _any(
        files,
        r"^tests-e2e/(specs|fixtures)/",
        r"^tests-e2e/(global-setup|playwright\.config)\.ts$",
        r"^tests-e2e/\.env\.example$",
        r"^console-next/(src|app|components|lib|public)/",
        r"^console-next/(next\.config|tailwind\.config|postcss\.config|middleware).*\.(mjs|js|ts)$",
        r"^console/app/(main|routers|services|static)/",
        r"^infra/(docker-compose|bootstrap)",
        r"^scripts/(wait_for_health|run-e2e|smoke)",
        r"^Makefile$",
        r"^\.github/workflows/e2e\.yml$",
    )
    compose = _any(files, r"^infra/.*docker-compose.*\.ya?ml$", r"^infra/terraform/deploy/.*compose.*\.ya?ml$")
    build_matrix = _build_matrix(files)
    root_test_targets = _root_test_targets(files)
    cartridge_test_targets = _cartridge_test_targets(files)

    full_stack_files = [
        path
        for path in files
        if not _matches(path, CONSOLE_SERVICE_RELEASE_EXCLUDE)
    ]
    release_full_stack = compose or _any(
        full_stack_files,
        r"^console/Dockerfile$",
        r"^console/app/(dependencies\.py|security\.py|config/|middleware/|services/)",
        r"^refinement/(app|Dockerfile)",
        r"^vault/(app|Dockerfile)",
        r"^workspace/(app|Dockerfile)",
        r"^mcp-infra/(app|Dockerfile)",
        r"^cartridges/[^/]+/(app|dags|Dockerfile)",
        r"^infra/airflow/",
        r"^scripts/(production|v1_stress|acceptance|smoke|run-e2e)",
        r"^\.github/workflows/(release|deploy-aws|docker-image|e2e)\.yml$",
    )

    flags: dict[str, bool | str] = {
        "python": py_file,
        "python_runtime": py_runtime,
        "frontend": frontend,
        "infra": infra,
        "cartridge": cartridge,
        "dataset": dataset,
        "python_deps": deps_python,
        "node_deps": deps_node,
        "e2e": e2e,
        "compose": compose,
        "console_tests": _any(files, r"^console/(app|tests)/"),
        "refinement_tests": _any(files, r"^refinement/(app|tests)/"),
        "vault_tests": _any(files, r"^vault/(app|tests)/"),
        "workspace_tests": _any(files, r"^workspace/(app|tests)/"),
        "root_tests": bool(root_test_targets),
        "cartridge_tests": bool(cartridge_test_targets),
        "root_test_targets": root_test_targets,
        "cartridge_test_targets": cartridge_test_targets,
        "cartridge_requirement_paths": _cartridge_requirement_paths(files),
        "build_matrix": build_matrix,
        "has_build_matrix": _has_build_matrix(build_matrix),
        "release_full_stack": release_full_stack,
    }
    bool_flags = {k: v for k, v in flags.items() if isinstance(v, bool)}
    _all_true_for_schedule(bool_flags)
    flags.update(bool_flags)
    return flags


def _write_outputs(flags: dict[str, bool | str], files: list[str]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"changed_files={json.dumps(files, separators=(',', ':'))}"]
    for key, value in flags.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = value
        lines.append(f"{key}={rendered}")
    text = "\n".join(lines) + "\n"
    if output_path:
        with open(output_path, "a", encoding="utf-8") as fh:
            fh.write(text)
    else:
        print(text, end="")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--files", nargs="*")
    args = parser.parse_args()

    if args.files is not None and len(args.files) > 0:
        files = args.files
    else:
        base, head = (args.base, args.head) if args.base and args.head else _default_base_head()
        files = _changed_files(base, head)
    _write_outputs(_flags(files), files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
