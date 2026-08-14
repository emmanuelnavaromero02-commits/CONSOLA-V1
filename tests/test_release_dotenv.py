from __future__ import annotations

import pytest

from scripts.load_release_dotenv import DotenvError, parse


def test_release_dotenv_is_data_not_shell_code() -> None:
    assert parse('NAME="System Administrator"\nHARMLESS=1 exit 0\n') == {
        "NAME": "System Administrator",
        "HARMLESS": "1 exit 0",
    }


@pytest.mark.parametrize(
    "text",
    ("exit 0\n", "A=1\nA=2\n", 'A="unterminated\n'),
)
def test_release_dotenv_rejects_non_assignments_duplicates_and_bad_quotes(
    text: str,
) -> None:
    with pytest.raises(DotenvError):
        parse(text)
