"""Static checks on the connection kit under ``cartridges/sap_b1/connect``.

The repository is public. Every file in the kit must be publishable: no
customer identifiers, no real-looking hosts or addresses, no secrets, SQL
that grants nothing beyond SELECT on the company schemas, and templates
that list exactly the variables the cartridge declares.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

CARTRIDGE = Path(__file__).resolve().parents[1]
KIT = CARTRIDGE / "connect"
CONFIG_PY = CARTRIDGE / "app" / "core" / "config.py"
VAULT_CLIENT_PY = CARTRIDGE / "app" / "core" / "vault_client.py"
ENTITIES_YAML = CARTRIDGE / "app" / "config" / "entities.yaml"
README = CARTRIDGE / "README.md"

EXPECTED_FILES = (
    "hana/00_find_tenant_sql_port.sql",
    "hana/01_create_readonly_user.sql",
    "hana/02_verify_readonly_user.sql",
    "hana/03_revoke_readonly_user.sql",
    "hana/test_connection.ps1",
    "hana/test_connection.sh",
    "config/env.sap_b1.template",
    "config/vault_connection.template.json",
    "config/schedule_entities_every_2h.sql",
    "config/initial_load_by_company_month.md",
    "vpn/README.md",
    "vpn/server/install_wireguard_host.sh",
    "vpn/server/open_security_group.sh",
    "vpn/client/wg-client.conf.template",
    "windows/README.md",
)

IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
HOSTNAME = re.compile(r"\b[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:com|mx|local)\b", re.IGNORECASE)
# Business One company schemas start with SBO_; only the <COMPANY_DB_n>
# placeholder may stand for one, never a real name.
SBO_SCHEMA = re.compile(r"SBO_[A-Za-z0-9]")
# A WireGuard key is 32 bytes in base64: 44 characters ending in '='.
WIREGUARD_KEY = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{42}=(?![A-Za-z0-9+/=])")
PLACEHOLDER = re.compile(r"<[^<>\n]+>")
PLACEHOLDER_FORMAT = re.compile(r"^<[A-Z][A-Z0-9_]*>$")


def _kit_files() -> list[Path]:
    return sorted(p for p in KIT.rglob("*") if p.is_file())


def _read(relative: str) -> str:
    return (KIT / relative).read_text(encoding="utf-8")


def _sql_statements(text: str) -> list[str]:
    """Uncommented SQL statements, whitespace-normalised, without the ';'."""
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"--[^\n]*", " ", text)
    statements = []
    for chunk in text.split(";"):
        normalised = " ".join(chunk.split())
        if normalised:
            statements.append(normalised)
    return statements


def _settings_sap_b1_vars() -> set[str]:
    """SAP_B1_* variables the cartridge Settings class declares."""
    tree = ast.parse(CONFIG_PY.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if item.target.id.startswith("sap_b1_"):
                        names.add(item.target.id.upper())
    assert names, "no sap_b1_* fields found on Settings"
    return names


def _field_aliases() -> dict[str, tuple[str, ...]]:
    tree = ast.parse(VAULT_CLIENT_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "_FIELD_ALIASES":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_FIELD_ALIASES" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("_FIELD_ALIASES not found in vault_client.py")


def _entity_names() -> set[str]:
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8"))
    return {e["entity"] for e in data["entities"]}


# ── Inventory ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("relative", EXPECTED_FILES)
def test_every_deliverable_exists_and_is_not_empty(relative):
    path = KIT / relative
    assert path.is_file(), f"missing {relative}"
    assert path.read_text(encoding="utf-8").strip(), f"{relative} is empty"


def test_no_stray_files_in_the_kit():
    """Anything else under connect/ is either a deliverable or lives in the
    windows-agent tree another engineer owns."""
    for path in _kit_files():
        relative = path.relative_to(KIT).as_posix()
        assert relative in EXPECTED_FILES or relative.startswith("windows-agent/"), relative


# ── Publishable content ─────────────────────────────────────────────────────


@pytest.mark.parametrize("path", _kit_files(), ids=lambda p: p.relative_to(KIT).as_posix())
def test_no_customer_identifiers_or_secrets(path):
    text = path.read_text(encoding="utf-8")
    assert not IPV4.search(text), f"{path.name}: IPv4 address {IPV4.search(text).group(0)!r}"
    assert not EMAIL.search(text), f"{path.name}: email address {EMAIL.search(text).group(0)!r}"
    assert not HOSTNAME.search(text), f"{path.name}: hostname {HOSTNAME.search(text).group(0)!r}"
    assert not SBO_SCHEMA.search(text), f"{path.name}: a Business One schema name; use <COMPANY_DB_n>"
    assert not WIREGUARD_KEY.search(text), f"{path.name}: a base64 value shaped like a WireGuard key"


@pytest.mark.parametrize(
    "relative",
    ("config/env.sap_b1.template", "config/vault_connection.template.json", "vpn/client/wg-client.conf.template"),
)
def test_template_placeholders_are_uppercase_tokens(relative):
    for token in PLACEHOLDER.findall(_read(relative)):
        assert PLACEHOLDER_FORMAT.match(token), f"{relative}: placeholder {token!r}"


def test_passwords_are_placeholders_everywhere():
    env = _read("config/env.sap_b1.template")
    match = re.search(r"^SAP_B1_PASSWORD=(.*)$", env, flags=re.MULTILINE)
    assert match and match.group(1).startswith("<") and match.group(1).endswith(">")
    vault = json.loads(_read("config/vault_connection.template.json"))
    assert vault["password"].startswith("<") and vault["password"].endswith(">")
    create = _read("hana/01_create_readonly_user.sql")
    assert re.search(r'PASSWORD "<[A-Z0-9_]+>"', create), "CREATE USER must carry a placeholder password"
    assert 'PrivateKey = <' in _read("vpn/client/wg-client.conf.template")


# ── HANA SQL: only the expected statement kinds ─────────────────────────────

FORBIDDEN_SQL = (
    re.compile(r"WITH GRANT OPTION", re.I),
    re.compile(r"WITH ADMIN OPTION", re.I),
    re.compile(r"SYSTEM PRIVILEGE", re.I),
    re.compile(r"\bROLE\b", re.I),
    re.compile(r"ALTER SYSTEM", re.I),
    re.compile(r"CATALOG READ", re.I),
    re.compile(r"DATA ADMIN", re.I),
    re.compile(r"USER ADMIN", re.I),
    re.compile(r"SBOCOMMON", re.I),  # the common schema is an opt-in comment, never a live grant
)
GRANT_SHAPE = re.compile(r'^GRANT SELECT ON SCHEMA "<COMPANY_DB_[123]>" TO <OMEGA_B1_READER>$')
REVOKE_SHAPE = re.compile(r'^REVOKE SELECT ON SCHEMA "<COMPANY_DB_[123]>" FROM <OMEGA_B1_READER>$')


@pytest.mark.parametrize(
    "relative",
    (
        "hana/00_find_tenant_sql_port.sql",
        "hana/01_create_readonly_user.sql",
        "hana/02_verify_readonly_user.sql",
        "hana/03_revoke_readonly_user.sql",
    ),
)
def test_hana_sql_never_grants_beyond_select_on_the_company_schemas(relative):
    statements = _sql_statements(_read(relative))
    assert statements, f"{relative}: no statements"
    for statement in statements:
        for pattern in FORBIDDEN_SQL:
            assert not pattern.search(statement), f"{relative}: {statement[:80]!r}"
        if statement.upper().startswith("GRANT"):
            assert GRANT_SHAPE.match(statement), f"{relative}: unexpected GRANT {statement!r}"
        if statement.upper().startswith("REVOKE"):
            assert REVOKE_SHAPE.match(statement), f"{relative}: unexpected REVOKE {statement!r}"
        for token in PLACEHOLDER.findall(statement):
            assert PLACEHOLDER_FORMAT.match(token), f"{relative}: placeholder {token!r}"


def test_find_tenant_port_only_selects_from_the_system_catalogue():
    statements = _sql_statements(_read("hana/00_find_tenant_sql_port.sql"))
    assert all(s.upper().startswith("SELECT") for s in statements)
    assert any("SYS_DATABASES.M_SERVICES" in s and "SERVICE_NAME = 'indexserver'" in s and "SQL_PORT" in s for s in statements)
    text = _read("hana/00_find_tenant_sql_port.sql")
    assert "3NN13" in text and "3NN15" in text and "SYSTEMDB" in text


def test_create_user_script_is_exactly_user_plus_three_schema_grants():
    statements = _sql_statements(_read("hana/01_create_readonly_user.sql"))
    assert statements[0] == 'CREATE USER <OMEGA_B1_READER> PASSWORD "<PASSWORD_GENERATED_BY_CUSTOMER_IT>" NO FORCE_FIRST_PASSWORD_CHANGE'
    assert statements[1] == "ALTER USER <OMEGA_B1_READER> DISABLE PASSWORD LIFETIME"
    grants = statements[2:]
    assert [GRANT_SHAPE.match(g) is not None for g in grants] == [True, True, True]
    assert sorted(re.search(r"COMPANY_DB_(\d)", g).group(1) for g in grants) == ["1", "2", "3"]
    assert len(statements) == 5


def test_verify_script_reads_privilege_views_and_smokes_cinf():
    statements = _sql_statements(_read("hana/02_verify_readonly_user.sql"))
    assert all(s.upper().startswith("SELECT") for s in statements)
    joined = "\n".join(statements)
    assert "SYS.GRANTED_PRIVILEGES" in joined
    assert "SYS.EFFECTIVE_PRIVILEGES" in joined and "SYSTEMPRIVILEGE" in joined
    assert 'SELECT "Version" FROM "<COMPANY_DB_1>"."CINF"' in statements


def test_revoke_script_revokes_then_drops():
    statements = _sql_statements(_read("hana/03_revoke_readonly_user.sql"))
    assert [REVOKE_SHAPE.match(s) is not None for s in statements[:3]] == [True, True, True]
    assert statements[3] == "DROP USER <OMEGA_B1_READER> CASCADE"
    assert len(statements) == 4


# ── Connectivity scripts ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "relative",
    ("hana/test_connection.sh", "vpn/server/install_wireguard_host.sh", "vpn/server/open_security_group.sh"),
)
def test_shell_scripts_parse_and_fail_closed(relative):
    path = KIT / relative
    text = path.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    assert path.stat().st_mode & 0o111, f"{relative} is not executable"
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("relative", ("hana/test_connection.sh", "hana/test_connection.ps1"))
def test_connectivity_checks_never_pass_the_password_on_the_command_line(relative):
    text = _read(relative)
    for line in text.splitlines():
        if "hdbsql" in line and not line.lstrip().startswith(("#", "Write-Host", "echo")):
            assert not re.search(r"(^|\s)-p(\s|$|\")", line), f"{relative}: {line.strip()!r}"
    assert "-u" in text and "-U" in text
    assert "CINF" in text and '"Version"' in text


def test_powershell_check_tests_the_port_before_the_session():
    text = _read("hana/test_connection.ps1")
    assert "Test-NetConnection" in text
    assert text.index("Test-NetConnection") < text.index("hdbsql")
    assert "SecureString" not in text or "-p" not in text  # no attempt to hand the password to hdbsql


# ── Environment and Vault templates ─────────────────────────────────────────


def test_env_template_lists_exactly_the_variables_the_cartridge_declares():
    declared = _settings_sap_b1_vars()
    listed = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", _read("config/env.sap_b1.template"), flags=re.MULTILINE))
    assert listed == declared, f"template != Settings: missing {declared - listed}, extra {listed - declared}"
    env = _read("config/env.sap_b1.template")
    assert re.search(r"^SAP_B1_DIALECT=hana$", env, flags=re.MULTILINE)
    assert re.search(r"^SAP_B1_ENCRYPT=true$", env, flags=re.MULTILINE)
    companies = re.search(r'^SAP_B1_COMPANIES="(.*)"$', env, flags=re.MULTILINE)
    assert companies
    pairs = dict(item.split("=") for item in companies.group(1).split(","))
    assert pairs == {"mx_mfg": "<COMPANY_DB_1>", "mx_dist_a": "<COMPANY_DB_2>", "mx_dist_b": "<COMPANY_DB_3>"}


def test_vault_template_uses_field_names_the_cartridge_accepts():
    payload = json.loads(_read("config/vault_connection.template.json"))
    aliases = _field_aliases()
    accepted = {alias for names in aliases.values() for alias in names}
    keys = {k for k in payload if not k.startswith("_")}
    assert keys <= accepted, f"unknown Vault fields: {keys - accepted}"
    for variable, names in aliases.items():
        assert keys & set(names), f"{variable}: none of {names} present in the Vault template"
    assert payload["dialect"] == "hana"
    assert payload["companies"] == "mx_mfg=<COMPANY_DB_1>,mx_dist_a=<COMPANY_DB_2>,mx_dist_b=<COMPANY_DB_3>"
    assert set(aliases) == _settings_sap_b1_vars() - {"SAP_B1_CONNECT_TIMEOUT_SECONDS"}


# ── Scheduling ──────────────────────────────────────────────────────────────


def test_schedule_sql_targets_entity_scheduler_columns_with_psql_variables():
    text = _read("config/schedule_entities_every_2h.sql")
    assert ":'tenant_id'" in text and ":'workspace_id'" in text
    assert "\\set ON_ERROR_STOP on" in text
    assert re.search(r"trigger_type\s*=\s*'scheduled'", text)
    assert re.search(r"tenant_id\s*=\s*:'tenant_id'::uuid", text)
    assert re.search(r"workspace_id\s*=\s*:'workspace_id'::uuid", text)
    assert "WHERE cartridge_id = 'sap_b1'" in text
    assert "'sap_b1_extract'" in text
    # The scheduler's contract, so a reader of the script knows what fires it.
    assert "entity_scheduler" in text and "last_scheduled_at" in text and "cron_expression" in text


def test_schedule_sql_is_every_two_hours_for_every_entity():
    text = _read("config/schedule_entities_every_2h.sql")
    statements = _sql_statements(text)
    update = next(s for s in statements if s.startswith("UPDATE entity_config"))
    crons = re.findall(r"'([^']*\*[^']*)'", update)
    assert crons, "no cron expressions in the UPDATE"
    assert all(re.fullmatch(r"\d{1,2} \*/2 \* \* \*", c) for c in crons), crons
    assert len(set(crons)) >= 2, "groups must be staggered inside the two-hour window"
    listed = set(re.findall(r"'([A-Z][A-Z0-9]{3})'", update))
    assert listed == _entity_names(), f"missing {_entity_names() - listed}, extra {listed - _entity_names()}"
    set_clause = update[: update.index("WHERE cartridge_id")]
    assert not re.search(r"\benabled\s*=", set_clause), "the script must not flip `enabled`"


def test_initial_load_runbook_matches_the_cartridge_contract():
    text = _read("config/initial_load_by_company_month.md")
    assert "airflow dags trigger sap_b1_extract" in text
    assert "from_date" in text and "to_date" in text
    for entity in ("OINV", "INV1", "OJDT", "JDT1", "OINM", "IBT1", "OWOR", "WOR1"):
        assert entity in text
    assert "entity_watermarks" in text, "the runbook must seed watermarks after a historical load"
    assert "b1_update_ts" in text
    assert "security_context" in text


# ── VPN kit ─────────────────────────────────────────────────────────────────


def test_client_template_limits_allowed_ips_to_our_tunnel_address():
    text = _read("vpn/client/wg-client.conf.template")
    assert re.search(r"^AllowedIPs = <WG_SERVER_TUNNEL_IP>/32$", text, flags=re.MULTILINE)
    assert re.search(r"^PersistentKeepalive = 25$", text, flags=re.MULTILINE)
    assert re.search(r"^Endpoint = <OMEGA_VPN_PUBLIC_IP>:<WG_LISTEN_PORT>$", text, flags=re.MULTILINE)
    assert "DNS =" not in text


def test_server_install_script_keeps_private_keys_private_and_scopes_the_peer():
    text = _read("vpn/server/install_wireguard_host.sh")
    assert "umask 077" in text
    assert 'wg genkey > "$KEY_FILE"' in text
    assert 'chmod 600 "$KEY_FILE"' in text and 'chmod 600 "$CONF_FILE"' in text
    assert "net.ipv4.ip_forward = 1" in text
    assert "MASQUERADE" in text
    assert 'AllowedIPs = ${CUSTOMER_PEER_TUNNEL_IP}/32' in text
    assert "--keys-only" in text
    assert 'systemctl enable "wg-quick@${WG_IFACE}"' in text
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # The private key may only be read into the config render, never printed.
        if 'cat "$KEY_FILE"' in stripped:
            assert stripped.startswith('echo "PrivateKey = $(cat "$KEY_FILE")"'), stripped
        if "wg pubkey" in stripped:
            assert 'wg pubkey < "$KEY_FILE" > "$PUB_FILE"' in stripped, stripped


def test_security_group_script_opens_the_udp_port_to_one_address_only():
    text = _read("vpn/server/open_security_group.sh")
    assert "authorize-security-group-ingress" in text
    assert "${CUSTOMER_PUBLIC_IP}/32" in text
    assert "IpProtocol=udp" in text
    assert "--revoke" in text


def test_vpn_readme_states_what_needs_the_customer_public_ip_first():
    text = _read("vpn/README.md")
    assert "<CUSTOMER_PUBLIC_IP>" in text and "<CUSTOMER_PEER_PUBLIC_KEY>" not in text or "clave pública" in text
    assert "open_security_group.sh" in text and "install_wireguard_host.sh" in text
    assert "--keys-only" in text
    assert "portproxy" in text


def test_windows_readme_only_describes_and_points_to_the_agent_tree():
    text = _read("windows/README.md")
    assert "windows-agent" in text
    assert not any(p.suffix in {".py", ".ps1", ".exe", ".msi"} for p in (KIT / "windows").rglob("*"))


def test_cartridge_readme_links_the_kit():
    text = README.read_text(encoding="utf-8")
    assert "## Connection kit" in text
    assert "connect/" in text
