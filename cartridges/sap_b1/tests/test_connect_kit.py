"""Static checks on the connection kit under ``cartridges/sap_b1/connect``.

The repository is public. Every file in the kit must be publishable: no
customer identifiers, no real-looking hosts or addresses, no secrets, SQL
that grants nothing beyond SELECT on the company schemas, and templates
that list exactly the variables the cartridge declares.
"""
from __future__ import annotations

import ast
import functools
import importlib
import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

CARTRIDGE = Path(__file__).resolve().parents[1]
REPO_ROOT = CARTRIDGE.parents[1]
KIT = CARTRIDGE / "connect"
CONFIG_PY = CARTRIDGE / "app" / "core" / "config.py"
VAULT_CLIENT_PY = CARTRIDGE / "app" / "core" / "vault_client.py"
ENTITIES_YAML = CARTRIDGE / "app" / "config" / "entities.yaml"
README = CARTRIDGE / "README.md"
COMPOSE = REPO_ROOT / "infra" / "docker-compose.yml"
RUNBOOK = "config/initial_load_by_company_month.md"

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
# `-p` as a standalone hdbsql flag (the password on the command line); not
# `-port`, `--dport`, `-Prompt` or `-p:` inside a comment.
PASSWORD_FLAG = re.compile(r'(?<![\w-])-p(?=$|[\s")])')


def _git(*args: str) -> str | None:
    """stdout of a git command run at the repository root, None without git."""
    try:
        result = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout if result.returncode == 0 else None


@functools.lru_cache(maxsize=None)
def _git_index() -> dict[str, str] | None:
    """Tracked kit files as {path relative to connect/: git mode}, or None
    when git is unavailable (then the checkout is scanned instead)."""
    kit = KIT.relative_to(REPO_ROOT).as_posix()
    out = _git("ls-files", "-s", "-z", "--", kit)
    if out is None:
        return None
    index: dict[str, str] = {}
    for record in out.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        index[path[len(kit) + 1 :]] = meta.split()[0]
    return index


def _is_utf8_text(path: Path) -> bool:
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _kit_files() -> list[Path]:
    """The kit as git knows it, so a stray .DS_Store, swap file or byte-code
    in a checkout never enters the secret scan."""
    index = _git_index()
    if index is not None:
        return sorted(KIT / relative for relative in index if (KIT / relative).is_file())
    return sorted(p for p in KIT.rglob("*") if p.is_file() and "__pycache__" not in p.parts and _is_utf8_text(p))


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


def _entities() -> list[dict]:
    data = yaml.safe_load(ENTITIES_YAML.read_text(encoding="utf-8"))
    return list(data["entities"])


def _entity_names() -> set[str]:
    return {e["entity"] for e in _entities()}


def _env_template_vars() -> set[str]:
    return set(re.findall(r"^([A-Z][A-Z0-9_]*)=", _read("config/env.sap_b1.template"), flags=re.MULTILINE))


def _block(text: str, start: str, end: str) -> str:
    """The lines from the first one containing ``start`` up to and including
    the next one containing ``end``."""
    lines = text.splitlines()
    first = next(i for i, line in enumerate(lines) if start in line)
    last = next(i for i, line in enumerate(lines) if i > first and end in line)
    return "\n".join(lines[first : last + 1])


def _strip_trailing_comment(line: str) -> str:
    return re.split(r"\s+#", line, maxsplit=1)[0]


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


def test_kit_inventory_comes_from_git_when_available():
    index = _git_index()
    if index is None:
        pytest.skip("git unavailable: the checkout was scanned instead")
    assert set(EXPECTED_FILES) <= set(index), f"untracked deliverables: {set(EXPECTED_FILES) - set(index)}"
    assert all(mode in {"100644", "100755"} for mode in index.values()), index


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
    index = _git_index()
    if index is not None:
        # The exec bit as committed, not as this checkout happens to carry it.
        assert index.get(relative) == "100755", f"{relative}: git mode {index.get(relative)}, expected 100755"
    else:
        assert path.stat().st_mode & 0o111, f"{relative} is not executable"
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def _hdbsql_argument_block(relative: str) -> str:
    """The whole block that builds and runs the hdbsql argument list, not
    just the lines that mention hdbsql."""
    text = _read(relative)
    if relative.endswith(".sh"):
        return _block(text, "if command -v hdbsql", 'hdbsql "${args[@]}"')
    return _block(text, "$args = @(", "& $hdbsql @args")


@pytest.mark.parametrize("relative", ("hana/test_connection.sh", "hana/test_connection.ps1"))
def test_connectivity_checks_never_pass_the_password_on_the_command_line(relative):
    block = _hdbsql_argument_block(relative)
    for line in block.splitlines():
        code = _strip_trailing_comment(line).strip()
        if not code or code.startswith(("#", "Write-Host", "echo")):
            continue
        assert not PASSWORD_FLAG.search(code), f"{relative}: {line.strip()!r}"
    assert re.search(r"(?<![\w-])-u(?![\w-])", block), "the user flag must be in the argument block"
    assert re.search(r"(?<![\w-])-U(?![\w-])", block), "the hdbuserstore key flag must be in the argument block"
    text = _read(relative)
    assert "CINF" in text and '"Version"' in text


def test_powershell_check_tests_the_port_before_the_session():
    text = _read("hana/test_connection.ps1")
    assert "Test-NetConnection" in text
    assert text.index("Test-NetConnection") < text.index("hdbsql")
    # hdbsql asks for the password itself; the script never reads one.
    assert "Read-Host" in text and "-AsSecureString" not in text
    assert not re.search(r"Read-Host[^\n]*(?i:contrase|password)", text)


def test_linux_check_validates_host_and_port_before_using_them():
    text = _read("hana/test_connection.sh")
    host_check = '[[ ! "$SAP_B1_HOST" =~ ^[A-Za-z0-9.-]{1,253}$ ]]'
    port_check = '[[ ! "$SAP_B1_PORT" =~ ^[0-9]{1,5}$ ]]'
    assert host_check in text and port_check in text
    assert text.index(host_check) < text.index("/dev/tcp/")
    assert text.index(port_check) < text.index("/dev/tcp/")
    # No child shell receives the values as code: the connect runs in a
    # subshell with both variables quoted.
    assert "bash -c" not in text
    assert 'exec 3<>"/dev/tcp/${SAP_B1_HOST}/${SAP_B1_PORT}"' in text
    assert re.search(r"^\s*\( exec 3<>\"/dev/tcp/\$\{SAP_B1_HOST\}/\$\{SAP_B1_PORT\}\" \)", text, flags=re.MULTILINE)


# ── Environment and Vault templates ─────────────────────────────────────────


def test_env_template_lists_exactly_the_variables_the_cartridge_declares():
    declared = _settings_sap_b1_vars()
    listed = _env_template_vars()
    assert listed == declared, f"template != Settings: missing {declared - listed}, extra {listed - declared}"
    env = _read("config/env.sap_b1.template")
    assert re.search(r"^SAP_B1_DIALECT=hana$", env, flags=re.MULTILINE)
    assert re.search(r"^SAP_B1_ENCRYPT=true$", env, flags=re.MULTILINE)
    companies = re.search(r'^SAP_B1_COMPANIES="(.*)"$', env, flags=re.MULTILINE)
    assert companies
    pairs = dict(item.split("=") for item in companies.group(1).split(","))
    assert pairs == {"mx_mfg": "<COMPANY_DB_1>", "mx_dist_a": "<COMPANY_DB_2>", "mx_dist_b": "<COMPANY_DB_3>"}


def test_compose_forwards_every_variable_the_env_template_names():
    """Every SAP_B1_* the operator is told to set reaches the container."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = compose["services"]["sap-b1"]["environment"]
    assert isinstance(environment, dict), "the sap-b1 environment block must be a mapping"
    forwarded = {k for k in environment if k.startswith("SAP_B1_")}
    template = _env_template_vars()
    assert template <= forwarded, f"sap-b1 service does not forward {sorted(template - forwarded)}"
    for name in template:
        assert f"${{{name}" in str(environment[name]), f"{name} is not read from the host environment"
    assert "NOTE" not in _read("config/env.sap_b1.template")


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


def _runbook_list(text: str, name: str) -> list[str]:
    match = re.search(rf'^{name}="([^"]*)"$', text, flags=re.MULTILINE)
    assert match, f"{name}=... not found in the runbook script"
    return match.group(1).split()


def test_initial_load_runbook_matches_the_cartridge_contract():
    text = _read(RUNBOOK)
    assert "airflow dags trigger sap_b1_extract" in text
    assert "from_date" in text and "to_date" in text
    assert "entity_watermarks" in text, "the runbook must seed watermarks after a historical load"
    assert "b1_update_ts" in text
    assert "security_context" in text

    entities = _entities()
    masters = _runbook_list(text, "MASTERS")
    dated = _runbook_list(text, "DATED")
    snapshots = _runbook_list(text, "SNAPSHOTS_LAST")
    listed = masters + dated + snapshots
    assert len(listed) == len(set(listed)), "an entity is listed twice"
    assert set(listed) == {e["entity"] for e in entities}, (
        f"missing {sorted({e['entity'] for e in entities} - set(listed))}, extra {sorted(set(listed) - {e['entity'] for e in entities})}"
    )
    # Dated entities are exactly the ones a historical (from/to) load can read.
    with_date = {e["entity"] for e in entities if e.get("date_field")}
    assert set(dated) == with_date, f"DATED != date_field entities: {set(dated) ^ with_date}"
    assert all(e.get("date_field") is None for e in entities if e["entity"] in set(masters + snapshots))
    # Headers before their lines, so the join a line table needs has data.
    for entity in entities:
        if entity.get("parent") and entity["entity"] in dated:
            assert dated.index(entity["parent"]) < dated.index(entity["entity"]), entity["entity"]
    # The prose ("Orden de entidades") names every entity, and the count it quotes is the real one.
    order = text[text.index("## Orden de entidades") : text.index("## Ventana de baja carga")]
    for name in listed:
        assert re.search(rf"\b{name}\b", order), f"{name} missing from the entity order"
    assert f"{len(dated)} entidades" in text, f"the runbook must quote {len(dated)} dated entities"


CODE_REFERENCE = re.compile(r"`([a-z_][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`")
FILE_SUFFIXES = {"py", "yaml", "yml", "md", "sql", "json", "toml", "sh", "ps1", "conf", "done", "txt", "template"}


def test_runbook_code_references_resolve():
    """Every `module.symbol` the runbook cites exists in the cartridge, so a
    rename (extraction_service._effective_mode became b1_reader.effective_mode)
    cannot leave a stale pointer behind."""
    text = _read(RUNBOOK)
    assert "_effective_mode" not in text
    references = [(m, s) for m, s in CODE_REFERENCE.findall(text) if s not in FILE_SUFFIXES]
    assert references, "no module.symbol references found; the regex or the runbook changed"
    for module_name, symbol in references:
        candidates = sorted(CARTRIDGE.glob(f"app/**/{module_name}.py"))
        assert len(candidates) == 1, f"`{module_name}.{symbol}`: module not found once under app/: {candidates}"
        dotted = ".".join(candidates[0].relative_to(CARTRIDGE).with_suffix("").parts)
        module = importlib.import_module(dotted)
        assert hasattr(module, symbol), f"`{module_name}.{symbol}`: {dotted} has no attribute {symbol}"


# ── VPN kit ─────────────────────────────────────────────────────────────────


def test_client_template_limits_allowed_ips_to_our_tunnel_address():
    text = _read("vpn/client/wg-client.conf.template")
    assert re.search(r"^AllowedIPs = <WG_SERVER_TUNNEL_IP>/32$", text, flags=re.MULTILINE)
    assert re.search(r"^PersistentKeepalive = 25$", text, flags=re.MULTILINE)
    assert re.search(r"^Endpoint = <OMEGA_VPN_PUBLIC_IP>:<WG_LISTEN_PORT>$", text, flags=re.MULTILINE)
    assert "DNS =" not in text


def _rendered_config_lines(text: str) -> tuple[list[str], int, int]:
    """The script's lines plus the indexes of the `{` ... `} > "$RENDERED"`
    group that renders the WireGuard config."""
    lines = text.splitlines()
    opened = lines.index("{")
    closed = lines.index('} > "$RENDERED"')
    assert opened < closed
    return lines, opened, closed


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
    lines, opened, closed = _rendered_config_lines(text)
    key_reads = [i for i, line in enumerate(lines) if 'cat "$KEY_FILE"' in line and not line.strip().startswith("#")]
    # The private key is read exactly once, into the config render and
    # nowhere else: that line must sit inside the `{ ... } > "$RENDERED"` group.
    assert len(key_reads) == 1, key_reads
    assert lines[key_reads[0]].strip() == 'echo "PrivateKey = $(cat "$KEY_FILE")"'
    assert opened < key_reads[0] < closed
    for line in lines:
        stripped = line.strip()
        if "wg pubkey" in stripped and not stripped.startswith("#"):
            assert 'wg pubkey < "$KEY_FILE" > "$PUB_FILE"' in stripped, stripped


def _post_rules(text: str, hook: str) -> list[str]:
    lines, opened, closed = _rendered_config_lines(text)
    rules = []
    for line in lines[opened : closed + 1]:
        match = re.match(rf'\s*echo "{hook} = (.*)"$', line)
        if match:
            rules.append(match.group(1))
    return rules


def test_server_install_script_drops_anything_the_customer_initiates():
    """The tunnel is one-way: replies to what we open come back, a NEW
    connection from the peer never reaches the host or the VPC, whatever the
    chain policy (Docker's DROP, or ACCEPT on a host without Docker)."""
    text = _read("vpn/server/install_wireguard_host.sh")
    ups = _post_rules(text, "PostUp")
    downs = _post_rules(text, "PostDown")
    established = "-i %i -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
    for chain in ("FORWARD", "INPUT"):
        inserts = {}
        for rule in ups:
            match = re.match(rf"iptables -I {chain} (\d+) (.*)$", rule)
            if match:
                inserts[match.group(2)] = int(match.group(1))
        assert established in inserts, f"{chain}: no RELATED,ESTABLISHED accept on the tunnel interface"
        assert "-i %i -j DROP" in inserts, f"{chain}: nothing drops NEW connections from the tunnel"
        assert inserts[established] < inserts["-i %i -j DROP"], f"{chain}: the DROP must follow the ESTABLISHED accept"
        # Anything accepted from the tunnel is explicit and above the DROP.
        for spec, position in inserts.items():
            if spec.startswith("-i %i") and spec != "-i %i -j DROP":
                assert position < inserts["-i %i -j DROP"], spec
        assert sorted(inserts.values()) == list(range(1, len(inserts) + 1)), f"{chain}: positions must be contiguous"
    # Into the tunnel only TCP on the HANA port and ICMP echo, to the peer's /32.
    forward_out = [r for r in ups if re.match(r"iptables -I FORWARD \d+ -o %i", r)]
    assert forward_out == [
        "iptables -I FORWARD 3 -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p tcp --dport ${TENANT_SQL_PORT} -j ACCEPT",
        "iptables -I FORWARD 4 -o %i -d ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT",
    ]
    # The host itself accepts nothing NEW from the tunnel but a ping from the peer.
    input_accepts = [r for r in ups if re.match(r"iptables -I INPUT \d+ ", r) and r.endswith("-j ACCEPT")]
    assert input_accepts == [
        "iptables -I INPUT 1 " + established,
        "iptables -I INPUT 2 -i %i -s ${CUSTOMER_PEER_TUNNEL_IP}/32 -p icmp --icmp-type echo-request -j ACCEPT",
    ]
    # Every PostUp rule is undone by a PostDown with the same specification.
    expected_downs = []
    for rule in ups:
        undone = re.sub(r"iptables -I (\w+) \d+ ", r"iptables -D \1 ", rule)
        undone = undone.replace("iptables -t nat -A ", "iptables -t nat -D ")
        expected_downs.append(undone)
    assert downs == expected_downs, "PostDown must mirror PostUp rule for rule"


def test_security_group_script_opens_the_udp_port_to_one_address_only():
    text = _read("vpn/server/open_security_group.sh")
    assert "authorize-security-group-ingress" in text
    assert "${CUSTOMER_PUBLIC_IP}/32" in text
    assert "IpProtocol=udp" in text
    assert "--revoke" in text


def test_vpn_readme_states_what_needs_the_customer_public_ip_first():
    text = _read("vpn/README.md")
    assert "<CUSTOMER_PUBLIC_IP>" in text
    # The customer's key is never a placeholder in this document: they generate it and send it.
    assert "<CUSTOMER_PEER_PUBLIC_KEY>" not in text
    assert "clave pública" in text
    assert "open_security_group.sh" in text and "install_wireguard_host.sh" in text
    assert "--keys-only" in text
    assert "portproxy" in text


def test_vpn_readme_states_the_one_way_boundary():
    text = _read("vpn/README.md")
    assert "`DROP`" in text and "`FORWARD`" in text and "`INPUT`" in text
    assert re.search(r"no entra nada", text, flags=re.IGNORECASE)


UNBUILT_PROMISES = re.compile(r"\b(firmad[oa]s?|firmas?|manifiestos?|checksums?|latidos?|heartbeats?)\b", re.IGNORECASE)


def test_windows_readme_only_describes_and_points_to_the_agent_tree():
    text = _read("windows/README.md")
    assert "windows-agent" in text
    assert not any(p.suffix in {".py", ".ps1", ".exe", ".msi"} for p in (KIT / "windows").rglob("*"))


def test_windows_readme_describes_the_delivered_agent_without_unbuilt_promises():
    """What ships is a Python venv run by a Task Scheduler task with SQLite
    state and a local spool; the customer-facing summary may promise nothing
    beyond that (no signing, manifests, checksums or heartbeat)."""
    text = _read("windows/README.md")
    match = UNBUILT_PROMISES.search(text)
    assert not match, f"windows/README.md promises {match.group(0)!r}, which the agent does not implement"
    assert "../windows-agent/README.md" in text
    for delivered in ("install.ps1", "Programador de tareas", "SQLite", "spool", "raw/sap_b1/", "run.ps1 status"):
        assert delivered in text, delivered
    assert "en paralelo" not in text


# ── Cartridge README ────────────────────────────────────────────────────────


def test_cartridge_readme_links_the_kit():
    text = README.read_text(encoding="utf-8")
    assert "## Connection kit" in text
    assert "connect/" in text
    assert "`tests/fixtures/sap_b1` at the\nrepository root" in text or "`tests/fixtures/sap_b1` at the repository root" in text


def test_cartridge_readme_lists_every_entity_it_reads():
    text = README.read_text(encoding="utf-8")
    table = text[text.index("## What it reads") : text.index("### Incremental reads")]
    listed = set(re.findall(r"`([A-Z][A-Z0-9]{3})`", table))
    assert listed == _entity_names(), f"missing {_entity_names() - listed}, extra {listed - _entity_names()}"
    assert f"{len(_entity_names())} tables" in table


def test_cartridge_readme_states_the_headers_each_guard_reads():
    text = README.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| **Internal auth** |"))
    rest, mcp = row.split("`/mcp/rpc`")
    assert "`X-Internal-Api-Key` or `X-Api-Key`" in rest and "`X-Internal-Service`" in rest
    assert "`X-Api-Key` plus `X-Internal-Service`" in mcp
    assert "X-Internal-Api-Key" not in mcp, "/mcp/rpc never reads X-Internal-Api-Key"
    # The claim must match the guard: the ASGI guard on /mcp/rpc reads x-api-key only.
    guard = (CARTRIDGE / "app" / "security.py").read_text(encoding="utf-8")
    guard_body = guard[guard.index("class InternalApiKeyASGIGuard") :]
    assert 'headers.get("x-api-key")' in guard_body and 'headers.get("x-internal-service")' in guard_body
    assert "x-internal-api-key" not in guard_body
