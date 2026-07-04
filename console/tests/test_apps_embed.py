from __future__ import annotations

from app.domains.apps.embed import workspace_server_url


def test_workspace_server_url_prefers_internal_url():
    assert (
        workspace_server_url(
            environ={
                "WORKSPACE_INTERNAL_URL": "http://workspace.internal:8001/",
                "WORKSPACE_PUBLIC_URL": "https://workspace.example.com",
            }
        )
        == "http://workspace.internal:8001"
    )


def test_workspace_server_url_uses_service_name_for_local_public_url_in_docker():
    assert (
        workspace_server_url(
            environ={"WORKSPACE_PUBLIC_URL": "http://localhost:8001"},
            docker_env_exists=lambda: True,
        )
        == "http://workspace:8001"
    )


def test_workspace_server_url_warns_and_returns_empty_in_production_without_config():
    warnings: list[str] = []

    assert (
        workspace_server_url(
            environ={},
            docker_env_exists=lambda: False,
            is_production_env=lambda: True,
            logger_warning=warnings.append,
        )
        == ""
    )
    assert warnings == ["WORKSPACE_INTERNAL_URL is not configured in production"]


def test_workspace_server_url_defaults_to_localhost_outside_production():
    assert (
        workspace_server_url(
            environ={},
            docker_env_exists=lambda: False,
            is_production_env=lambda: False,
        )
        == "http://localhost:8001"
    )
