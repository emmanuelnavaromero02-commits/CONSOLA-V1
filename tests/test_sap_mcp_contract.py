"""Sprint v1.43.4 — Codex C1 BLOCKER: SAP cartridges' /mcp/tools
endpoint returned HTTP 500 because cartridges/sap_*/requirements.txt
pinned ``fastmcp==2.5.0`` while cartridges/sap_*/app/main.py uses
the fastmcp 3.x API (``mcp.list_tools()`` and ``mcp.get_tool()``,
neither of which exist on fastmcp 2.x's FastMCP class).

Replicon worked by accident: it pinned ``fastmcp`` with no version,
so pip picked the latest (3.x).

This sprint:
  * Bumps ``fastmcp`` to ``>=3.3.0,<4.0`` in all 4 cartridges.
  * Bumps ``pydantic`` to ``>=2.11.7,<3.0`` (fastmcp 3.x floor).
  * Bumps ``uvicorn[standard]`` to ``>=0.35.0,<1.0`` (fastmcp 3.x
    server extras floor).
  * Bumps ``requests`` to ``>=2.33.0,<3.0`` (closes the two CVEs
    v1.43.2 had to --ignore-vuln in the cartridge sprint).
  * Bumps ``python-dotenv`` to ``>=1.2.2,<2.0`` (closes the one
    CVE v1.43.2 had to --ignore-vuln).
  * Retires the 10 ``--ignore-vuln`` entries from
    .github/workflows/security.yml that the v1.43.2 sprint added
    as "cartridge sprint" technical debt.

Static verification only — the runtime HTTP contract is exercised
by tests/test_e2e_smoke.py against the live stack (which the dev
must run before merging this PR; the CI sandbox cannot boot
docker-compose).
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CARTRIDGES = (
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "replicon",
)


def _reqs(cart: str) -> str:
    return (REPO / "cartridges" / cart / "requirements.txt").read_text(
        encoding="utf-8"
    )


def _main(cart: str) -> str:
    return (REPO / "cartridges" / cart / "app/main.py").read_text(
        encoding="utf-8"
    )


def _security_workflow() -> str:
    return (REPO / ".github/workflows/security.yml").read_text(
        encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────
# requirements.txt — version pin contracts
# ─────────────────────────────────────────────────────────────


def test_all_cartridges_pin_fastmcp_to_3_x():
    """fastmcp 2.x's FastMCP class is missing ``list_tools()`` and
    ``get_tool()`` — the exact methods cartridge main.py calls.
    Lock every cartridge to the 3.x major so a future re-pin to
    2.x can't reopen the regression."""
    for cart in CARTRIDGES:
        src = _reqs(cart)
        assert re.search(
            r"^fastmcp>=3\.\d+\.\d+,<4\.0\s*$", src, re.MULTILINE
        ), f"{cart} must pin fastmcp to >=3.x,<4.0; got:\n{src}"
        assert "fastmcp==2." not in src, (
            f"{cart} still references fastmcp 2.x — would reintroduce "
            f"the Codex C1 AttributeError on /mcp/tools"
        )


def test_all_cartridges_pin_pydantic_compatible_with_fastmcp_3():
    """fastmcp 3.x requires pydantic >= 2.11.7. SAP cartridges were
    pinned at 2.9.2 (v1.43.1 era), which makes pip refuse to resolve.

    Replicon is the exception here: its pydantic was unpinned in
    v1.43.1, and fastmcp 3's dep solver pulls a compatible
    transitively. Don't force a re-pin there — only assert the SAP
    floor.
    """
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors"):
        src = _reqs(cart)
        assert re.search(
            r"^pydantic>=2\.11\.\d+,<3\.0\s*$", src, re.MULTILINE
        ), f"{cart} must pin pydantic to >=2.11.7,<3.0 (fastmcp 3.x floor)"


def test_all_cartridges_pin_uvicorn_compatible_with_fastmcp_3_server():
    """fastmcp 3.x's ``[server]`` extra requires uvicorn >= 0.35.
    The 0.30.x pin from v1.43.2 makes pip refuse to resolve."""
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors"):
        src = _reqs(cart)
        assert re.search(
            r"^uvicorn\[standard\]>=0\.3[5-9]\.\d+", src, re.MULTILINE
        ) or re.search(
            r"^uvicorn\[standard\]>=0\.[4-9]\d?\.\d+", src, re.MULTILINE
        ), f"{cart} must pin uvicorn[standard] to >=0.35 (fastmcp 3.x server)"


def test_sap_cartridges_no_longer_pin_vulnerable_requests():
    """v1.43.2 deferred ``requests==2.32.3`` → ``>=2.33.0`` as part
    of the "cartridge sprint" and used --ignore-vuln to suppress
    the two CVEs (GHSA-9hjg-9r4m-mvj7, GHSA-gc5v-m9x4-r6x2).
    The sprint is this hotfix; the pins move."""
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors"):
        src = _reqs(cart)
        assert "requests==2.32.3" not in src, (
            f"{cart} still pins vulnerable requests 2.32.3"
        )
        assert re.search(
            r"^requests>=2\.3[3-9]\.\d+", src, re.MULTILINE
        ) or re.search(
            r"^requests>=2\.[4-9]\d?\.\d+", src, re.MULTILINE
        ), f"{cart} must pin requests to >=2.33.0"


def test_sap_cartridges_no_longer_pin_vulnerable_python_dotenv():
    """Same story as requests: v1.43.2 --ignore-vuln'd
    GHSA-mf9w-mj56-hr94 against python-dotenv 1.1.0. 1.2.2 fixes it."""
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors"):
        src = _reqs(cart)
        assert "python-dotenv==1.1.0" not in src
        assert re.search(
            r"^python-dotenv>=1\.[2-9]\.\d+", src, re.MULTILINE
        ), f"{cart} must pin python-dotenv to >=1.2.2"


# ─────────────────────────────────────────────────────────────
# main.py — /mcp/tools and /mcp/invoke contract
# ─────────────────────────────────────────────────────────────


def test_all_cartridges_implement_mcp_tools_endpoint():
    """Every cartridge must expose ``GET /mcp/tools`` so the console
    registry can sync tool catalogs. This is the contract the v1.43.4
    fix makes work for SAP."""
    for cart in CARTRIDGES:
        src = _main(cart)
        assert "@app.get(\"/mcp/tools\"" in src, (
            f"{cart} missing GET /mcp/tools handler"
        )
        # The handler must call fastmcp 3.x's list_tools() API.
        assert "await mcp.list_tools()" in src, (
            f"{cart}/app/main.py must call ``await mcp.list_tools()`` — "
            f"the fastmcp 3.x API the pins now align with"
        )


def test_all_cartridges_implement_mcp_invoke_endpoint():
    """``POST /mcp/invoke`` is the tool-execution contract. Same
    fastmcp 3.x API requirement."""
    for cart in CARTRIDGES:
        src = _main(cart)
        assert "@app.post(\"/mcp/invoke\"" in src, (
            f"{cart} missing POST /mcp/invoke handler"
        )
        assert "await mcp.get_tool(" in src, (
            f"{cart}/app/main.py must call ``await mcp.get_tool(name)`` — "
            f"the fastmcp 3.x API the pins now align with"
        )


def test_all_cartridges_mcp_tools_return_uniform_shape():
    """Console assumes the same JSON shape from all 4 cartridges:
    ``{"tools": [{"name": ..., "description": ..., "input_schema": ...}]}``.
    Verifies that all 4 main.py files build that shape."""
    for cart in CARTRIDGES:
        src = _main(cart)
        for key in ('"name"', '"description"', '"input_schema"', '"tools"'):
            assert key in src, (
                f"{cart}/app/main.py missing {key} in /mcp/tools response shape"
            )


# ─────────────────────────────────────────────────────────────
# security.yml — ignore-list pruning
# ─────────────────────────────────────────────────────────────


def test_security_workflow_retired_fastmcp_2x_ignores():
    """v1.43.2 added 6 GHSAs to suppress fastmcp 2.x vulnerabilities.
    The 3.x bump retires them; the workflow must not silently keep
    suppressing CVEs that no longer apply (would mask future 3.x CVEs
    with the same IDs — unlikely but worth guarding)."""
    src = _security_workflow()
    for ghsa in (
        "GHSA-mxxr-jv3v-6pgc",
        "GHSA-rj5c-58rq-j5g5",
        "GHSA-rcfx-77hg-w2wv",
        "GHSA-5h2m-4q8j-pqpj",
        "GHSA-m8x7-r2rg-vh5g",
        "GHSA-rww4-4w9c-7733",
    ):
        assert ghsa not in src, (
            f"security workflow still suppresses {ghsa} — that GHSA was "
            f"in fastmcp 2.x and is closed by the v1.43.4 3.x bump"
        )


def test_security_workflow_retired_cartridge_transitive_ignores():
    """Same story for mcp / requests / python-dotenv — those bumps
    landed in this hotfix and the suppressions can come off."""
    src = _security_workflow()
    for ghsa in (
        "GHSA-9h52-p55h-vw2f",     # mcp
        "GHSA-9hjg-9r4m-mvj7",     # requests
        "GHSA-gc5v-m9x4-r6x2",     # requests
        "GHSA-mf9w-mj56-hr94",     # python-dotenv
    ):
        assert ghsa not in src, (
            f"security workflow still suppresses {ghsa} — closed by v1.43.4"
        )
