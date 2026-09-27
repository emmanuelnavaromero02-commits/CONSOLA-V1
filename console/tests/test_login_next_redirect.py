from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.main as console_main
from app.domains.security.redirects import safe_login_next


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/viewer/pipeline", "/viewer/pipeline"),
        ("/studio/cartridges/sap_b1", "/studio/cartridges/sap_b1"),
        ("/a b", "/a%20b"),
        ("/x?next=//evil#frag", None),
        ("/x?y=1#frag", "/x%3Fy%3D1%23frag"),
        ("/ñ", "/%C3%B1"),
    ],
)
def test_plain_local_paths_are_quoted(path, expected):
    assert safe_login_next(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        "dashboard",
        "//evil.example",
        "//evil.example/dashboard",
        "/.//evil.example",
        "/\\evil.example",
        "/\\/evil.example",
        "/\t/evil.example",
        "/\n/evil.example",
        "/\x7f/evil.example",
        "https://evil.example",
        "/" + "a" * 2048,
    ],
)
def test_paths_that_could_leave_the_origin_get_no_next(path):
    assert safe_login_next(path) is None


def _redirect(path: str):
    request = SimpleNamespace(headers={"accept": "text/html"})
    return console_main._unauthenticated_middleware_response(
        request, path=path, is_public=False, user=None
    )


def test_unauthenticated_page_redirect_keeps_a_safe_next():
    response = _redirect("/viewer/pipeline")
    assert response.status_code == 307
    assert response.headers["location"] == "/login?next=/viewer/pipeline"


@pytest.mark.parametrize("path", ["/\\evil.example", "//evil.example", "/.//evil.example", "/\t/evil"])
def test_unauthenticated_page_redirect_drops_an_unsafe_next(path):
    response = _redirect(path)
    assert response.status_code == 307
    assert response.headers["location"] == "/login"


def test_decoded_query_and_fragment_characters_stay_inside_next():
    response = _redirect("/monitor?tab=a&x=1#top")
    assert response.headers["location"] == "/login?next=/monitor%3Ftab%3Da%26x%3D1%23top"
    assert _redirect("/monitor?next=https://evil.example").headers["location"] == "/login"
