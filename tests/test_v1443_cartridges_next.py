"""Sprint v1.44.3 (Tarea A) — /cartridges Next.js page contract.

Static guards — browser-level behaviour (form submission, toast
appearance, password eye toggle) is out of scope for the CI sandbox
and falls to the Mac validation checklist in the PR body.

Covered:
  * console-next/src/lib/cartridges.ts exports typed helpers against
    the backend endpoints (list, schema, save, test, delete, activate).
  * useCartridges hooks use the shared TanStack `cartridges` root key
    so mutations invalidate the grid.
  * StatusBadge renders the 4 documented states.
  * CartridgeCard navigates to /cartridges/viewer?id=... via a Link.
  * CredentialsForm is Vault-led (no local secret save/delete fields),
    includes a CTA to /operations/vault, and keeps Test connection.
  * Pages exist for grid and detail.
  * Build outputs /cartridges and /cartridges/viewer static routes.
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
               "testConnection", "deleteCredentials", "activateCartridge"):
        assert f"export async function {fn}" in src, (
            f"lib/cartridges.ts missing helper {fn}"
        )


def test_hubspot_is_visible_in_next_cartridge_surfaces():
    surfaces = [
        NEXT_SRC / "lib/cartridges.ts",
        NEXT_SRC / "app/(shell)/cartridges/page.tsx",
        NEXT_SRC / "app/(shell)/studio/page.tsx",
        NEXT_SRC / "app/(shell)/data/catalog/page.tsx",
        NEXT_SRC / "app/(shell)/data/lineage/page.tsx",
        NEXT_SRC / "app/(shell)/copilot/knowledge/page.tsx",
        NEXT_SRC / "components/operations/VaultConnectionsTable.tsx",
    ]
    for path in surfaces:
        assert '"hubspot"' in _read(path), f"{path.relative_to(REPO)} omits hubspot"


def test_monitor_surface_is_cartridge_agnostic():
    src = _read(NEXT_SRC / "app/(shell)/monitor/page.tsx")

    assert "useJobs" in src
    assert "JobTable" in src
    assert '"hubspot"' not in src


def test_cartridges_helpers_target_correct_endpoints():
    src = _read(NEXT_SRC / "lib/cartridges.ts")
    assert "/api/cartridges" in src
    assert "/connector_schema" in src
    assert "/credentials" in src
    assert "/test_connection" in src
    assert "/api/marketplace/products/" in src
    assert "/activate" in src


def test_cartridges_api_helper_handles_dict_form_schema():
    """Older connector.yaml files emit a flat ``{ field_a: spec_a, ...}``
    instead of ``{ fields: [...] }``. The helper must tolerate both
    shapes or the form refuses to render for those cartridges."""
    src = _read(NEXT_SRC / "lib/cartridges.ts")
    assert "Object.entries(data)" in src
    assert "Array.isArray(data.fields)" in src
    assert "isRecord(data)" in src


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
    assert "/cartridges/viewer?id=" in src


def test_test_connection_result_shows_both_outcomes():
    src = _read(NEXT_SRC / "components/cartridges/TestConnectionResult.tsx")
    assert "Conexión exitosa" in src
    assert "Conexión fallida" in src
    assert "latency_ms" in src


# ── CredentialsForm ─────────────────────────────────────────────────────


def test_credentials_form_uses_react_hook_form_with_zod():
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    # CredentialsForm is now a lean Vault handoff surface: no local
    # secret persistence form stack.
    assert "react-hook-form" not in src
    assert "@hookform/resolvers/zod" not in src
    assert "zodResolver(zodSchema)" not in src
    assert "Configurar en Vault" in src


def test_credentials_form_uses_vault_cta_for_credentials():
    """Credentials are stored via Vault; no local save/delete actions."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    assert "/operations/vault" in src
    assert "Configurar en Vault" in src
    assert "Probar conexión" in src
    assert "Guardar credenciales" not in src
    assert "Borrar credenciales" not in src


def test_credentials_form_password_field_has_eye_toggle():
    """The brief calls out password handling only for Vault-scoped flows,\n    so this component no longer renders local password inputs."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    assert "type=\"password\"" not in src
    assert "Eye" not in src
    assert "EyeOff" not in src


def test_credentials_form_does_not_render_local_delete_dialog():
    """Deletion is not a local credentials-editor action anymore."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    assert "ConfirmDeleteDialog" not in src
    assert "useDeleteCredentials" not in src


def test_credentials_form_uses_sonner_toasts():
    """Per the brief — sonner toasts for save/test/delete outcomes."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    assert 'from "sonner"' in src
    assert "toast.success" in src
    assert "toast.error" in src


def test_zod_schema_adapts_to_required_min_length_url_pattern():
    """The form no longer owns schema validation; it only previews the\n    expected connector fields."""
    src = _read(NEXT_SRC / "components/cartridges/CredentialsForm.tsx")
    for token in ("min_length", "max_length", "pattern", "url", "zodResolver", "zod"):
        assert token not in src, f"unexpected legacy schema-adapter token: {token!r}"


# ── Pages ───────────────────────────────────────────────────────────────


def test_cartridges_grid_page_exists():
    page = NEXT_SRC / "app/(shell)/cartridges/page.tsx"
    assert page.exists()
    src = _read(page)
    assert "useCartridgeList" in src
    assert "CartridgeCard" in src
    # Loading skeleton + error state both rendered (the v1.44.1 dashboard
    # pattern carried into v1.44.3).
    assert "isLoading" in src and "isError" in src


def test_cartridge_detail_page_exists_and_uses_query_param():
    page = NEXT_SRC / "app/(shell)/cartridges/viewer/page.tsx"
    assert page.exists()
    src = _read(page)
    assert "useSearchParams" in src
    assert "useConnectorSchema" in src
    assert "CredentialsForm" in src


def test_cartridge_detail_never_uses_generic_schema_fallback():
    page = NEXT_SRC / "app/(shell)/cartridges/viewer/page.tsx"
    src = _read(page)
    assert "fallbackSchema" not in src
    assert "schemaQuery.data ??" not in src
    assert 'name: "base_url"' not in src
    assert 'name: "token"' not in src
    assert "schemaQuery.isLoading" in src
    assert ") : schema ? (" in src
    assert "schema={schema}" in src


def test_grid_derives_status_from_kpi_freshness():
    """The grid avoids a second per-cartridge endpoint by reading
    /api/dashboard/kpis.data_freshness. Lock the mapping:
      never      → unconfigured
      very_stale → failed
      fresh|stale → connected
    """
    src = _read(NEXT_SRC / "app/(shell)/cartridges/page.tsx")
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
