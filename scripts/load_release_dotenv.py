#!/usr/bin/env python3
"""Load a passive dotenv file without evaluating shell syntax."""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class DotenvError(RuntimeError):
    pass


def parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        if not separator or KEY.fullmatch(key) is None or key in values:
            raise DotenvError(f"invalid or duplicate dotenv assignment at line {number}")
        try:
            lexer = shlex.shlex(raw_value, posix=True)
            lexer.whitespace_split = True
            lexer.commenters = "#"
            tokens = list(lexer)
        except ValueError as exc:
            raise DotenvError(f"invalid dotenv quoting at line {number}") from exc
        value = " ".join(tokens)
        if "\x00" in value or "\n" in value or "\r" in value:
            raise DotenvError(f"invalid dotenv value at line {number}")
        values[key] = value
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        values = parse(args.input.read_text(encoding="utf-8"))
        with args.output.open("wb") as handle:
            for key, value in values.items():
                handle.write(key.encode("utf-8") + b"\0")
                handle.write(value.encode("utf-8") + b"\0")
    except (OSError, UnicodeError, DotenvError) as exc:
        print(f"RELEASE DOTENV BLOCKED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
