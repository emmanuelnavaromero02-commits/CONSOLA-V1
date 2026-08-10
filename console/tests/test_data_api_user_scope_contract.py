from __future__ import annotations

from pathlib import Path


def _api_data_section(source: str) -> str:
    return source.split("async def api_data(", 1)[1].split(
        "async def api_data_options", 1
    )[0]


def test_data_api_uses_authenticated_user_dependency_in_main_and_v1_router():
    for path in (
        Path("console/app/main.py"),
        Path("console/app/routers/v1/data.py"),
    ):
        section = _api_data_section(path.read_text())
        assert 'user: dict = Depends(require_permission("datasets.read"))' in section
        assert 'getattr(request.state, "user"' not in section
        assert (
            "from app.services.intelligence.gold_fetcher import query_gold_dataset_rows"
            in section
        )
        assert "query_gold_dataset_rows(dataset, user, limit)" in section
