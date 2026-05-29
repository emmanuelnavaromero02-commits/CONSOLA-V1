"""Make ``import app`` resolve to THIS cartridge when running the suite
from the repo root (``pytest cartridges -q``).

Every cartridge ships its own top-level ``app`` package. Without this
shim pytest only puts the ``tests/`` directory on ``sys.path`` (there is
no ``app`` there) and collection dies with
``ModuleNotFoundError: No module named 'app'``. And once one cartridge's
``app`` is cached in ``sys.modules`` a sibling cartridge would silently
import the wrong package, so we also evict the cached modules before each
test to keep every cartridge pinned to its own code.
"""
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


# Import time: the test module's top-level ``from app... import ...`` runs
# during collection, so point it at this cartridge before that happens.
_use_this_cartridge()


@pytest.fixture(autouse=True)
def _isolate_cartridge_app():
    # Re-assert per test: a sibling cartridge collected afterwards may have
    # repointed sys.path / repopulated sys.modules['app'].
    _use_this_cartridge()
    yield
