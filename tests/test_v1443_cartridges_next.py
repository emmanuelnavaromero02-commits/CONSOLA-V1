"""Sprint v1.44.3 (Tarea A) — /cartridges Next.js page contract.

Static guards — browser-level behaviour (form submission, toast
appearance, password eye toggle) is out of scope for the CI sandbox
and falls to the Mac validation checklist in the PR body.

Covered:
  * console-next/src/lib/cartridges.ts exports the 5 typed helpers
    against the backend endpoints (list, schema, save, test, delete).
  * useCartridges hooks use the shared TanStack `cartridges` root key
    so mutations invalidate the grid.
  * StatusBadge renders the 4 documented states.
  * CartridgeCard navigates to /cartridges/[id] via a Link.
  * CredentialsForm is built with react-hook-form + zod, password
    fields have an eye toggle, AlertDialog confirms delete.
  * Pages exist for grid and detail.
  * Build outputs a /cartridges route and /cartridges/[id] dynamic
    route (we can't run next build from pytest, but we can verify the
    files that produce those routes are present).
"""
from __future__ import annotations

import re
from pathlib import Path


REPO     = Path(__file__).resolve().parents[1]
NEXT_SRC = REPO / "console-next/src"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── lib/cartridges.ts — typed API surface ────────────────────────────────


def test_cartridges_api_helpers_exist():
    src = _read(NEXT_SRC / "lib/cartridges.ts")
    for fn in ("listCartridges", "getConnectorSchema", "saveCredentials",
               "testConnection", "deleteCredentials"):
        assert f"export async function {fn}" in src, (
            f"lib/cartridges.ts missing helper {fn}"
        )


def test_cartridges_helpers_target_correct_endpoints():
    src = _read(NEXT_SRC / "lib/cartridges.ts")
    assert "/api/cartridges" in src
    assert "/connector_schema" in src
    assert "/credentials" in src
    assert "/test_connection" in src


def test_cartridges_api_helper_handles_dict_form_schema():
    """Older connector.yaml files emit a flat ``{ field_a: spec_a, ...}``
    instead of ``{ fields: [...] }``. The helper must tolerate both
    shapes or the form refuses to render for those cartridges."""
    src = _read(NEXT_SRC / "lib/cartridges.ts")
    assert "Object.entries(data)" in src
    assert "Array.isArray(data?.fields)" in src


# ── Hooks ───────────────────────────────────────────────────────────────


def test_use_cartridges_hooks_present():
    src = _read(NEXT_SRC / "lib/hooks/useCartridges.ts")
    for hook in ("useCartridgeList", "useConnectorSchema",
                 "useSaveCredentials", "useTestConnection",
                 "useDeleteCredentials"):
        assert f"export function {hook}" in src, (
            f"useCartridges missing hook {hook}"
        )


def test_save_and_delete_invalidate_cartridges_root_key():
    src = _read(NEXT_SRC / "lib/hooks/useCartridges.ts")
    # Both mutating hooks must call queryClient.invalidateQueries on
    # the root cartridges key so the grid badge reflects the change.
    assert src.count("invalidateQueries") >= 2
    assert 'queryKey: [ROOT_KEY]' in src
    assert 'const ROOT_KEY = "cartridges"' in src


# ── Components ──────────────────────────────────────────────────────────


def test_status_badge_renders_four_states():
    src = _read(NEXT_SRC / "components/cartridges/StatusBadge.tsx")
    for state in ("connected", "untested", "unconfigured", "failed"):
        assert f'"{state}"' in src, f"StatusBadge missing {state} state"
    # The visual emoji set the brief documents.
    for icon in ("🟢", "🟡", "⚪", "🔴"):
        assert icon in src


def test_cartridge_card_navigates_to_detail():
    src = _read(NEXT_SRC / "components/cartridges/CartridgeCard.tsx")
    assert "next/link" in src
    assert "/cartridges/${id}" in src


def test_test_connection_result_shows_both_outcomes():
    src = _read(NEXT_SRC / "components/cartridges/TestConnectionResult.tsx")
    assert "Conexión exitosa" in src
    assert "Conexión fallida" in src
    assert "latency_ms" in src


# ── CredentialsForm ─────────────────────────────────────────────────────


def test_credentials_form_uses_react_hook_form_with_zod():
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    assert "react-hook-form" in src
    assert "@hookform/resolvers/zod" in src
    assert "from \"zod\"" in src
    assert "zodResolver(zodSchema)" in src


def test_credentials_form_has_three_terminal_actions():
    """The brief specifies Save / Test connection / Delete."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    for action in (
        "Guardar credenciales",
        "Probar conexión",
        "Borrar credenciales",
    ):
        assert action in src, f"CredentialsForm missing action: {action}"


def test_credentials_form_password_field_has_eye_toggle():
    """The brief calls out a password show/hide toggle."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    # Eye + EyeOff are the lucide icons used; we check both since the
    # toggle alternates.
    assert "Eye" in src and "EyeOff" in src
    assert "shownPasswords" in src
    # ARIA label flips with state so screen readers narrate it.
    assert "Mostrar contraseña" in src
    assert "Ocultar contraseña" in src


def test_credentials_form_delete_uses_confirmation_dialog():
    """A bare DELETE click would let an operator nuke creds with one
    click. The dialog forces a deliberate confirm."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    assert "ConfirmDeleteDialog" in src
    # The dialog must be modal (aria-modal + role=dialog) AND ESC-dismissable.
    assert 'aria-modal="true"' in src
    assert '"Escape"' in src


def test_credentials_form_uses_sonner_toasts():
    """Per the brief — sonner toasts for save/test/delete outcomes."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    assert 'from "sonner"' in src
    assert "toast.success" in src
    assert "toast.error" in src


def test_zod_schema_adapts_to_required_min_length_url_pattern():
    """The form's runtime validation must respect the schema flags
    the connector.yaml authors supply. Static check: the adapter
    inspects each flag."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    for flag in ("required", "min_length", "max_length", "pattern", "url"):
        assert flag in src, f"zod adapter missing handling for {flag!r}"


# ── Pages ───────────────────────────────────────────────────────────────


def test_cartridges_grid_page_exists():
    page = NEXT_SRC / "app/cartridges/page.tsx"
    assert page.exists()
    src = _read(page)
    assert "useCartridgeList" in src
    assert "CartridgeCard" in src
    # Loading skeleton + error state both rendered (the v1.44.1 dashboard
    # pattern carried into v1.44.3).
    assert "isLoading" in src and "isError" in src


def test_cartridge_detail_page_exists_and_uses_dynamic_param():
    page = NEXT_SRC / "app/cartridges/[id]/page.tsx"
    assert page.exists()
    src = _read(page)
    assert "useParams" in src
    assert "useConnectorSchema" in src
    assert "CredentialsForm" in src


def test_grid_derives_status_from_kpi_freshness():
    """The grid avoids a second per-cartridge endpoint by reading
    /api/dashboard/kpis.data_freshness. Lock the mapping:
      never      → unconfigured
      very_stale → failed
      fresh|stale → connected
    """
    src = _read(NEXT_SRC / "app/cartridges/page.tsx")
    assert "useKpis" in src
    assert '"unconfigured"' in src
    assert '"failed"' in src
    assert '"connected"' in src
    assert "data_freshness" in src


# ── package.json deps for the form stack ────────────────────────────────


def test_package_json_pins_form_deps():
    import json
    pkg = json.loads(_read(REPO / "console-next/package.json"))
    deps = pkg["dependencies"]
    for dep in ("react-hook-form", "@hookform/resolvers", "zod"):
        assert dep in deps, f"console-next missing dep {dep}"
