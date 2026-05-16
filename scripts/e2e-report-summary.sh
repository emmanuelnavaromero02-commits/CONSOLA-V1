#!/usr/bin/env bash
# v1.44.3.2.1 — Parse the JSON Playwright report into a categorised
# markdown digest. Designed to be appended to docs/E2E_FINDINGS.md
# so the v1.44.3.3 fix sprint has a single source of truth.
#
# Reads:  tests-e2e/playwright-report/results.json (written by the
#         json reporter declared in playwright.config.ts).
# Writes: stdout — pipe to a file or paste into the findings doc.
#
# Severity buckets are heuristic, derived from the spec filename
# the test lives in:
#   🔴 Críticos  — auth gate (07-apis-deep), MCP cartridges (10-mcp),
#                  studio user-reported bugs (05-studio)
#   🟡 Altos     — dashboard / cartridges Next.js (02, 03)
#   🟢 Medios    — legacy HTML pages (06), API contracts (07)
#   🔵 Bajos     — UX / mobile / a11y / perf (09)
#                  copilot deferred (04, 11)
#
# Operators are encouraged to rewrite the severity ranking once
# the first run lands real data; the heuristic is just a default.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT="${REPO_ROOT}/tests-e2e/playwright-report/results.json"

if [ ! -f "${REPORT}" ]; then
    cat >&2 <<EOF
❌ Playwright JSON report not found at:
   ${REPORT}

Run the suite first:
   make e2e
EOF
    exit 1
fi

# Need either jq OR python3 to parse the JSON. Most operator boxes
# have one or the other; use python3 because we already require it
# for the Python test suite.
if ! command -v python3 > /dev/null 2>&1; then
    echo "❌ python3 not on PATH — needed to parse the report" >&2
    exit 1
fi

python3 - "${REPORT}" <<'PY'
import json
import sys
from collections import defaultdict
from pathlib import Path

report_path = Path(sys.argv[1])
data = json.loads(report_path.read_text())

# Walk the Playwright JSON shape. Each suite has specs; each spec
# has tests; each test has results. A test is "failed" if ANY of
# its results.status != "passed" AND != "skipped".
def iter_tests(suite):
    for s in suite.get("suites", []):
        yield from iter_tests(s)
    for spec in suite.get("specs", []):
        for t in spec.get("tests", []):
            yield {
                "title":     spec.get("title", "<no title>"),
                "file":      spec.get("file", "<no file>"),
                "results":   t.get("results", []),
                "expected":  t.get("expectedStatus"),
            }

# Severity heuristic by file name.
def severity(file: str) -> tuple[str, str]:
    if "05-studio" in file or "07-apis-deep" in file or "10-mcp" in file:
        return ("🔴", "Críticos")
    if "02-dashboard" in file or "03-cartridges" in file or "01-login" in file:
        return ("🟡", "Altos")
    if "06-html-pages" in file or "07-api-endpoints" in file:
        return ("🟢", "Medios")
    return ("🔵", "Bajos")

buckets: dict[str, list[dict]] = defaultdict(list)
totals = {"passed": 0, "failed": 0, "skipped": 0, "expected_fail": 0}

for suite in data.get("suites", []):
    for t in iter_tests(suite):
        # Most-recent result wins (Playwright records retries).
        last = t["results"][-1] if t["results"] else {"status": "unknown"}
        status = last.get("status", "unknown")
        if status == "passed":
            totals["passed"] += 1
        elif status == "skipped":
            totals["skipped"] += 1
        elif status == "failed" and t.get("expected") == "failed":
            totals["expected_fail"] += 1
        elif status == "failed":
            totals["failed"] += 1
            icon, bucket = severity(t["file"])
            buckets[bucket].append({
                "icon":  icon,
                "title": t["title"],
                "file":  t["file"],
                "error": (last.get("error", {}) or {}).get("message", ""),
            })

print("# E2E Run Summary\n")
print(f"- ✅ Passed:        {totals['passed']}")
print(f"- ❌ Failed:        {totals['failed']}")
print(f"- ⏭️  Skipped:       {totals['skipped']}")
print(f"- 🪦 Expected fail: {totals['expected_fail']}")
print()

if not buckets:
    print("**🎉 No failing tests.**")
    sys.exit(0)

for bucket in ("Críticos", "Altos", "Medios", "Bajos"):
    if bucket not in buckets:
        continue
    print(f"\n## {bucket} ({len(buckets[bucket])} fallos)\n")
    print("| Test | File | Error snippet |")
    print("|---|---|---|")
    for item in buckets[bucket]:
        title = item["title"].replace("|", "\\|")[:80]
        file = Path(item["file"]).name
        err = (item["error"].splitlines()[0] if item["error"] else "")[:120].replace("|", "\\|")
        print(f"| {title} | {file} | {err} |")
PY
