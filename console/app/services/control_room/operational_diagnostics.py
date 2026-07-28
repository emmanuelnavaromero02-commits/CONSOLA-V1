from __future__ import annotations

from app.schemas.control_room_surfaces import ControlRoomDiagnosticsResponse
from app.services.control_room.business_projection import (
    diagnostic_items,
    filter_business_items,
    strip_business_fields,
)
from app.services.control_room.business_surface_identity import (
    resolve_business_surface_identity,
)
from app.services.control_room.diagnostic_redaction import redact_diagnostic_value
from app.services.control_room.diagnostics_public_factory import (
    RawDiagnosticsDraft,
    build_diagnostics_response,
)
from app.services.control_room.surface_snapshot import (
    SurfaceSnapshot,
    validate_snapshot_scope,
)


def build_operational_diagnostics(
    snapshot: SurfaceSnapshot,
) -> ControlRoomDiagnosticsResponse:
    validate_snapshot_scope(snapshot)
    unsectioned = [
        strip_business_fields(item)
        for item in filter_business_items(snapshot.items)
        if resolve_business_surface_identity(item) is None
    ]
    technical = [
        *snapshot.diagnostics,
        *diagnostic_items(snapshot.items),
        *unsectioned,
    ]
    return build_diagnostics_response(
        RawDiagnosticsDraft(
            generated_at=snapshot.generated_at,
            sources=snapshot.sources,
            diagnostic_items=tuple(technical),
            installations=snapshot.installations,
        )
    )


__all__ = ("build_operational_diagnostics", "redact_diagnostic_value")
