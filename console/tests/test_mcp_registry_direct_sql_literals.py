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
        f"FROM {READ} a, \"/tmp/secret.csv\" b",
        f"FROM {READ} a, 'file:///etc/passwd' b",
        f"FROM {READ} UNION ALL SELECT * FROM main.\"read_text\"(chr(47) || 'etc')",
        f"FROM {READ} UNION ALL SELECT * FROM \"glob\"('*')",
        f"FROM {READ} UNION ALL SELECT * FROM read_text('/etc/hosts')",
        f"FROM {READ} UNION ALL SELECT * FROM (FROM 'x.csv')",
    ],
)
def test_stray_storage_literals_are_rejected_before_reaching_a_cartridge(tail):
    ctx = {"tenant_id": "t1", "workspace_id": "w1", "allowed_prefixes": ["raw/sap_b1/"]}
    with pytest.raises(PermissionError):
        mcp_registry._validate_direct_cartridge_sql(ctx, "sap_b1", f"SELECT * {tail} LIMIT 5")


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT coalesce(\"CardName\", 'unknown') FROM {READ} LIMIT 5",
        f"SELECT * FROM {READ} WHERE status IN ('open', 'closed') LIMIT 5",
        f"SELECT strftime(\"DocDate\", '%Y-%m') FROM {READ} LIMIT 5",
        f"SELECT \"DocNum\", \"CardCode\" FROM {READ} LIMIT 5",
        f"SELECT replace(\"CardName\", '/', '-') FROM {READ} WHERE \"U_Web\" LIKE 'https://%' LIMIT 5",
    ],
)
def test_ordinary_values_after_commas_reach_the_cartridge(sql):
    ctx = {"tenant_id": "t1", "workspace_id": "w1", "allowed_prefixes": ["raw/sap_b1/"]}
    mcp_registry._validate_direct_cartridge_sql(ctx, "sap_b1", sql)
