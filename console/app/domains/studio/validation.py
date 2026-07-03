from __future__ import annotations

import os
import re

from fastapi import HTTPException


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
SAFE_DAG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,127}$")
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def valid_identifier(value: str | None) -> bool:
    return bool(IDENTIFIER_RE.fullmatch((value or "").strip()))


def clean_identifier(value: str, *, label: str) -> str:
    ident = (value or "").strip()
    if not valid_identifier(ident):
        raise HTTPException(
            400, f"Invalid {label}: use letters, numbers and underscores only"
        )
    return ident


def clean_filename(value: str | None) -> str:
    name = SAFE_FILENAME_RE.sub("_", os.path.basename(value or "spec.yaml")).strip("._")
    return name or "spec.yaml"


def clean_dag_id(value: str | None) -> str:
    dag_id = (value or "").strip()
    if not SAFE_DAG_ID_RE.fullmatch(dag_id):
        raise HTTPException(
            400, "Invalid dag_id: use letters, numbers and underscores only"
        )
    return dag_id
