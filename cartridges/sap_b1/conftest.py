from __future__ import annotations

import os
import sys

import pytest

_CARTRIDGE_ROOT = os.path.dirname(__file__)
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://test:test@postgres:5432/modecissions")
os.environ.setdefault("MINIO_ACCESS_KEY", "test-minio-access")
os.environ.setdefault("MINIO_SECRET_KEY", "test-minio-secret")


def _use_this_cartridge() -> None:
    if _CARTRIDGE_ROOT in sys.path:
        sys.path.remove(_CARTRIDGE_ROOT)
    sys.path.insert(0, _CARTRIDGE_ROOT)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]


_use_this_cartridge()


@pytest.fixture(autouse=True)
def _isolate_cartridge_app():
    _use_this_cartridge()
    yield
