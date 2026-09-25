from __future__ import annotations

from pathlib import Path

import pytest

from app.services.seed_packaged_catalog import parse_dataset


REPO = Path(__file__).resolve().parents[2]
HEADER = (
    "-- omega_movements  (silver)  cartridge: omega_test\n"
    '-- sources: ["raw/omega_test/Movements"]\n'
    "-- description: Movements by month.\n"
)


def _dataset(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "omega_movements.sql"
    path.write_text(HEADER + extra + "SELECT 1 AS period\n", encoding="utf-8")
    return path


def test_partition_header_is_exposed_with_the_packaged_dataset(tmp_path):
    parsed = parse_dataset(_dataset(tmp_path, "-- partition_by: period\n"))

    assert parsed["partition_by"] == "period"
    assert parsed["sources"] == ["raw/omega_test/Movements"]
    assert parsed["sql"].startswith(HEADER)


def test_datasets_without_the_header_stay_unpartitioned(tmp_path):
    assert parse_dataset(_dataset(tmp_path, ""))["partition_by"] == ""


@pytest.mark.parametrize(
    "extra",
    [
        "-- partition_by: period, company\n",
        "-- partition_by:\n",
        "-- partition_by: 2024\n",
        "-- partition_by: period\n-- partition_by: company\n",
    ],
)
def test_invalid_partition_headers_fail_the_packaged_catalog(tmp_path, extra):
    with pytest.raises(ValueError, match="partition_by"):
        parse_dataset(_dataset(tmp_path, extra))


def test_every_packaged_dataset_header_still_parses():
    paths = sorted((REPO / "cartridges").glob("*/datasets/*.sql"))

    assert paths
    for path in paths:
        parse_dataset(path)
