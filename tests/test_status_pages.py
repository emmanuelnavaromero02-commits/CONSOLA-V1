from __future__ import annotations

from types import SimpleNamespace

from app.services import status_pages


def test_functional_status_page_escapes_detail():
    response = status_pages.functional_status_page(404, "<script>alert(1)</script>")

    body = response.body.decode("utf-8")
    assert response.status_code == 404
    assert "Página no disponible" in body
    assert "&lt;script&gt;" not in body
    assert "<script>" not in body


def test_status_page_body_uses_safe_business_copy():
    assert "permisos necesarios" in status_pages.status_page_body(403, "ignored")
    assert "no respondió a tiempo" in status_pages.status_page_body(503, "ignored")
    assert status_pages.status_page_body(418, "detalle") == "detalle"


def test_internal_error_request_id_preserves_valid_state_id():
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="12345678-1234-5678-1234-567812345678")
    )

    assert (
        status_pages.internal_error_request_id(request)
        == "12345678-1234-5678-1234-567812345678"
    )

