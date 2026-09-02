from app.domains.security.request_classification import (
    is_agent_runner_request,
    is_api_like,
    is_app_content_capability_path,
    is_direct_static_html_request,
    uses_rbac_dependency,
)


def test_is_api_like_matches_prefixes_and_json_accept():
    prefixes = ("/api/", "/internal/")

    assert is_api_like("/api/data", "", prefixes) is True
    assert is_api_like("/public", "text/html,application/json", prefixes) is True
    assert is_api_like("/public", "text/html", prefixes) is False


def test_is_direct_static_html_request_only_blocks_static_html():
    assert is_direct_static_html_request("/static/index.html") is True
    assert is_direct_static_html_request("/static/page.HTM") is True
    assert is_direct_static_html_request("/static/app.js") is False
    assert is_direct_static_html_request("/viewer/index.html") is False


def test_uses_rbac_dependency_avoids_false_prefix_matches():
    prefixes = ("/jobs", "/api/data")

    assert uses_rbac_dependency("/jobs", prefixes) is True
    assert uses_rbac_dependency("/jobs/job-1", prefixes) is True
    assert uses_rbac_dependency("/jobsX", prefixes) is False
    assert uses_rbac_dependency("/api/datafoo", prefixes) is False


def test_app_content_capability_path_matches_only_the_exact_route_shape():
    assert is_app_content_capability_path("/apps/skill_gaps_heatmap/content") is True

    for path in (
        "/apps/skill_gaps_heatmap/embed",
        "/apps/skill_gaps_heatmap/content/",
        "/apps/skill/gaps/content",
        "/apps//content",
        "/api/apps/skill_gaps_heatmap/content",
    ):
        assert is_app_content_capability_path(path) is False


def test_is_agent_runner_request_requires_scheduled_route_and_token():
    assert (
        is_agent_runner_request(
            "/api/agents/agent-1/invoke/scheduled",
            supplied_token="runner-token",
            expected_token="runner-token",
        )
        is True
    )
    assert (
        is_agent_runner_request(
            "/api/agents/agent-1/invoke",
            supplied_token="runner-token",
            expected_token="runner-token",
        )
        is False
    )
    assert (
        is_agent_runner_request(
            "/api/agents/agent-1/invoke/scheduled",
            supplied_token="wrong",
            expected_token="runner-token",
        )
        is False
    )
    assert (
        is_agent_runner_request(
            "/api/agents/agent-1/invoke/scheduled",
            supplied_token="runner-token",
            expected_token="",
        )
        is False
    )
