from __future__ import annotations

import hashlib
import json
import pathlib
import re


_HEADER_RE = re.compile(
    r"^--\s*(?P<name>[A-Za-z_][\w]*)\s+\((?P<layer>[^)]+)\)\s+cartridge:\s*(?P<cartridge>[\w-]+)",
)


def dataset_files(
    registry: pathlib.Path,
    *,
    expected_files: int,
    expected_digest: str,
) -> dict[str, list[pathlib.Path]]:
    if not registry.exists():
        return {}
    out = {
        directory.parent.name: sorted(directory.glob("*.sql"))
        for directory in registry.glob("*/datasets")
        if directory.is_dir()
    }
    paths = sorted(
        (path for cartridge_paths in out.values() for path in cartridge_paths),
        key=lambda path: path.relative_to(registry).as_posix(),
    )
    entries = [
        f"{path.relative_to(registry).as_posix()}\0"
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}"
        for path in paths
    ]
    digest = hashlib.sha256(("\n".join(entries) + "\n").encode()).hexdigest()
    if len(entries) != expected_files or digest != expected_digest:
        raise ValueError(
            "packaged dataset catalog integrity check failed: "
            f"expected {expected_files} versioned files"
        )
    return out


def parse_dataset(sql_path: pathlib.Path) -> dict:
    sql = sql_path.read_text(encoding="utf-8")
    first_line = sql.splitlines()[0] if sql.splitlines() else ""
    match = _HEADER_RE.match(first_line)
    if match is None:
        raise ValueError(f"{sql_path}: missing or invalid packaged dataset header")
    name = match.group("name")
    layer = match.group("layer").strip().lower()
    cartridge = match.group("cartridge").strip()
    if layer not in {"gold", "master", "silver"}:
        raise ValueError(f"{sql_path}: unsupported packaged dataset layer {layer!r}")
    if layer == "master":
        layer = "gold"

    sources: list[str] = []
    description = ""
    for line in sql.splitlines()[:20]:
        stripped = line.strip()
        if stripped.startswith("-- sources:"):
            raw = stripped.removeprefix("-- sources:").strip()
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{sql_path}: invalid packaged dataset sources"
                ) from exc
            if not isinstance(parsed, list) or not all(
                isinstance(item, str) and item.strip() for item in parsed
            ):
                raise ValueError(
                    f"{sql_path}: packaged dataset sources must be a string list"
                )
            sources = list(parsed)
        elif stripped.startswith("-- description:"):
            description = stripped.removeprefix("-- description:").strip()
    return {
        "name": name,
        "layer": layer,
        "cartridge": cartridge,
        "sources": sources,
        "description": description,
        "sql": sql,
    }


def load_packaged_manifest(
    packaged: dict[str, list[pathlib.Path]],
) -> dict[str, list[dict]]:
    if not packaged:
        raise ValueError("packaged dataset manifest is empty")
    manifest: dict[str, list[dict]] = {}
    names: dict[str, pathlib.Path] = {}
    for cartridge_id in sorted(packaged):
        sql_files = sorted(packaged[cartridge_id], key=lambda path: path.as_posix())
        if not sql_files:
            raise ValueError(f"packaged dataset manifest is empty for {cartridge_id}")
        datasets: list[dict] = []
        for sql_path in sql_files:
            dataset = parse_dataset(sql_path)
            if dataset["cartridge"] != cartridge_id:
                raise ValueError(
                    f"{sql_path}: cartridge {dataset['cartridge']!r} does not match "
                    f"manifest {cartridge_id!r}"
                )
            if dataset["name"] in names:
                raise ValueError(
                    f"duplicate packaged dataset {dataset['name']!r}: "
                    f"{names[dataset['name']]} and {sql_path}"
                )
            names[dataset["name"]] = sql_path
            datasets.append(dataset)
        manifest[cartridge_id] = datasets
    return manifest
