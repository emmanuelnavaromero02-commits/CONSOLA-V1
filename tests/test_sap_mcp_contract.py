from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CARTRIDGES = (
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
    "sap_b1",
    "replicon",
    "hubspot",
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


def test_all_cartridges_pin_fastmcp_to_3_x():
    for cart in CARTRIDGES:
        src = _reqs(cart)
        assert re.search(
            r"^fastmcp>=3\.\d+\.\d+,<4\.0\s*$", src, re.MULTILINE
        ), f"{cart} must pin fastmcp to >=3.x,<4.0; got:\n{src}"
        assert "fastmcp==2." not in src, (
            f"{cart} still references fastmcp 2.x — would reintroduce "
            f"the C1 AttributeError on /mcp/tools"
        )


def test_all_cartridges_pin_pydantic_compatible_with_fastmcp_3():
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1"):
        src = _reqs(cart)
        assert re.search(
            r"^pydantic>=2\.11\.\d+,<3\.0\s*$", src, re.MULTILINE
        ), f"{cart} must pin pydantic to >=2.11.7,<3.0 (fastmcp 3.x floor)"


def test_all_cartridges_pin_uvicorn_compatible_with_fastmcp_3_server():
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1"):
        src = _reqs(cart)
        assert re.search(
            r"^uvicorn\[standard\]>=0\.3[5-9]\.\d+", src, re.MULTILINE
        ) or re.search(
            r"^uvicorn\[standard\]>=0\.[4-9]\d?\.\d+", src, re.MULTILINE
        ), f"{cart} must pin uvicorn[standard] to >=0.35 (fastmcp 3.x server)"


def test_sap_cartridges_no_longer_pin_vulnerable_requests():
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1"):
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
    for cart in ("sap_hcm", "sap_s4hana", "sap_successfactors", "sap_b1"):
        src = _reqs(cart)
        assert "python-dotenv==1.1.0" not in src
        assert re.search(
            r"^python-dotenv>=1\.[2-9]\.\d+", src, re.MULTILINE
        ), f"{cart} must pin python-dotenv to >=1.2.2"


def test_all_cartridges_implement_mcp_tools_endpoint():
    for cart in CARTRIDGES:
        src = _main(cart)
        assert "@app.get(\"/mcp/tools\"" in src, (
            f"{cart} missing GET /mcp/tools handler"
        )
        assert "await mcp.list_tools()" in src, (
            f"{cart}/app/main.py must call ``await mcp.list_tools()`` — "
            f"the fastmcp 3.x API the pins now align with"
        )


def test_all_cartridges_implement_mcp_invoke_endpoint():
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
    for cart in CARTRIDGES:
        src = _main(cart)
        for key in ('"name"', '"description"', '"input_schema"', '"tools"'):
            assert key in src, (
                f"{cart}/app/main.py missing {key} in /mcp/tools response shape"
            )


def test_security_workflow_retired_fastmcp_2x_ignores():
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
    src = _security_workflow()
    for ghsa in (
        "GHSA-9h52-p55h-vw2f",
        "GHSA-9hjg-9r4m-mvj7",
        "GHSA-gc5v-m9x4-r6x2",
        "GHSA-mf9w-mj56-hr94",
    ):
        assert ghsa not in src, (
            f"security workflow still suppresses {ghsa} — closed by v1.43.4"
        )
