from __future__ import annotations

import re
from pathlib import Path


def test_refinement_main_has_no_except_pass_blocks():
    source = (Path(__file__).resolve().parents[1] / "refinement/app/main.py").read_text()

    assert not re.search(r"except\s+Exception(?:\s+as\s+\w+)?:\n\s+pass\b", source)
