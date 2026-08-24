"""Red-team hardening (parcial, seguro): no-new-privileges en todos los
servicios del compose base. Impide GANAR privilegios via setuid-execve sin
romper drops de privilegio existentes. Las piezas mas riesgosas (cap_drop,
read_only, USER de Refinement, binding a 127.0.0.1) quedan como seguimiento
con validacion full-stack (ver reporte)."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"


def _service_names(text: str) -> list[str]:
    lines = text.splitlines()
    vol_idx = next(i for i, l in enumerate(lines) if l.rstrip() == "volumes:")
    svc = re.compile(r"^  ([a-z][a-z0-9_-]*):\s*$")
    return [m.group(1) for i, l in enumerate(lines) if i < vol_idx and (m := svc.match(l))]


def test_every_service_has_no_new_privileges():
    text = COMPOSE.read_text(encoding="utf-8")
    names = _service_names(text)
    assert len(names) >= 20, "sanidad: se esperan ~26 servicios"
    # Cada bloque de servicio (hasta el siguiente servicio) debe declararlo.
    lines = text.splitlines()
    svc = re.compile(r"^  ([a-z][a-z0-9_-]*):\s*$")
    starts = [i for i, l in enumerate(lines) if svc.match(l) and svc.match(l).group(1) in names]
    starts.append(len(lines))
    for a, b in zip(starts, starts[1:]):
        block = "\n".join(lines[a:b])
        name = svc.match(lines[a]).group(1)
        assert "no-new-privileges:true" in block, f"{name} sin no-new-privileges"
