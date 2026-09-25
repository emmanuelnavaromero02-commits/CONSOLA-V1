#!/usr/bin/env bash
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

PYTHON="${PYTHON:-$(if [ -x .venv/bin/python ]; then echo .venv/bin/python; else echo python3; fi)}"
RUFF="${RUFF:-$(if [ -x .venv/bin/ruff ]; then echo .venv/bin/ruff; else echo ruff; fi)}"
PYTEST="${PYTEST:-$(if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)}"

PASS=0
FAIL=0

ok()   { PASS=$((PASS + 1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL + 1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; [ $# -gt 1 ] && printf '        %s\n' "$2"; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

step "Version identity"

VERSION="$(cat VERSION 2>/dev/null | tr -d '[:space:]')"
if [ -n "$VERSION" ]; then
  ok "VERSION file present ($VERSION)"
else
  bad "VERSION file present" "VERSION is missing or empty"
fi

if printf '%s' "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+-beta$'; then
  ok "VERSION is a well-formed beta"
else
  bad "VERSION is a well-formed beta" "got '$VERSION'; v1.0 gates are still BLOCKED"
fi

RESOLVED="$(PYTHONPATH=.:console "$PYTHON" -c \
  'from app.version import app_version; print(app_version())' 2>/dev/null | tr -d '[:space:]')"
if [ "$RESOLVED" = "$VERSION" ]; then
  ok "app.version.app_version() resolves to VERSION"
else
  bad "app.version.app_version() resolves to VERSION" "resolver said '$RESOLVED', file says '$VERSION'"
fi

step "Canonical commands exist"

for target in preflight up up-core down smoke beta-smoke baseline-smoke test e2e acceptance verify-release security-scan migrate nuke; do
  if grep -qE "^${target}:" Makefile; then
    ok "make $target"
  else
    bad "make $target" "documented target is absent from the Makefile"
  fi
done

step "Config parses"

if "$PYTHON" - <<'PY'
import glob, os, pathlib, sys
try:
    import yaml
except ImportError:
    print("PyYAML unavailable", file=sys.stderr)
    sys.exit(2)

# Explicit, not globbed. A glob over .github/workflows reports PASS when a
# required workflow is DELETED — there is simply nothing left to parse, and
# "0 files, 0 errors" reads as success. That is a false green on the exact
# event the check exists to catch, so the four gate families are named here
# and their absence is a failure. Keep this list in step with the gates:
# tests/test_v1_release_checklist.py enforces that it stays explicit.
REQUIRED_WORKFLOWS = {
    ".github/workflows/lint.yml": "Lint",
    ".github/workflows/security.yml": "Security Scan",
    ".github/workflows/mcp-infra-pdf-security.yml": "MCP Infra PDF Security",
    ".github/workflows/control-room-postgres-rls.yml": "Control Room PostgreSQL RLS",
}

missing = []
for path in sorted(REQUIRED_WORKFLOWS):
    p = pathlib.Path(path)
    if not p.is_file():
        missing.append(f"{path}: required workflow is absent or not a regular file")
    elif not os.access(path, os.R_OK):
        missing.append(f"{path}: required workflow is not readable")
    elif p.stat().st_size == 0:
        missing.append(f"{path}: required workflow is empty")
if missing:
    print("\n".join(missing), file=sys.stderr)
    sys.exit(1)

targets = sorted(
    set(REQUIRED_WORKFLOWS)
    | set(glob.glob(".github/workflows/*.yml"))
    | set(glob.glob("infra/docker-compose*.yml"))
)
# An empty collection must never read as success.
if not targets:
    print("no workflow or compose files discovered", file=sys.stderr)
    sys.exit(1)

bad = []
for path in targets:
    try:
        doc = yaml.safe_load(open(path, encoding="utf-8"))
    except Exception as exc:
        bad.append(f"{path}: {exc}")
        continue
    expected = REQUIRED_WORKFLOWS.get(path)
    if expected is None:
        continue
    if not isinstance(doc, dict):
        bad.append(f"{path}: required workflow did not parse to a mapping")
    elif doc.get("name") != expected:
        bad.append(f"{path}: expected workflow name {expected!r}, got {doc.get('name')!r}")
if bad:
    print("\n".join(bad), file=sys.stderr)
    sys.exit(1)
PY
then
  ok "required workflows present; workflows and compose are valid YAML"
else
  bad "required workflows present; workflows and compose are valid YAML" "see stderr above"
fi

SH_BAD=""
while IFS= read -r script; do
  bash -n "$script" 2>/dev/null || SH_BAD="$SH_BAD $script"
done < <(find scripts infra -name '*.sh' -not -path '*/node_modules/*' 2>/dev/null | sort)
if [ -z "$SH_BAD" ]; then
  ok "shell scripts parse (bash -n)"
else
  bad "shell scripts parse (bash -n)" "failed:$SH_BAD"
fi

if "$PYTHON" -m compileall -q console/app refinement/app vault/app workspace/app mcp-infra/app airflow/dags scripts >/dev/null 2>&1; then
  ok "python sources compile"
else
  bad "python sources compile" "compileall reported syntax errors"
fi

step "Static analysis"

if "$RUFF" check . >/dev/null 2>&1; then
  ok "ruff check"
else
  bad "ruff check" "run '$RUFF check .' for detail"
fi

step "Baseline contract tests"

if "$PYTEST" -q tests/test_v1_release_checklist.py >/dev/null 2>&1; then
  ok "release checklist contract"
else
  bad "release checklist contract" "run '$PYTEST tests/test_v1_release_checklist.py' for detail"
fi

if PYTHONPATH=.:console "$PYTEST" -q console/tests/test_ops_summary_and_version.py >/dev/null 2>&1; then
  ok "version surfaces contract"
else
  bad "version surfaces contract" "run 'PYTHONPATH=.:console $PYTEST console/tests/test_ops_summary_and_version.py' for detail"
fi

printf '\n'
if [ "$FAIL" -eq 0 ]; then
  printf '\033[32m%d/%d baseline checks passed\033[0m — checkout is coherent.\n' "$PASS" "$((PASS + FAIL))"
  printf 'This is not a product gate. Run `make test` and `make beta-smoke` before release.\n'
  exit 0
fi
printf '\033[31m%d/%d baseline checks passed, %d failed\033[0m — the baseline has drifted.\n' \
  "$PASS" "$((PASS + FAIL))" "$FAIL"
exit 1
