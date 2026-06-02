"""File-ingestion helpers for the Teams channel.

v0.1 supports **only text-shaped attachments** (``.txt`` / ``.md``,
content-type ``text/*``). Binary formats — PDF, DOCX, XLSX, images — are
intentionally NOT decoded here: each one needs a dedicated parser
(``pypdf``, ``python-docx``, ``openpyxl``…) plus its own safety review
(zip-bomb defence, embedded-macro stripping, OCR cost, etc.) and so is
deferred to a follow-up PR. Submitting a non-text attachment today
results in a clean "unsupported_type" audit, never a parse failure.
"""
from __future__ import annotations

from typing import Any

_SUPPORTED_TEXT_EXTS = (".txt", ".md", ".markdown", ".log", ".csv")
# Per-attachment cap on rendered text we hand to the copilot. Independent
# of the raw-bytes download cap (``MSTEAMS_FILES_MAX_BYTES``): even a
# 5 MB text file would explode the LLM prompt past any sensible budget.
TEXT_BUDGET_CHARS = 8000
# Per-turn cap on TOTAL rendered text across all attachments. Five 8 KB
# excerpts can still add up.
TOTAL_BUDGET_CHARS = 20000


def is_supported_text(content_type: str | None, name: str | None) -> bool:
    """Whitelist: ``text/*`` content-type OR a known-safe extension. Both
    optional inputs are normalised; missing values fail closed (return False)."""
    ct = (content_type or "").lower().split(";", 1)[0].strip()
    if ct.startswith("text/"):
        return True
    if ct in {"application/json", "application/xml"}:
        return True
    n = (name or "").lower()
    return any(n.endswith(ext) for ext in _SUPPORTED_TEXT_EXTS)


def decode_text(content: bytes, content_type: str | None) -> str | None:
    """Decode bytes to text using the content-type charset (when declared)
    or utf-8 with replacement. Returns None on any decode catastrophe so the
    caller can audit ``decode_failed`` rather than 500.
    """
    if not isinstance(content, (bytes, bytearray)):
        return None
    charset = "utf-8"
    if content_type:
        for part in content_type.split(";"):
            part = part.strip().lower()
            if part.startswith("charset="):
                charset = part.split("=", 1)[1].strip() or "utf-8"
                break
    try:
        return bytes(content).decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        try:
            return bytes(content).decode("utf-8", errors="replace")
        except Exception:
            return None


def render_for_copilot(
    summaries: list[dict[str, Any]],
    *, total_budget: int = TOTAL_BUDGET_CHARS,
) -> str:
    """Render successfully-ingested attachments as a fenced footer the
    copilot can read alongside the user's prompt. Returns an empty string
    when no attachments survived ingestion.

    Output shape (Markdown — Teams' textFormat) lets the LLM tell apart
    the human prompt from each file. The cumulative budget is enforced
    *before* fences so a single oversize file can never silently truncate
    a following one mid-content.
    """
    parts: list[str] = []
    remaining = max(0, int(total_budget))
    for s in summaries:
        if remaining <= 0:
            break
        name = str(s.get("name") or "archivo")
        text = str(s.get("text") or "")
        if not text:
            continue
        snippet = text[:remaining]
        remaining -= len(snippet)
        parts.append(f"Archivo adjunto: {name}\n```\n{snippet}\n```")
    return "\n\n".join(parts)
