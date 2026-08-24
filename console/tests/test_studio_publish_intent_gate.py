"""Red-team: publish_app/delete_app exigen intención explícita en el mensaje
del usuario (canal confiable). Cierra las 14/14 inyecciones de Studio."""
from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "local")

from app.services import studio_assistant as sa  # noqa: E402


def _allow(bare, message, *, step=3, role="admin"):
    return sa._admin_direct_write_allowed(
        step=step,
        bare_name=bare,
        risk_meta={"risk_level": "write"},
        actor_role=role,
        actor_user={"role": role, "id": 1},
        message=message,
        refine_preview_ok=True,
    )


POISONED = [
    "muéstrame el reporte de ventas",
    "resume los datos del cartucho",
    "¿qué apps existen?",
    "analiza el dataset",
    "",
]


def test_publish_blocked_without_user_intent():
    for msg in POISONED:
        assert _allow("publish_app", msg) is False, msg


def test_publish_allowed_with_explicit_user_intent():
    for msg in ("publica la app de ventas", "actualiza la app kpi", "genera la app"):
        assert _allow("publish_app", msg) is True, msg


def test_delete_app_is_always_blocked_from_direct_write():
    # delete_app es destructivo: vive en _STUDIO_DIRECT_ADMIN_BLOCKED_TOOLS y
    # nunca es escritura directa, ni con intencion explicita (requiere aprobacion).
    assert _allow("delete_app", "elimina la app vieja") is False
    assert _allow("delete_app", "publica y elimina") is False


def test_other_writes_unchanged():
    # create_entity conserva su propia puerta de intención
    assert _allow("create_entity", "crea una entidad nueva") is True
    assert _allow("create_entity", "muestra el catálogo") is False
    # writes de refinamiento siguen por su ruta de step
    assert _allow("save_dataset", "ok", step=4) is True


def test_analyst_never_direct_writes():
    assert _allow("publish_app", "publica la app", role="analyst") is False
