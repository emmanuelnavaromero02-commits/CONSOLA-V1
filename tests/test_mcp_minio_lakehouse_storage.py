from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


class FakeStorage:
    def __init__(self) -> None:
        self.objects = {
            "cartridges/replicon/specs/openapi.yaml": b"openapi: 3.0.0",
        }
        self.uploads = {}

    def iter_list(self, prefix):
        return iter(
            [
                SimpleNamespace(key=key, size=len(value), updated_at=datetime.now(timezone.utc))
                for key, value in sorted(self.objects.items())
                if key.startswith(prefix)
            ]
        )

    def stat(self, key):
        return SimpleNamespace(size=len(self.objects[key]))

    def get_bytes(self, key):
        return self.objects[key]

    def put_bytes(self, key, data, *, overwrite=False, metadata=None):
        self.uploads[key] = (data, overwrite, metadata)
        self.objects[key] = data
        return SimpleNamespace(uri=f"s3://lakehouse/{key}")


def test_mcp_spec_tools_use_lakehouse_storage(monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    monkeypatch.syspath_prepend(str(repo / "mcp-infra"))
    for env_name in ("AIRFLOW_USER", "AIRFLOW_PASSWORD", "PG_PASSWORD", "SUPERSET_USER", "SUPERSET_PASSWORD"):
        monkeypatch.setenv(env_name, "test")
    module = importlib.import_module("app.tools.minio")
    fake = FakeStorage()
    monkeypatch.setattr(module, "storage_from_env", lambda *, bucket=None: fake)

    listed = module.minio_list_cartridge_specs("replicon")
    read = module.minio_read_spec("replicon", "openapi.yaml")
    uploaded = module.minio_upload_spec("replicon", "metadata.xml", "<xml />")

    assert listed["specs"][0]["name"] == "openapi.yaml"
    assert read["content"] == "openapi: 3.0.0"
    assert uploaded["uploaded"] == "cartridges/replicon/specs/metadata.xml"
    assert fake.uploads["cartridges/replicon/specs/metadata.xml"][1] is True
