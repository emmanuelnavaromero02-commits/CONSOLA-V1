"""Sprint v1.44.4 Task B — pin the briefing shape contract.

The Next.js dashboard's BriefingCard renders backend-supplied
highlights and the round-1 audit caught significant shape drift
on Task A (Memory + Drafts) before the PR shipped. This file
pins the briefing shape STATICALLY so a future backend refactor
can't silently break the frontend renderer.

The contract is sourced directly from
``console/app/services/proactive_service.py:_make_highlight``,
which every analyzer (freshness / volume / pending / anomaly)
funnels through.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PROACTIVE = REPO / "console/app/services/proactive_service.py"
COPILOT   = REPO / "console/app/routers/copilot.py"
TS_TYPES  = REPO / "console-next/src/lib/copilot/types.ts"
TS_CARD   = REPO / "console-next/src/components/dashboard/BriefingCard.tsx"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── Backend invariants ────────────────────────────────────────


def test_make_highlight_emits_documented_eight_fields():
    """Every analyzer goes through ``_make_highlight`` — pin the
    eight-field shape so a refactor can't drop one and break
    BriefingCard's renderer."""
    src = _read(PROACTIVE)
    m = re.search(
        r"def _make_highlight\([\s\S]*?return\s*\{([\s\S]*?)\}",
        src,
    )
    assert m, "could not locate _make_highlight in proactive_service.py"
    body = m.group(1)

    for required_key in (
        '"id"',
        '"severity"',
        '"title"',
        '"body"',
        '"category"',
        '"cartridge"',
        '"action_label"',
        '"action_href"',
    ):
        assert required_key in body, (
            f"_make_highlight() must emit {required_key} — "
            f"BriefingCard reads this field. Got body:\n{body}"
        )


def test_briefing_endpoint_returns_highlights_envelope():
    """The router wraps the list as ``{highlights: [...]}``.
    Frontend's BriefingResponse type expects that exact key."""
    src = _read(COPILOT)
    # Locate the body of get_briefing and verify the wrapper.
    m = re.search(
        r'@router\.get\("/briefing"\)[\s\S]*?return\s+\{[^}]*\}',
        src,
    )
    assert m, "could not locate the GET /briefing handler"
    handler_body = m.group(0)
    assert '"highlights"' in handler_body, (
        "GET /briefing must wrap the list as ``{highlights: ...}``"
    )


def test_severity_values_match_frontend_enum():
    """The frontend Severity union is ``info | warning |
    critical``. Pin the analyzer literals so a renamed
    constant (e.g. "warn") doesn't silently fall back to
    SEVERITY_FALLBACK in BriefingCard."""
    src = _read(PROACTIVE)
    # Find every ``severity=`` literal in calls to _make_highlight.
    severities = set(re.findall(r'severity=\s*"([^"]+)"', src))
    # Ignore the parameter declarations.
    severities -= {""}
    # The analyzer is allowed to emit a subset; assert it never
    # emits a value the frontend can't render.
    allowed = {"info", "warning", "critical"}
    rogue = severities - allowed
    assert not rogue, (
        f"proactive_service.py emits unknown severities {rogue} — "
        f"BriefingCard only knows {allowed}. Either widen the "
        f"Severity union in console-next/src/lib/copilot/types.ts "
        f"or rename the backend literals."
    )


def test_dismiss_endpoint_path_is_briefing_id_dismiss():
    """Frontend posts to
    ``/api/copilot/briefing/{id}/dismiss``. Pin the path so a
    refactor (renaming to e.g. ``/highlights/...``) breaks
    loudly in CI rather than silently in the UI."""
    src = _read(COPILOT)
    assert '"/briefing/{highlight_id}/dismiss"' in src or \
           "'/briefing/{highlight_id}/dismiss'" in src, (
        "POST /briefing/{highlight_id}/dismiss path drift would "
        "break the Next.js dashboard dismiss flow"
    )


# ── Frontend shape pins ───────────────────────────────────────


def test_ts_briefing_type_includes_all_eight_fields():
    """The Next.js BriefingHighlight TypeScript type must keep
    parity with the backend ``_make_highlight`` shape — if a
    field disappears here, the card silently stops rendering it
    even though the backend still emits it."""
    src = _read(TS_TYPES)
    block = re.search(
        r"export interface BriefingHighlight\s*\{([\s\S]*?)\n\}",
        src,
    )
    assert block, "BriefingHighlight type not found in types.ts"
    body = block.group(1)
    for field in (
        "id", "severity", "title", "body",
        "category", "cartridge", "action_label", "action_href",
    ):
        assert re.search(rf"\b{field}\s*[?:]", body), (
            f"BriefingHighlight TypeScript type missing field {field!r}"
        )


def test_briefing_card_uses_safe_href_classifier():
    """Round-1 security audit: BriefingCard MUST gate
    action_href through a classifier that rejects unsafe
    schemes (javascript:, data:, etc). Pin the classifier name
    + the rejected-schemes branch so a future refactor can't
    silently drop the safety check."""
    src = _read(TS_CARD)
    assert "function classifyHref" in src, (
        "BriefingCard.tsx must keep the classifyHref allow-list"
    )
    # The classifier returns null for anything that doesn't fit
    # the http / https / same-origin allow-list. That null then
    # surfaces as data-action-dropped + a console.warn.
    assert 'data-action-dropped' in src
    assert "console.warn" in src, (
        "Unsafe action_href drops must be observable via "
        "console.warn so QA + monitoring can catch backend "
        "bugs that emit bad URLs."
    )
