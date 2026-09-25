from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import CARTRIDGES_ROOT

CARTRIDGE_PORTS = {
    "hubspot":            "8210",
    "sap_successfactors": "8203",
    "sap_hcm":            "8202",
    "sap_s4hana":         "8204",
    "sap_b1":             "8206",
}


def _all_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts:
            continue
        if path.suffix in {".pyc", ".parquet", ".duckdb"}:
            continue
        yield path


@pytest.mark.parametrize("cartridge,own_port", list(CARTRIDGE_PORTS.items()))
def test_cartridge_does_not_reference_other_cartridges_port(
    cartridge: str, own_port: str,
) -> None:
    foreign_ports = {p for c, p in CARTRIDGE_PORTS.items() if c != cartridge}
    cart_dir = CARTRIDGES_ROOT / cartridge

    bad_lines: list[str] = []
    for path in _all_text_files(cart_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for port in foreign_ports:
                if f"{cartridge}:{port}" in line:
                    bad_lines.append(
                        f"{path.relative_to(CARTRIDGES_ROOT)}:{lineno}: {line.strip()}"
                    )
    assert not bad_lines, \
        f"{cartridge} references foreign port(s): {bad_lines}"


def test_s4hana_uses_8204_not_8202_in_dags() -> None:
    dags_dir = CARTRIDGES_ROOT / "sap_s4hana" / "dags"
    assert dags_dir.is_dir()

    for path in dags_dir.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "sap_s4hana:8202" not in text, \
            f"{path.name}: still points to port 8202"
        assert "sap_s4hana:8204" in text or "8204" in text, \
            f"{path.name}: no 8204 reference"


def test_dockerfile_cmd_matches_canonical_port() -> None:
    for cartridge, port in CARTRIDGE_PORTS.items():
        dockerfile = CARTRIDGES_ROOT / cartridge / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        assert f'"--port", "{port}"' in text, \
            f"{cartridge}/Dockerfile CMD does not bind {port}"
