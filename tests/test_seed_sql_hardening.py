"""P6 — seed.sql validation must fail closed on dollar-quotes and block comments.

The cartridge-import path validates an uploaded seed.sql with a hand-written
statement splitter (``_split_sql_statements``) plus a keyword blacklist and a
table whitelist. A ``;`` hidden inside a PostgreSQL dollar-quoted string
(``$$...$$`` / ``$tag$...$tag$``) or a ``/* */`` block comment could desync the
naive splitter and let a non-whitelisted statement slip past validation.

Hardening:
  * the splitter is now dollar-quote aware, so a ``;`` (or ``'``) inside a
    dollar-quoted literal stays part of a single statement instead of being
    treated as a boundary;
  * ``/* */`` block comments are rejected outright (no legitimate seed uses
    them).

Real cartridge seeds must still validate: Replicon uses ``$$`` for multi-line
agent instructions, and the SAP seeds are plain INSERTs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES = ("replicon", "sap_hcm", "sap_s4hana", "sap_successfactors")


@pytest.fixture()
def cartridge_service():
    # Isolate sys.path so ``app`` resolves to console/app and not a cartridge
    # ``app`` package a previous test may have imported, then import fresh.
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    siblings = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in siblings)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    import importlib

    return importlib.import_module("app.services.cartridge_service")


# ── block comments ───────────────────────────────────────────────────────────

def test_block_comment_is_rejected(cartridge_service):
    sql = (
        "INSERT INTO cartridges (id, name) VALUES ('x', 'n');\n"
        "/* a benign ; block comment */\n"
    )
    with pytest.raises(ValueError, match="block comments"):
        cartridge_service._validate_seed_sql(sql)


def test_block_comment_close_token_alone_is_rejected(cartridge_service):
    with pytest.raises(ValueError, match="block comments"):
        cartridge_service._validate_seed_sql("INSERT INTO cartridges (id) VALUES ('x'); */")


# ── dollar-quote: malicious DO block still blocked by the blacklist ──────────

def test_do_dollar_quote_block_is_rejected(cartridge_service):
    sql = (
        "INSERT INTO cartridges (id, name) VALUES ('x', 'n');\n"
        "DO $$ BEGIN DELETE FROM users; END $$;\n"
    )
    with pytest.raises(ValueError):
        cartridge_service._validate_seed_sql(sql)


# ── dollar-quote: ';' inside the literal is data, not a statement boundary ────

def test_semicolon_inside_dollar_quote_is_a_single_statement(cartridge_service):
    sql = "INSERT INTO cartridges (id, name) VALUES ('x', $body$a ; b ; c$body$);"
    assert len(cartridge_service._split_sql_statements(sql)) == 1
    cartridge_service._validate_seed_sql(sql)  # must not raise


def test_tagged_dollar_quote_with_semicolon_validates(cartridge_service):
    # The prompt's $tag$ example: a ';' inside $body$ must not split the INSERT.
    sql = "INSERT INTO cartridges (id, name) VALUES ('x', $body$nombre con ; punto y coma$body$);"
    assert len(cartridge_service._split_sql_statements(sql)) == 1
    cartridge_service._validate_seed_sql(sql)  # must not raise


def test_quote_and_semicolon_inside_dollar_quote_stay_literal(cartridge_service):
    # A naive splitter would toggle its single-quote state on the apostrophe in
    # "it's" and then mis-handle the ';'. The dollar-quote-aware splitter does not.
    sql = "INSERT INTO agents (slug, instructions) VALUES ('a', $$it's a ; trap$$);"
    assert len(cartridge_service._split_sql_statements(sql)) == 1
    cartridge_service._validate_seed_sql(sql)  # must not raise


# ── line comments stay allowed ───────────────────────────────────────────────

def test_line_comments_still_allowed(cartridge_service):
    sql = (
        "-- this is a line comment\n"
        "INSERT INTO cartridges (id, name) VALUES ('x', 'n');\n"
    )
    cartridge_service._validate_seed_sql(sql)  # must not raise


# ── real seeds keep validating ───────────────────────────────────────────────

@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_real_cartridge_seed_still_validates(cartridge_service, cartridge):
    seed = (REPO_ROOT / "cartridges" / cartridge / "config" / "seed.sql").read_text(encoding="utf-8")
    cartridge_service._validate_seed_sql(seed)  # must not raise
