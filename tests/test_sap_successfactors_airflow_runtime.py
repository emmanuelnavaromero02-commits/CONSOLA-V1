from __future__ import annotations

import importlib
import importlib.abc
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


class _BlockPydanticSettings(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # noqa: ANN001
        if fullname == "pydantic_settings":
            raise ModuleNotFoundError("No module named 'pydantic_settings'")
        return None


def _purge_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def test_successfactors_config_imports_without_pydantic_settings(monkeypatch):
    _purge_app_modules()
    sys.path.insert(0, str(REPO / "cartridges" / "sap_successfactors"))
    blocker = _BlockPydanticSettings()
    sys.meta_path.insert(0, blocker)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@postgres:5432/modecissions")
    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "minio")
    monkeypatch.setenv("MINIO_SECRET_KEY", "secret")
    try:
        config = importlib.import_module("app.core.config")
        assert config.settings.database_url == "postgresql://u:p@postgres:5432/modecissions"
        assert config.settings.minio_endpoint == "minio:9000"
        assert config.settings.sf_auth_method == "oauth2_client_credentials"
    finally:
        sys.meta_path.remove(blocker)
        sys.path.remove(str(REPO / "cartridges" / "sap_successfactors"))
        _purge_app_modules()
