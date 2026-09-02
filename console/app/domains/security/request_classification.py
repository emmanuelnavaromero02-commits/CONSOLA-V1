"""Pure request classification rules used by console middleware."""

from __future__ import annotations


def is_api_like(path: str, accept: str, api_like_prefixes: tuple[str, ...]) -> bool:
    if any(path.startswith(prefix) for prefix in api_like_prefixes):
        return True
    return "application/json" in (accept or "")


def is_direct_static_html_request(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("/static/") and lowered.endswith((".html", ".htm"))


def uses_rbac_dependency(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def is_app_content_capability_path(path: str) -> bool:
    """Return whether *path* is exactly the capability-guarded app content route.

    The viewer's inner frame is credentialless, so the request intentionally has
    no session cookie.  Its short-lived capability is validated by the route
    itself; redirecting this request to ``/login`` makes the browser reject the
    framed login page before that validation can run.
    """
    parts = path.split("/")
    return (
        len(parts) == 4
        and parts[0] == ""
        and parts[1] == "apps"
        and bool(parts[2])
        and parts[3] == "content"
    )


def is_agent_runner_request(
    path: str,
    *,
    supplied_token: str,
    expected_token: str,
) -> bool:
    if not (path.startswith("/api/agents/") and path.endswith("/invoke/scheduled")):
        return False
    return bool(expected_token and supplied_token == expected_token)
