from __future__ import annotations

from importlib import import_module

__all__: list[str] = []


def __getattr__(name: str) -> object:
    core = import_module(f"{__name__}.core")

    return getattr(core, name)
