from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT  = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "infra" / "airflow" / "Dockerfile"
COMPOSE    = REPO_ROOT / "infra" / "docker-compose.yml"

EXPECTED_VERSION = "2.10.5"
EXPECTED_IMAGE_TAG = f"mode-airflow:{EXPECTED_VERSION}-local"
EXPECTED_FROM_LINE = f"FROM apache/airflow:{EXPECTED_VERSION}"

_FROM_RE  = re.compile(r"^FROM\s+apache/airflow:([^\s]+)", re.MULTILINE)
_IMAGE_RE = re.compile(r"^\s*image:\s*(mode-airflow:[^\s]+)", re.MULTILINE)


def _airflow_2x_or_die(tag: str) -> tuple[int, int, int]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", tag)
    assert m, f"airflow tag {tag!r} is not a strict semver — refusing to interpret"
    major, minor, patch = (int(x) for x in m.groups())
    assert major == 2, (
        f"airflow major version is {major}.x — this sprint stays on 2.x. "
        f"3.x is a breaking-change upgrade we deliberately do NOT take here."
    )
    return major, minor, patch


def test_dockerfile_pins_airflow_2_10_5() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    m = _FROM_RE.search(text)
    assert m, f"no FROM apache/airflow:* line in {DOCKERFILE}"
    tag = m.group(1)
    _airflow_2x_or_die(tag)
    assert tag == EXPECTED_VERSION, (
        f"Dockerfile pins apache/airflow:{tag} — expected {EXPECTED_VERSION}"
    )
    assert EXPECTED_FROM_LINE in text


def test_docker_compose_image_tags_match_dockerfile() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    tags = _IMAGE_RE.findall(text)
    airflow_tags = [t for t in tags if t.startswith("mode-airflow:")]
    assert len(airflow_tags) == 3, (
        f"expected 3 mode-airflow image references, found {len(airflow_tags)}: {airflow_tags}"
    )
    assert all(t == EXPECTED_IMAGE_TAG for t in airflow_tags), (
        f"compose mode-airflow tags drift: {airflow_tags!r}; expected all to be {EXPECTED_IMAGE_TAG!r}"
    )


def test_no_stale_2_9_x_references_remain() -> None:
    for path in (DOCKERFILE, COMPOSE):
        text = path.read_text(encoding="utf-8")
        assert "apache/airflow:2.9" not in text, f"{path} still references apache/airflow:2.9"
        assert "mode-airflow:2.9" not in text, f"{path} still references mode-airflow:2.9"
