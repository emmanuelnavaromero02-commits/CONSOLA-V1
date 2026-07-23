from __future__ import annotations

import logging
import sys
from io import BytesIO

from pypdf import PdfReader


LIMIT_EXIT = 4
EMPTY_EXIT = 3
INVALID_EXIT = 2


def _set_resource_limits(memory_bytes: int, cpu_seconds: int) -> None:
    try:
        import resource
    except ImportError:
        return
    for resource_name in ("RLIMIT_AS", "RLIMIT_DATA"):
        resource_id = getattr(resource, resource_name, None)
        if resource_id is None:
            continue
        try:
            resource.setrlimit(resource_id, (memory_bytes, memory_bytes))
        except (OSError, ValueError):
            continue
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    except (OSError, ValueError):
        pass


def _arguments() -> tuple[int, ...]:
    if len(sys.argv) != 8:
        raise ValueError("invalid worker arguments")
    return tuple(int(value) for value in sys.argv[1:])


def _extract(
    raw: bytes,
    max_pdf_bytes: int,
    max_pages: int,
    max_text_chars: int,
    max_page_content_bytes: int,
    max_total_content_bytes: int,
) -> str:
    if len(raw) > max_pdf_bytes:
        raise MemoryError
    reader = PdfReader(BytesIO(raw))
    if len(reader.pages) > max_pages:
        raise MemoryError

    pages: list[str] = []
    total_content_bytes = 0
    total_text_chars = 0
    for page in reader.pages:
        contents = page.get_contents()
        content_bytes = len(contents.get_data()) if contents is not None else 0
        total_content_bytes += content_bytes
        if (
            content_bytes > max_page_content_bytes
            or total_content_bytes > max_total_content_bytes
        ):
            raise MemoryError
        page_text = page.extract_text() or ""
        total_text_chars += len(page_text)
        if total_text_chars > max_text_chars:
            raise MemoryError
        pages.append(page_text)
    return "\n\n".join(pages).strip()


def main() -> int:
    logging.disable(logging.CRITICAL)
    try:
        (
            max_pdf_bytes,
            max_pages,
            max_text_chars,
            max_page_content_bytes,
            max_total_content_bytes,
            memory_bytes,
            cpu_seconds,
        ) = _arguments()
        _set_resource_limits(memory_bytes, cpu_seconds)
        raw = sys.stdin.buffer.read(max_pdf_bytes + 1)
        content = _extract(
            raw,
            max_pdf_bytes,
            max_pages,
            max_text_chars,
            max_page_content_bytes,
            max_total_content_bytes,
        )
    except MemoryError:
        return LIMIT_EXIT
    except Exception:
        return INVALID_EXIT
    if not content:
        return EMPTY_EXIT
    try:
        sys.stdout.buffer.write(content.encode("utf-8"))
    except (BrokenPipeError, UnicodeEncodeError):
        return INVALID_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
