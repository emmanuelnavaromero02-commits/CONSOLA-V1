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


def test_make_highlight_emits_documented_eight_fields():
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
    src = _read(COPILOT)
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
    src = _read(PROACTIVE)
    severities = set(re.findall(r'severity=\s*"([^"]+)"', src))
    severities -= {""}
    allowed = {"info", "warning", "critical"}
    rogue = severities - allowed
    assert not rogue, (
        f"proactive_service.py emits unknown severities {rogue} — "
        f"BriefingCard only knows {allowed}. Either widen the "
        f"Severity union in console-next/src/lib/copilot/types.ts "
        f"or rename the backend literals."
    )


def test_dismiss_endpoint_path_is_briefing_id_dismiss():
    src = _read(COPILOT)
    assert '"/briefing/{highlight_id}/dismiss"' in src or \
           "'/briefing/{highlight_id}/dismiss'" in src, (
        "POST /briefing/{highlight_id}/dismiss path drift would "
        "break the Next.js dashboard dismiss flow"
    )


def test_ts_briefing_type_includes_all_eight_fields():
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
    src = _read(TS_CARD)
    assert "function classifyHref" in src, (
        "BriefingCard.tsx must keep the classifyHref allow-list"
    )
    assert 'data-action-dropped' in src
    assert "console.warn" in src, (
        "Unsafe action_href drops must be observable via "
        "console.warn so QA + monitoring can catch backend "
        "bugs that emit bad URLs."
    )
