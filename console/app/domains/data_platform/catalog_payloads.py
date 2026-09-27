from __future__ import annotations

import json
from typing import Any


def catalog_query_args(
    *,
    layer: str = "",
    cartridge: str = "",
    tags: str = "",
    datasets: str = "",
    include_sources: bool = False,
) -> dict[str, Any]:
    args: dict[str, Any] = {}
    if include_sources:
        args["include_sources"] = True
    if layer:
        args["layer"] = layer
    if cartridge:
        args["cartridge"] = cartridge
    if tags:
        args["tags"] = [tag.strip() for tag in tags.split(",") if tag.strip()]
    if datasets:
        args["datasets"] = [
            dataset.strip() for dataset in datasets.split(",") if dataset.strip()
        ]
    return args


def catalog_cache_key(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, default=str)
