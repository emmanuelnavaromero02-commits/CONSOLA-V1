from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CARTRIDGES = (
    "replicon",
    "hubspot",
    "salesforce",
    "sap_hcm",
    "sap_s4hana",
    "sap_successfactors",
)


@pytest.fixture()
def cartridge_service():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    siblings = ("/cartridges/", "/console", "/refinement", "/vault", "/workspace", "/mcp-infra")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in siblings)]
    sys.path.insert(0, str(REPO_ROOT / "console"))
    import importlib

    return importlib.import_module("app.services.cartridge_service")


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


def test_do_dollar_quote_block_is_rejected(cartridge_service):
    sql = (
        "INSERT INTO cartridges (id, name) VALUES ('x', 'n');\n"
        "DO $$ BEGIN DELETE FROM users; END $$;\n"
    )
    with pytest.raises(ValueError):
        cartridge_service._validate_seed_sql(sql)


def test_semicolon_inside_dollar_quote_is_a_single_statement(cartridge_service):
    sql = "INSERT INTO cartridges (id, name) VALUES ('x', $body$a ; b ; c$body$);"
    assert len(cartridge_service._split_sql_statements(sql)) == 1
    cartridge_service._validate_seed_sql(sql)


def test_tagged_dollar_quote_with_semicolon_validates(cartridge_service):
    sql = "INSERT INTO cartridges (id, name) VALUES ('x', $body$nombre con ; punto y coma$body$);"
    assert len(cartridge_service._split_sql_statements(sql)) == 1
    cartridge_service._validate_seed_sql(sql)


def test_quote_and_semicolon_inside_dollar_quote_stay_literal(cartridge_service):
    sql = (
        "INSERT INTO cartridges (id, name) VALUES ('x', 'X');"
        "INSERT INTO agents (cartridge_id, slug, instructions) "
        "VALUES ('x', 'a', $$it's a ; trap$$);"
    )
    assert len(cartridge_service._split_sql_statements(sql)) == 2
    cartridge_service._validate_seed_sql(sql)


def test_line_comments_still_allowed(cartridge_service):
    sql = (
        "-- this is a line comment\n"
        "INSERT INTO cartridges (id, name) VALUES ('x', 'n');\n"
    )
    cartridge_service._validate_seed_sql(sql)


@pytest.mark.parametrize("cartridge", CARTRIDGES)
def test_real_cartridge_seed_still_validates(cartridge_service, cartridge):
    seed = (REPO_ROOT / "cartridges" / cartridge / "config" / "seed.sql").read_text(encoding="utf-8")
    cartridge_service._validate_seed_sql(seed)
