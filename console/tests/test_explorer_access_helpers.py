from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.data_platform.explorer_access import (
    explorer_path_allowed,
    explorer_quicklinks_for_cartridges,
    resolve_explorer_bucket,
)


def test_resolve_explorer_bucket_allows_lakehouse_for_workspace_user():
    buckets = [{"id": "lakehouse", "label": "Lakehouse", "name": "lakehouse-prod"}]

    assert (
        resolve_explorer_bucket(
            "lakehouse",
            buckets,
            is_security_admin=False,
            lakehouse_bucket="lakehouse-prod",
        )
        == "lakehouse-prod"
    )


def test_resolve_explorer_bucket_rejects_admin_bucket_for_workspace_user():
    buckets = [{"id": "ses_inbox", "label": "SES", "name": "mail-bucket"}]

    with pytest.raises(HTTPException) as exc:
        resolve_explorer_bucket(
            "ses_inbox",
            buckets,
            is_security_admin=False,
            lakehouse_bucket="lakehouse",
        )

    assert exc.value.status_code == 403


def test_explorer_path_allowed_requires_scoped_object_markers():
    context = {
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "allowed_cartridges": ["sap_successfactors"],
    }

    assert explorer_path_allowed(
        "raw/sap_successfactors/PerPerson/",
        context,
        is_security_admin=False,
    )
    assert explorer_path_allowed(
        "raw/sap_successfactors/PerPerson/tenant_id=tenant-1/workspace_id=workspace-1/data.parquet",
        context,
        is_security_admin=False,
        object_access=True,
    )
    assert not explorer_path_allowed(
        "raw/sap_successfactors/PerPerson/tenant_id=other/workspace_id=workspace-1/data.parquet",
        context,
        is_security_admin=False,
        object_access=True,
    )


def test_explorer_path_allowed_fails_closed_for_wildcard_workspace_context():
    context = {
        "tenant_id": "tenant-1",
        "workspace_id": "workspace-1",
        "allowed_cartridges": ["*"],
    }

    assert not explorer_path_allowed(
        "raw/sap_successfactors/",
        context,
        is_security_admin=False,
    )


def test_explorer_quicklinks_are_generated_per_cartridge():
    quicklinks = explorer_quicklinks_for_cartridges({"sap_successfactors"})

    assert quicklinks == [
        {
            "label": "Raw - sap successfactors",
            "bucket": "lakehouse",
            "prefix": "raw/sap_successfactors/",
        },
        {
            "label": "Silver - sap successfactors",
            "bucket": "lakehouse",
            "prefix": "silver/sap_successfactors/",
        },
        {
            "label": "Gold - sap successfactors",
            "bucket": "lakehouse",
            "prefix": "gold/sap_successfactors/",
        },
    ]
