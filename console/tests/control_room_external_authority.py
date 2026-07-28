from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


def external_authority(item: dict, template: dict, payload: dict):
    return SimpleNamespace(
        item=item,
        template=template,
        payload=payload,
        authority_audit={"test_only_verified_authority": True},
    )


@contextmanager
def patch_started_revalidation(service, authority):
    with (
        patch.object(
            service,
            "revalidate_authoritative_action",
            new=AsyncMock(
                return_value=(
                    authority,
                    {"metadata": {"remote_attempt": {"status": "started"}}},
                )
            ),
        ),
        patch.object(
            service,
            "require_authoritative_binding_current",
            new=Mock(),
        ),
    ):
        yield


__all__ = ("external_authority", "patch_started_revalidation")
