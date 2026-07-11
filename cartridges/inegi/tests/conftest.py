from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

CARTRIDGE_ROOT = str(Path(__file__).resolve().parents[1])
REPO_ROOT = str(Path(__file__).resolve().parents[3])

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("INTERNAL_API_KEY", "test-secret-key-not-default")
os.environ.setdefault("INEGI_API_TOKEN", "unit-test-inegi-token")
os.environ.setdefault("MINIO_ACCESS_KEY", "test-minio-access")
os.environ.setdefault("MINIO_SECRET_KEY", "test-minio-secret")


def _use_paths() -> None:
    siblings = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [path for path in sys.path if not any(marker in path for marker in siblings)]
    for path in (CARTRIDGE_ROOT, REPO_ROOT):
        if path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, REPO_ROOT)
    sys.path.insert(0, CARTRIDGE_ROOT)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]


_use_paths()


@pytest.fixture(autouse=True)
def _isolate_app():
    _use_paths()
    yield
