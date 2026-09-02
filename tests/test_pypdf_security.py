from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version

import pytest


CHILD = r"""
import sys
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import ContentStream
from pypdf.generic._image_inline import (
    extract_inline__ascii85_decode,
    extract_inline__ascii_hex_decode,
)

case = sys.argv[1]
if case == "valid":
    reader = PdfReader(BytesIO(sys.stdin.buffer.read()))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if text.strip() != "OMEGA PDF":
        raise SystemExit("unexpected extracted text")
    print("extracted")
    raise SystemExit(0)

try:
    if case == "normal":
        stream = BytesIO(b"\n/IM true\n/W001")
        ContentStream(stream=None, pdf=None)._read_inline_image(stream)
    elif case == "ascii85":
        extract_inline__ascii85_decode(
            BytesIO(b"Gar8O(o6*i%*56~\ne  L\ne  L9/ LL9/ L")
        )
    elif case == "asciihex":
        extract_inline__ascii_hex_decode(BytesIO(b"ABCDE\nF G"))
    else:
        raise SystemExit("unknown case")
except PdfReadError:
    print("controlled-error")
    raise SystemExit(0)
raise SystemExit("malformed inline image unexpectedly succeeded")
"""


def _pdf_with_stream(stream: bytes) -> bytes:
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


def _run(case: str, payload: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-I", "-c", CHILD, case],
        input=payload,
        capture_output=True,
        check=False,
        timeout=3,
    )


def test_runtime_uses_pypdf_6_16_1() -> None:
    assert version("pypdf") == "6.16.1"


def test_pdfreader_extract_text_finishes_for_valid_pdf() -> None:
    payload = _pdf_with_stream(b"BT /F1 12 Tf 72 720 Td (OMEGA PDF) Tj ET")

    result = _run("valid", payload)

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.strip() == b"extracted"


@pytest.mark.parametrize("case", ["normal", "ascii85", "asciihex"])
def test_incomplete_inline_image_finishes_with_controlled_error(case: str) -> None:
    result = _run(case)

    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.strip() == b"controlled-error"
