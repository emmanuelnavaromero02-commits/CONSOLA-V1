from __future__ import annotations

import pytest

from app.services import mcp_registry

READ = "read_parquet('s3://lakehouse/raw/sap_b1/OINV/tenant_id=t1/workspace_id=w1/**/*.parquet')"


@pytest.mark.parametrize(
    "tail",
    [
        f"FROM {READ} a, '/tmp/secret.csv' b",
        f"FROM {READ} a, 's3://lakehouse/raw/other/x.parquet' b",
        "FROM E'/tmp/secret.csv'",
        f"FROM {READ} a JOIN E'/etc/passwd' b ON TRUE",
    ],
)
def test_stray_storage_literals_are_rejected_before_reaching_a_cartridge(tail):
    ctx = {"tenant_id": "t1", "workspace_id": "w1", "allowed_prefixes": ["raw/sap_b1/"]}
    with pytest.raises(PermissionError):
        mcp_registry._validate_direct_cartridge_sql(ctx, "sap_b1", f"SELECT * {tail} LIMIT 5")
