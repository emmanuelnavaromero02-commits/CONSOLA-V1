#!/usr/bin/env python3
"""OMEGA push agent for SAP Business One (the "Windows connector").

Runs on the customer's Windows server, reads the Business One company
schemas over SQL (SAP HANA, ``hdbcli``) and uploads Bronze parquet files to
the lakehouse bucket over HTTPS. No tunnel into the customer's network is
needed: the agent only ever opens outbound connections.

It is the cartridge's own extraction, not a fork of it:

* the catalogue is ``app/config/entities.yaml``;
* plans, SQL, watermark rules, records and the declared arrow schema come
  from ``app.services.b1_queries``;
* the per-company loop (keyset pages, 5 minute back-off, source-clock cap,
  flush-before-commit) is ``app.services.b1_reader.read_entity``, the same
  function ``extraction_service.run_entity`` calls in the platform;
* the file shape and the object layout come from
  ``app.services.bronze_parquet``.

What differs is only where things are kept: watermarks and the run log in
a local SQLite file instead of the platform Postgres, and files in a local
spool directory until S3 has confirmed them.

Commands::

    python agent.py test-connection
    python agent.py extract --entity OINV --mode incremental|full [--from-date --to-date]
    python agent.py extract-all [--mode full] [--entity OINV --entity INV1]
    python agent.py initial-load [--months 24] [--window 22:00-05:30] [--restart]
    python agent.py serve [--interval-minutes 120]
    python agent.py inventory [--months 24] [--json]
    python agent.py status [--json]

``--output-dir DIR`` on ``extract``/``extract-all`` writes the files under
DIR with the bucket layout instead of uploading them (used by the tests).

Exit codes: 0 success, 1 a run failed, 2 configuration or usage error.
Logs never carry passwords, hosts, users, database or schema names: every
line is scrubbed before it is written.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import logging.handlers
import os
import re
import sqlite3
import sys
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

AGENT_VERSION = "0.2.0"
AGENT_NAME = "omega-sap-b1-agent"
HERE = Path(__file__).resolve().parent
CARTRIDGE_ROOT_ENV = "OMEGA_SAP_B1_CARTRIDGE_ROOT"
CONFIG_ENV = "OMEGA_SAP_B1_AGENT_CONFIG"
STATE_DB_NAME = "agent-state.sqlite"
LOCK_NAME = "agent.lock"
LOG_NAME = "agent.log"
QUARANTINE_DIR_NAME = "quarantine"
SPOOL_NAME_HEX = 20
PROBE_ENTITY = "CINF"
DEFAULT_MAX_PENDING_FILES = 500
DEFAULT_UPLOAD_ATTEMPTS = 5
EXIT_OK, EXIT_FAILED, EXIT_CONFIG = 0, 1, 2
INITIAL_LOAD_META = "initial_load."
DEFAULT_HISTORY_MONTHS = 24
DEFAULT_SERVE_MINUTES = 120

CARTRIDGE_FILES = (
    "app/__init__.py",
    "app/core/__init__.py",
    "app/core/b1_source.py",
    "app/services/__init__.py",
    "app/services/b1_queries.py",
    "app/services/b1_reader.py",
    "app/services/bronze_parquet.py",
    "app/services/intercompany_mapping.py",
    "app/config/entities.yaml",
)


def _locate_cartridge_root() -> Path:
    """The directory holding ``app/`` (the cartridge modules the agent reuses)."""
    candidates: list[Path] = []
    override = os.environ.get(CARTRIDGE_ROOT_ENV, "").strip()
    if override:
        candidates.append(Path(override))
    candidates.extend(list(HERE.parents)[:2])
    candidates.append(HERE / "cartridge")
    for candidate in candidates:
        if (candidate / "app" / "config" / "entities.yaml").is_file() and (
            candidate / "app" / "services" / "b1_reader.py"
        ).is_file():
            missing = [relative for relative in CARTRIDGE_FILES if not (candidate / relative).is_file()]
            if missing:
                raise SystemExit(
                    f"{AGENT_NAME}: the cartridge copy under {candidate} is incomplete, missing "
                    f"{', '.join(missing)}; run install.ps1 again"
                )
            return candidate.resolve()
    raise SystemExit(
        f"{AGENT_NAME}: cannot find the cartridge modules (app/); "
        f"set {CARTRIDGE_ROOT_ENV} to the directory that contains app/"
    )


CARTRIDGE_ROOT = _locate_cartridge_root()
if str(CARTRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(CARTRIDGE_ROOT))

from app.core import b1_source  # noqa: E402
from app.services import b1_queries, b1_reader, bronze_parquet, intercompany_mapping  # noqa: E402

ENTITIES_PATH = CARTRIDGE_ROOT / "app" / "config" / "entities.yaml"


class AgentError(RuntimeError):
    """A run could not complete; reported as a failed run, exit code 1."""


class ConfigError(AgentError):
    """The agent lacks or rejects configuration; exit code 2."""


class UploadError(AgentError):
    """A file could not be delivered to S3 after the configured attempts."""


class UploadRejected(UploadError):
    """S3 refused the key or the bucket for good (AccessDenied, ExpiredToken, ...)."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def scrub(text: Any, secrets: Sequence[str]) -> str:
    """Replace every secret (password, host, user, database, schema names, access keys) with ``***``."""
    out = str(text if text is not None else "")
    for secret in sorted((s for s in secrets if s and len(s) >= 3), key=len, reverse=True):
        out = out.replace(secret, "***")
    return out


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: Sequence[str]) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record), self.secrets)


@dataclass
class UploadConfig:
    bucket: str = ""
    region: str = ""
    endpoint_url: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""
    max_attempts: int = DEFAULT_UPLOAD_ATTEMPTS
    server_side_encryption: str = ""
    sse_kms_key_id: str = ""

    def secrets(self) -> tuple[str, ...]:
        return tuple(v for v in (self.secret_access_key, self.session_token, self.access_key_id) if v)


@dataclass
class AgentConfig:
    tenant_id: str
    workspace_id: str
    state_dir: Path
    spool_dir: Path
    log_dir: Path
    entities: list[str]
    exclude: list[str]
    source: "b1_source.B1Config"
    upload: UploadConfig
    intercompany: list["intercompany_mapping.IntercompanyPartner"] = field(default_factory=list)
    max_pending_files: int = DEFAULT_MAX_PENDING_FILES
    log_level: str = "INFO"
    config_path: Path | None = None

    @property
    def state_db(self) -> Path:
        return self.state_dir / STATE_DB_NAME

    @property
    def scope(self) -> str:
        return bronze_parquet.scope_prefix(self.tenant_id, self.workspace_id)

    def secrets(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*b1_source._secrets_of(self.source), *self.upload.secrets())))

    def require_scope(self) -> None:
        try:
            self.scope
        except ValueError as exc:
            raise ConfigError(f"[agent] tenant_id / workspace_id: {exc}") from exc

    def require_source(self) -> None:
        if self.source.missing:
            raise ConfigError(
                "source connection is incomplete; missing: "
                + ", ".join(f"[source] {_SOURCE_KEYS[name]} (or {name})" for name in self.source.missing)
            )

    def require_upload(self) -> None:
        if not self.upload.bucket:
            raise ConfigError("[upload] bucket (or OMEGA_S3_BUCKET) is required to upload; use --output-dir to write locally")


_SOURCE_KEYS = {
    "SAP_B1_DIALECT": "dialect",
    "SAP_B1_HOST": "host",
    "SAP_B1_PORT": "port",
    "SAP_B1_USER": "user",
    "SAP_B1_PASSWORD": "password",
    "SAP_B1_DATABASE": "database",
    "SAP_B1_COMPANIES": "companies",
}


def _section(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return dict(value)


_PLACEHOLDER = re.compile(r"^<[A-Z][A-Z0-9_]*>$")


def _text(section: Mapping[str, Any], key: str, env: Mapping[str, str], env_name: str | None, default: str = "") -> str:
    """Environment first (so a secret can live outside the file), then the file."""
    if env_name and env.get(env_name, "").strip():
        text = env[env_name].strip()
    else:
        value = section.get(key)
        if value is None:
            return default
        text = ("true" if value else "false") if isinstance(value, bool) else str(value).strip()
    if _PLACEHOLDER.fullmatch(text):
        raise ConfigError(f"{key}: replace the template placeholder {text}")
    return text


def _source_config(section: Mapping[str, Any], env: Mapping[str, str]) -> "b1_source.B1Config":
    dialect = (_text(section, "dialect", env, "SAP_B1_DIALECT") or "hana").lower()
    host = _text(section, "host", env, "SAP_B1_HOST")
    port_text = _text(section, "port", env, "SAP_B1_PORT")
    user = _text(section, "user", env, "SAP_B1_USER")
    password = _text(section, "password", env, "SAP_B1_PASSWORD")
    database = _text(section, "database", env, "SAP_B1_DATABASE")
    companies_spec = _text(section, "companies", env, "SAP_B1_COMPANIES")
    encrypt = b1_source._truthy(_text(section, "encrypt", env, "SAP_B1_ENCRYPT"), True)
    ssl_validate = b1_source._truthy(
        _text(section, "ssl_validate_certificate", env, "SAP_B1_SSL_VALIDATE_CERTIFICATE"), True
    )
    timeout_text = _text(section, "connect_timeout_seconds", env, "SAP_B1_CONNECT_TIMEOUT_SECONDS", "15")

    missing: list[str] = []
    if dialect not in b1_source.DIALECTS:
        missing.append("SAP_B1_DIALECT")
    for name, value in (("SAP_B1_HOST", host), ("SAP_B1_USER", user), ("SAP_B1_PASSWORD", password)):
        if not value:
            missing.append(name)
    if dialect == "postgres" and not database:
        missing.append("SAP_B1_DATABASE")

    port = b1_source.DEFAULT_PORTS.get(dialect, 0)
    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            port = 0
        if not 0 < port < 65536:
            missing.append("SAP_B1_PORT")

    companies: list[b1_source.Company] = []
    if not companies_spec:
        missing.append("SAP_B1_COMPANIES")
    else:
        try:
            companies = b1_source.parse_companies(companies_spec)
        except b1_source.B1ConfigurationError as exc:
            raise ConfigError(f"[source] companies: {exc}") from exc
        if not companies:
            missing.append("SAP_B1_COMPANIES")

    try:
        connect_timeout = max(1, int(timeout_text or 15))
    except ValueError as exc:
        raise ConfigError("[source] connect_timeout_seconds must be an integer") from exc

    return b1_source.B1Config(
        dialect=dialect,
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        companies=companies,
        encrypt=encrypt,
        ssl_validate_certificate=ssl_validate,
        connect_timeout=connect_timeout,
        missing=missing,
    )


def _intercompany_config(
    section: Mapping[str, Any], env: Mapping[str, str], source: "b1_source.B1Config"
) -> list["intercompany_mapping.IntercompanyPartner"]:
    """``[source] intercompany`` (or ``SAP_B1_INTERCOMPANY``)."""
    spec = _text(section, "intercompany", env, "SAP_B1_INTERCOMPANY")
    try:
        partners = intercompany_mapping.parse_intercompany(spec)
        intercompany_mapping.validate_against_companies(partners, (company.alias for company in source.companies))
    except b1_source.B1ConfigurationError as exc:
        raise ConfigError(f"[source] intercompany: {exc}") from exc
    return partners


def _upload_config(section: Mapping[str, Any], env: Mapping[str, str]) -> UploadConfig:
    attempts_text = _text(section, "max_attempts", env, None, str(DEFAULT_UPLOAD_ATTEMPTS))
    try:
        attempts = max(1, int(attempts_text))
    except ValueError as exc:
        raise ConfigError("[upload] max_attempts must be an integer") from exc
    region = _text(section, "region", env, "AWS_REGION") or env.get("AWS_DEFAULT_REGION", "").strip()
    return UploadConfig(
        bucket=_text(section, "bucket", env, "OMEGA_S3_BUCKET"),
        region=region,
        endpoint_url=_text(section, "endpoint_url", env, "OMEGA_S3_ENDPOINT_URL"),
        access_key_id=_text(section, "access_key_id", env, "AWS_ACCESS_KEY_ID"),
        secret_access_key=_text(section, "secret_access_key", env, "AWS_SECRET_ACCESS_KEY"),
        session_token=_text(section, "session_token", env, "AWS_SESSION_TOKEN"),
        max_attempts=attempts,
        server_side_encryption=_text(section, "server_side_encryption", env, None),
        sse_kms_key_id=_text(section, "sse_kms_key_id", env, None),
    )


def _names(value: Any, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part for part in value.replace(";", ",").split(",")]
    if not isinstance(value, (list, tuple)):
        raise ConfigError(f"[agent] {key} must be a list of entity names")
    return [str(item).strip() for item in value if str(item).strip()]


def load_config(path: Path | None, env: Mapping[str, str] | None = None) -> AgentConfig:
    env = os.environ if env is None else env
    data: dict[str, Any] = {}
    if path is not None:
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConfigError(f"configuration file not found: {path}") from exc
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ConfigError(f"configuration file is not valid TOML: {exc}") from exc
    agent = _section(data, "agent")
    base_dir = path.resolve().parent if path is not None else Path.cwd()

    state_text = _text(agent, "state_dir", env, "OMEGA_AGENT_STATE_DIR")
    state_dir = (base_dir / state_text).resolve() if state_text else (base_dir / "state").resolve()
    spool_text = _text(agent, "spool_dir", env, None)
    log_text = _text(agent, "log_dir", env, None)
    pending_text = _text(agent, "max_pending_files", env, None, str(DEFAULT_MAX_PENDING_FILES))
    try:
        max_pending = max(1, int(pending_text))
    except ValueError as exc:
        raise ConfigError("[agent] max_pending_files must be an integer") from exc

    source_section = _section(data, "source")
    source = _source_config(source_section, env)
    return AgentConfig(
        tenant_id=_text(agent, "tenant_id", env, "OMEGA_TENANT_ID"),
        workspace_id=_text(agent, "workspace_id", env, "OMEGA_WORKSPACE_ID"),
        state_dir=state_dir,
        spool_dir=(state_dir / spool_text).resolve() if spool_text else state_dir / "spool",
        log_dir=(state_dir / log_text).resolve() if log_text else state_dir / "logs",
        entities=_names(agent.get("entities"), "entities"),
        exclude=_names(agent.get("exclude"), "exclude"),
        source=source,
        upload=_upload_config(_section(data, "upload"), env),
        intercompany=_intercompany_config(source_section, env, source),
        max_pending_files=max_pending,
        log_level=(_text(agent, "log_level", env, "OMEGA_AGENT_LOG_LEVEL") or "INFO").upper(),
        config_path=path,
    )


def load_catalogue() -> list[dict[str, Any]]:
    import yaml

    with ENTITIES_PATH.open(encoding="utf-8") as handle:
        entities = (yaml.safe_load(handle) or {}).get("entities", [])
    return [dict(entity) for entity in entities if entity.get("entity")]


def select_entities(
    catalogue: Sequence[dict[str, Any]],
    config: AgentConfig,
    only: Sequence[str] = (),
    log: logging.Logger | None = None,
) -> list[dict[str, Any]]:
    """The entities a command runs, in catalogue order of the request."""
    by_name = {entity["entity"]: entity for entity in catalogue}
    unknown = [name for name in (*only, *config.entities, *config.exclude) if name not in by_name]
    if unknown:
        raise ConfigError(f"unknown entities (not in entities.yaml): {', '.join(dict.fromkeys(unknown))}")
    excluded = set(config.exclude)
    if only:
        overridden = [name for name in only if name in excluded]
        if overridden and log is not None:
            log.info("--entity %s overrides [agent] exclude for this run", ", ".join(overridden))
        return [by_name[name] for name in only]
    wanted = list(config.entities) or list(by_name)
    return [by_name[name] for name in wanted if name not in excluded]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity_watermarks (
    entity_name          TEXT PRIMARY KEY,
    watermark_field      TEXT NOT NULL,
    last_watermark_value TEXT NOT NULL,
    last_run_id          TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_runs (
    run_id            TEXT PRIMARY KEY,
    entity_name       TEXT NOT NULL,
    run_type          TEXT NOT NULL,
    status            TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    records_extracted INTEGER,
    batches           INTEGER,
    storage_uri       TEXT,
    source_clock      TEXT,
    error_message     TEXT,
    agent_version     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spool (
    path         TEXT PRIMARY KEY,
    object_name  TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    entity_name  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);
CREATE TABLE IF NOT EXISTS spool_lost (
    path             TEXT NOT NULL,
    object_name      TEXT NOT NULL,
    run_id           TEXT NOT NULL,
    entity_name      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    lost_at          TEXT NOT NULL,
    reason           TEXT NOT NULL,
    quarantine_path  TEXT
);
CREATE TABLE IF NOT EXISTS initial_load_steps (
    step        TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    records     INTEGER NOT NULL,
    finished_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AgentState:
    """Watermarks per ``Entity@alias``, the run log and the spool ledger."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


    def get_watermark(self, entity_name: str) -> str | None:
        row = self.conn.execute(
            "SELECT last_watermark_value FROM entity_watermarks WHERE entity_name = ?", (entity_name,)
        ).fetchone()
        return row[0] if row else None

    def update_watermark(self, entity_name: str, watermark_field: str, value: str, run_id: str) -> None:
        self.conn.execute(
            """
            INSERT INTO entity_watermarks (entity_name, watermark_field, last_watermark_value, last_run_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_name) DO UPDATE SET
                watermark_field = excluded.watermark_field,
                last_watermark_value = excluded.last_watermark_value,
                last_run_id = excluded.last_run_id,
                updated_at = excluded.updated_at
            WHERE COALESCE(entity_watermarks.last_watermark_value, '') <= excluded.last_watermark_value
            """,
            (entity_name, watermark_field, value, run_id, _utc_now_text()),
        )
        self.conn.commit()

    def list_watermarks(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM entity_watermarks ORDER BY entity_name").fetchall()
        return [dict(row) for row in rows]


    def create_run(self, entity_name: str, run_type: str) -> str:
        run_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO extraction_runs (run_id, entity_name, run_type, status, started_at, agent_version)"
            " VALUES (?, ?, ?, 'running', ?, ?)",
            (run_id, entity_name, run_type, _utc_now_text(), AGENT_VERSION),
        )
        self.conn.commit()
        return run_id

    def set_run_clock(self, run_id: str, source_clock: str | None) -> None:
        self.conn.execute("UPDATE extraction_runs SET source_clock = ? WHERE run_id = ?", (source_clock, run_id))
        self.conn.commit()

    def finish_run(
        self,
        run_id: str,
        status: str,
        *,
        records: int = 0,
        batches: int = 0,
        storage_uri: str = "",
        error_message: str | None = None,
    ) -> None:
        error = error_message[:4000] if error_message else None
        self.conn.execute(
            "UPDATE extraction_runs SET status = ?, records_extracted = ?, batches = ?, storage_uri = ?,"
            " error_message = ?, finished_at = ? WHERE run_id = ?",
            (status, records, batches, storage_uri, error, _utc_now_text(), run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM extraction_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM extraction_runs ORDER BY started_at DESC, rowid DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]


    def spool_add(self, path: Path, object_name: str, run_id: str, entity_name: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO spool (path, object_name, run_id, entity_name, created_at, attempts)"
            " VALUES (?, ?, ?, ?, ?, 0)",
            (str(path), object_name, run_id, entity_name, _utc_now_text()),
        )
        self.conn.commit()

    def spool_attempt(self, path: Path, error: str | None) -> None:
        self.conn.execute(
            "UPDATE spool SET attempts = attempts + 1, last_error = ? WHERE path = ?", (error, str(path))
        )
        self.conn.commit()

    def spool_uploaded(self, path: Path) -> None:
        self.conn.execute("DELETE FROM spool WHERE path = ?", (str(path),))
        self.conn.commit()

    def spool_pending(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM spool ORDER BY created_at, rowid").fetchall()
        return [dict(row) for row in rows]

    def spool_lose(self, path: Path, reason: str, quarantine_path: Path | None = None) -> None:
        """A spooled file that can never be uploaded (missing or unreadable)."""
        self.conn.execute(
            """
            INSERT INTO spool_lost (path, object_name, run_id, entity_name, created_at, lost_at, reason, quarantine_path)
            SELECT path, object_name, run_id, entity_name, created_at, ?, ?, ? FROM spool WHERE path = ?
            """,
            (_utc_now_text(), reason[:4000], str(quarantine_path) if quarantine_path else None, str(path)),
        )
        self.conn.execute("DELETE FROM spool WHERE path = ?", (str(path),))
        self.conn.commit()

    def spool_lost(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM spool_lost ORDER BY lost_at, rowid").fetchall()
        return [dict(row) for row in rows]

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM agent_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO agent_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    def step_records(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT step, records FROM initial_load_steps").fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def record_step(self, step: str, run_id: str, records: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO initial_load_steps (step, run_id, records, finished_at) VALUES (?, ?, ?, ?)",
            (step, run_id, int(records), _utc_now_text()),
        )
        self.conn.commit()

    def reset_initial_load(self) -> None:
        self.conn.execute("DELETE FROM initial_load_steps")
        self.conn.execute("DELETE FROM agent_meta WHERE key LIKE ?", (f"{INITIAL_LOAD_META}%",))
        self.conn.commit()


_NO_RETRY_CODES = {
    "AccessDenied",
    "AllAccessDisabled",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "ExpiredToken",
    "NoSuchBucket",
    "InvalidBucketName",
}


_ERROR_CODE_IN_MESSAGE = re.compile(r"An error occurred \(([A-Za-z0-9_.]+)\)")


def scoped_prefix(scope: str, entity: str = "*") -> str:
    """``raw/sap_b1/<entity>/tenant_id=<t>/workspace_id=<w>/``."""
    return f"{bronze_parquet.BRONZE_PREFIX}{entity}/{scope}"


class S3Uploader:
    """boto3 uploads restricted to this tenant and workspace under ``raw/sap_b1/<entity>/``."""

    def __init__(self, config: UploadConfig, log: logging.Logger, *, scope: str, client: Any = None) -> None:
        self.config = config
        self.log = log
        self.scope = scope
        self.client = client if client is not None else self._build_client(config)

    @staticmethod
    def _build_client(config: UploadConfig) -> Any:
        import boto3
        from botocore.config import Config

        session = boto3.session.Session(
            aws_access_key_id=config.access_key_id or None,
            aws_secret_access_key=config.secret_access_key or None,
            aws_session_token=config.session_token or None,
            region_name=config.region or None,
        )
        return session.client(
            "s3",
            endpoint_url=config.endpoint_url or None,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=15,
                read_timeout=120,
                s3={"addressing_style": "path" if config.endpoint_url else "virtual"},
                user_agent_extra=f"{AGENT_NAME}/{AGENT_VERSION}",
            ),
        )

    def _extra_args(self) -> dict[str, str]:
        extra: dict[str, str] = {}
        if self.config.server_side_encryption:
            extra["ServerSideEncryption"] = self.config.server_side_encryption
            if self.config.sse_kms_key_id:
                extra["SSEKMSKeyId"] = self.config.sse_kms_key_id
        return extra

    @staticmethod
    def _error_code(exc: BaseException) -> str:
        """The S3 error code of ``exc``: from its ``response``, from the exception it wraps (``__cause__``/``__context__``), or."""
        seen: set[int] = set()
        current: BaseException | None = exc
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            response = getattr(current, "response", None)
            if isinstance(response, dict):
                code = str((response.get("Error") or {}).get("Code") or "")
                if code:
                    return code
            current = current.__cause__ or current.__context__
        match = _ERROR_CODE_IN_MESSAGE.search(str(exc))
        return match.group(1) if match else ""

    def check_scope(self, object_name: str) -> None:
        """Refuse any key outside ``raw/sap_b1/<entity>/<this scope>``."""
        prefix = bronze_parquet.BRONZE_PREFIX
        entity, separator, rest = object_name[len(prefix):].partition("/") if object_name.startswith(prefix) else ("", "", "")
        if (
            not entity
            or not separator
            or entity in (".", "..")
            or ".." in object_name.split("/")
            or not rest.startswith(self.scope)
        ):
            raise UploadError(f"refusing to upload outside {scoped_prefix(self.scope)}: {object_name}")

    def probe(self) -> None:
        """Prove the key can see its own prefix without writing anything."""
        self.client.list_objects_v2(Bucket=self.config.bucket, Prefix=scoped_prefix(self.scope, PROBE_ENTITY), MaxKeys=1)

    def upload(self, path: Path, object_name: str) -> None:
        self.check_scope(object_name)
        delay = 2.0
        last: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                self.client.upload_file(str(path), self.config.bucket, object_name, ExtraArgs=self._extra_args() or None)
                return
            except Exception as exc:  # noqa: BLE001 - every failure is retried or reported
                last = exc
                code = self._error_code(exc)
                if code in _NO_RETRY_CODES:
                    raise UploadRejected(
                        f"upload rejected ({code}); check the access key and the IAM policy for the bucket", code
                    ) from exc
                self.log.warning(
                    "upload attempt %d/%d failed for %s: %s: %s",
                    attempt,
                    self.config.max_attempts,
                    object_name,
                    type(exc).__name__,
                    exc,
                )
                if attempt < self.config.max_attempts:
                    time.sleep(delay)
                    delay = min(delay * 2, 60.0)
        raise UploadError(
            f"upload failed after {self.config.max_attempts} attempts: {type(last).__name__}: {last}"
        )


@dataclass
class DrainResult:
    uploaded: int = 0
    pending: int = 0
    lost: int = 0


class Spool:
    """Files wait here until S3 confirms them; nothing is deleted before."""

    def __init__(
        self,
        root: Path,
        state: AgentState | None,
        uploader: S3Uploader | None,
        log: logging.Logger,
        secrets: Sequence[str] = (),
    ) -> None:
        self.root = root
        self.state = state
        self.uploader = uploader
        self.log = log
        self.secrets = tuple(secrets)
        self.rejected: str | None = None
        root.mkdir(parents=True, exist_ok=True)

    @property
    def delivers_locally(self) -> bool:
        return self.uploader is None

    @property
    def quarantine_dir(self) -> Path:
        return self.root / QUARANTINE_DIR_NAME

    def _target(self, object_name: str) -> Path:
        if self.delivers_locally:
            return self.root.joinpath(*object_name.split("/"))
        return self.root / f"{uuid.uuid4().hex[:SPOOL_NAME_HEX]}.parquet"

    def _write(self, table, object_name: str) -> Path:
        """Write next to the target and rename into place, with the bytes forced to disk first."""
        target = self._target(object_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        with open(partial, "wb") as handle:
            bronze_parquet.write_bronze_file(table, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, target)
        return target

    def _record_failure(self, path: Path, error: str) -> None:
        if self.state is not None:
            self.state.spool_attempt(path, scrub(error, self.secrets))

    def _remove(self, path: Path) -> None:
        """S3 confirmed the file, so the delivery stands whatever the local cleanup does (on Windows an antivirus may still hold the file)."""
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.log.warning("uploaded file could not be removed yet, swept on the next run: %s (%s)", path.name, exc)

    def _try_upload(self, path: Path, object_name: str) -> bool:
        assert self.uploader is not None
        if self.rejected:
            self._record_failure(path, self.rejected)
            self.log.warning("kept in spool, uploads are suspended for this cycle: %s", object_name)
            return False
        try:
            self.uploader.upload(path, object_name)
        except UploadRejected as exc:
            self.rejected = str(exc)
            self._record_failure(path, str(exc))
            self.log.error("%s; no further upload is tried this cycle, files stay in the spool: %s", exc, object_name)
            return False
        except UploadError as exc:
            self._record_failure(path, str(exc))
            self.log.error("kept in spool, will retry on the next run: %s (%s)", object_name, exc)
            return False
        if self.state is not None:
            self.state.spool_uploaded(path)
        self._remove(path)
        return True

    def deliver(self, table, object_name: str, *, run_id: str, entity: str) -> tuple[Path, bool]:
        """Write the batch; upload it now when there is an uploader."""
        path = self._write(table, object_name)
        if self.uploader is None:
            return path, True
        if self.state is not None:
            self.state.spool_add(path, object_name, run_id, entity)
        return path, self._try_upload(path, object_name)

    @staticmethod
    def _unreadable(path: Path) -> str | None:
        """None when ``path`` is a parquet file with a readable footer."""
        import pyarrow.parquet as pq

        try:
            with pq.ParquetFile(path) as parquet:
                int(parquet.metadata.num_rows)
        except Exception as exc:  # noqa: BLE001 - any failure to open it is the answer
            return f"unreadable parquet file ({type(exc).__name__}: {exc})"
        return None

    def _quarantine(self, path: Path) -> Path | None:
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)
        target = self.quarantine_dir / path.name
        try:
            os.replace(path, target)
        except OSError as exc:
            self.log.warning("could not move %s to quarantine: %s", path.name, exc)
            return None
        return target

    def _lose(self, row: Mapping[str, Any], reason: str, quarantine_path: Path | None) -> None:
        assert self.state is not None
        self.state.spool_lose(Path(row["path"]), scrub(reason, self.secrets), quarantine_path)
        self.log.error(
            "spool file lost, its rows never reached S3 and the watermark already passed them: %s (%s);"
            " re-extract the table (--mode full or a date range)",
            row["object_name"],
            reason,
        )

    def sweep(self) -> None:
        """Delete files in the spool the ledger does not know."""
        assert self.state is not None
        known = {Path(row["path"]).name for row in self.state.spool_pending()}
        for candidate in sorted(self.root.iterdir()):
            if not candidate.is_file() or candidate.name in known or not candidate.name.endswith((".parquet", ".part")):
                continue
            try:
                candidate.unlink()
            except OSError as exc:
                self.log.warning("leftover spool file could not be removed: %s (%s)", candidate.name, exc)
            else:
                self.log.info("removed leftover spool file: %s", candidate.name)

    def drain(self) -> DrainResult:
        """Retry every pending file, oldest first."""
        result = DrainResult()
        if self.uploader is None or self.state is None:
            return result
        self.sweep()
        for row in self.state.spool_pending():
            path = Path(row["path"])
            if not path.is_file():
                self._lose(row, "spool file is missing", None)
                result.lost += 1
                continue
            reason = self._unreadable(path)
            if reason:
                self._lose(row, reason, self._quarantine(path))
                result.lost += 1
                continue
            if self.rejected:
                result.pending += 1
                continue
            if self._try_upload(path, row["object_name"]):
                result.uploaded += 1
                self.log.info("uploaded from spool: %s", row["object_name"])
            else:
                result.pending += 1
        return result


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists, just not ours to query
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _claim(source: Path, target: Path) -> None:
    """Make ``source`` appear as ``target`` atomically, failing with FileExistsError when ``target`` exists."""
    if os.name == "nt":
        os.rename(source, target)
        return
    os.link(source, target)
    source.unlink()


class RunLock:
    """One extraction at a time per state directory (a scheduled cycle must not overlap a manual run)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._held = False

    @staticmethod
    def _owner(text: str) -> int | None:
        try:
            pid = int(text.split()[0])
        except (ValueError, IndexError):
            return None
        return pid if pid > 0 else None

    def _inspect_existing(self) -> None:
        """Raise when the existing lock must be respected."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        owner = self._owner(text)
        if owner is None:
            raise AgentError(
                f"the run lock cannot be read ({self.path}); if no agent is running, delete it and run again"
            )
        if owner != os.getpid() and _pid_alive(owner):
            raise AgentError(f"another agent run is in progress (pid {owner}); lock: {self.path}")
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        for _ in range(3):
            self._inspect_existing()
            staging.write_text(f"{os.getpid()} {_utc_now_text()}\n", encoding="utf-8")
            try:
                _claim(staging, self.path)
            except FileExistsError:
                staging.unlink(missing_ok=True)
                continue
            self._held = True
            return self
        staging.unlink(missing_ok=True)
        raise AgentError(f"could not acquire the run lock: {self.path}")

    def __exit__(self, *_exc: Any) -> None:
        if self._held:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._held = False


@dataclass
class RunOutcome:
    entity: str
    run_id: str
    mode: str
    status: str
    records: int = 0
    batches: int = 0
    storage_uri: str = ""
    error: str | None = None
    companies: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "success"


@dataclass
class Runtime:
    config: AgentConfig
    state: AgentState
    spool: Spool
    log: logging.Logger


def run_extract(
    runtime: Runtime,
    entity_config: dict[str, Any],
    *,
    mode: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    skip_empty: bool = False,
) -> RunOutcome:
    """One entity across every company: the cartridge's ``run_entity`` with the SQLite state and the spool as sinks."""
    config, state, spool, log = runtime.config, runtime.state, runtime.spool, runtime.log
    entity_name = str(entity_config.get("entity") or "")
    plan = b1_queries.plan_from_config(entity_config)
    if entity_config.get("protection"):
        raise ConfigError(
            f"{entity_name}: declares field protection rules; this agent does not apply them, use the platform cartridge"
        )
    schema = b1_queries.arrow_schema(plan)
    if schema is None:
        raise ConfigError(f"{entity_name}: entities.yaml declares no column_types; the agent only writes typed files")
    effective_config = dict(entity_config)
    if mode:
        effective_config["mode"] = mode
    effective_mode = b1_reader.effective_mode(effective_config, plan, from_date, to_date)
    if effective_mode == "historical" and not plan.date_field:
        raise ConfigError(f"{entity_name}: a date range needs a date_field in the catalogue")
    if mode == "incremental" and effective_mode == "full":
        log.info("%s: incremental requested but the table has no update stamp; read whole (full)", entity_name)
    scope = config.scope
    bucket = config.upload.bucket

    run_id = state.create_run(entity_name, effective_mode)
    outcome = RunOutcome(entity=entity_name, run_id=run_id, mode=effective_mode, status="running")
    log.info(
        "run %s: %s mode=%s companies=%s destination=%s",
        run_id,
        entity_name,
        effective_mode,
        ",".join(company.alias for company in config.source.companies),
        "local" if spool.delivers_locally else f"s3://{bucket}/{bronze_parquet.BRONZE_PREFIX}",
    )
    pending: list[str] = []

    def _write_batch(records: list[dict[str, Any]]) -> None:
        if skip_empty and not records:
            return
        batch_run_id = run_id if outcome.batches == 0 else f"{run_id}-b{outcome.batches}"
        load_date, extracted_at = bronze_parquet.stamp_now()
        table = bronze_parquet.bronze_table(
            records,
            schema=schema,
            entity=entity_name,
            run_id=batch_run_id,
            load_type=effective_mode,
            watermark_field=plan.watermark_field,
            extracted_at=extracted_at,
        )
        object_name = bronze_parquet.bronze_object_name(entity_name, scope, load_date, batch_run_id)
        path, uploaded = spool.deliver(table, object_name, run_id=run_id, entity=entity_name)
        outcome.batches += 1
        outcome.storage_uri = str(path) if spool.delivers_locally else f"s3://{bucket}/{object_name}"
        if not uploaded:
            pending.append(object_name)
        log.info("batch %d: %d rows -> %s%s", outcome.batches, len(records), object_name, "" if uploaded else " (pending)")

    def _update_watermark(key: str, value: str) -> None:
        state.update_watermark(key, plan.watermark_field or "", value, run_id)
        log.info("watermark %s -> %s", key, value)

    try:
        connection = b1_source.open_connection(config.source)
        try:
            result = b1_reader.read_entity(
                plan,
                connection,
                config.source.companies,
                mode=effective_mode,
                get_watermark=state.get_watermark,
                update_watermark=_update_watermark,
                write_batch=_write_batch,
                from_date=from_date,
                to_date=to_date,
            )
        finally:
            connection.close()
        state.set_run_clock(run_id, result.source_clock.text() if result.source_clock else None)
        outcome.records = result.total_records
        outcome.companies = result.companies
        if pending:
            outcome.status = "failed"
            outcome.error = (
                f"{len(pending)} batch file(s) could not be uploaded and stay in the spool;"
                " the rows are safe locally and the next run retries them"
            )
            if spool.rejected:
                outcome.error += f" ({spool.rejected})"
        else:
            outcome.status = "success"
    except Exception as exc:  # noqa: BLE001 - every failure becomes a failed run
        outcome.status = "failed"
        outcome.error = scrub(f"{type(exc).__name__}: {exc}", config.secrets())
        log.error("run %s failed: %s", run_id, outcome.error)
    state.finish_run(
        run_id,
        outcome.status,
        records=outcome.records,
        batches=outcome.batches,
        storage_uri=outcome.storage_uri,
        error_message=outcome.error,
    )
    if outcome.ok:
        log.info("run %s finished: %d rows in %d batch(es)", run_id, outcome.records, outcome.batches)
    return outcome


def run_intercompany(runtime: Runtime) -> RunOutcome:
    """Write the configured intercompany mapping as the full snapshot ``IntercompanyPartners``, exactly as the cartridge's ``refresh_intercompany_partners`` does."""
    config, state, spool, log = runtime.config, runtime.state, runtime.spool, runtime.log
    entity_name = intercompany_mapping.ENTITY
    scope = config.scope
    bucket = config.upload.bucket
    run_id = state.create_run(entity_name, "full")
    outcome = RunOutcome(entity=entity_name, run_id=run_id, mode="full", status="running")
    log.info("run %s: %s (configured mapping, %d partner(s))", run_id, entity_name, len(config.intercompany))
    try:
        rows = intercompany_mapping.partner_records(config.intercompany)
        load_date, extracted_at = bronze_parquet.stamp_now()
        table = bronze_parquet.bronze_table(
            rows,
            schema=intercompany_mapping.arrow_schema(),
            entity=entity_name,
            run_id=run_id,
            load_type="full",
            watermark_field=None,
            extracted_at=extracted_at,
        )
        object_name = bronze_parquet.bronze_object_name(entity_name, scope, load_date, run_id)
        path, uploaded = spool.deliver(table, object_name, run_id=run_id, entity=entity_name)
        outcome.records = len(rows)
        outcome.batches = 1
        outcome.storage_uri = str(path) if spool.delivers_locally else f"s3://{bucket}/{object_name}"
        outcome.companies = {
            alias: {"record_count": sum(1 for p in config.intercompany if p.company == alias)}
            for alias in sorted({p.company for p in config.intercompany})
        }
        if uploaded:
            outcome.status = "success"
        else:
            outcome.status = "failed"
            outcome.error = "the mapping file could not be uploaded and stays in the spool; the next run retries it"
            if spool.rejected:
                outcome.error += f" ({spool.rejected})"
        log.info("batch 1: %d rows -> %s%s", len(rows), object_name, "" if uploaded else " (pending)")
    except Exception as exc:  # noqa: BLE001 - every failure becomes a failed run
        outcome.status = "failed"
        outcome.error = scrub(f"{type(exc).__name__}: {exc}", config.secrets())
        log.error("run %s failed: %s", run_id, outcome.error)
    state.finish_run(
        run_id,
        outcome.status,
        records=outcome.records,
        batches=outcome.batches,
        storage_uri=outcome.storage_uri,
        error_message=outcome.error,
    )
    if outcome.ok:
        log.info("run %s finished: %d rows in %d batch(es)", run_id, outcome.records, outcome.batches)
    return outcome


def _setup_logging(
    log_dir: Path, level: str, secrets: Sequence[str], quiet: bool = False
) -> list[logging.Handler]:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter(secrets)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(log_dir / LOG_NAME, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    ]
    if not quiet:
        handlers.append(logging.StreamHandler(sys.stderr))
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    for noisy in ("boto3", "botocore", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return handlers


def _bootstrap_log_dir(path: Path | None, env: Mapping[str, str]) -> Path | None:
    """Where a configuration error is logged when the configuration itself is unusable."""
    if path is None:
        return None
    base = path.resolve().parent
    agent: dict[str, Any] = {}
    parsed = False
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        parsed = True
        agent = data.get("agent") if isinstance(data.get("agent"), dict) else {}
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        pass

    def _plain(value: Any) -> str:
        text = str(value or "").strip()
        return "" if _PLACEHOLDER.fullmatch(text) else text

    state_text = _plain(env.get("OMEGA_AGENT_STATE_DIR", "")) or _plain(agent.get("state_dir"))
    if state_text:
        state_dir = (base / state_text).resolve()
    else:
        state_dir = base / "state" if parsed else base
    log_text = _plain(agent.get("log_dir"))
    return (state_dir / log_text).resolve() if log_text else state_dir / "logs"


def _env_secrets(env: Mapping[str, str]) -> tuple[str, ...]:
    names = ("SAP_B1_PASSWORD", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_ACCESS_KEY_ID", "SAP_B1_HOST")
    return tuple(env.get(name, "").strip() for name in names if env.get(name, "").strip())


def _log_config_error(path: Path | None, message: str) -> None:
    """Best effort: the scheduled task shows nothing but an exit code, so a configuration error also lands in agent.log whenever a log directory can be worked out."""
    log_dir = _bootstrap_log_dir(path, os.environ)
    if log_dir is None:
        return
    try:
        handlers = _setup_logging(log_dir, "INFO", _env_secrets(os.environ), quiet=True)
    except OSError:
        return
    try:
        logging.getLogger(AGENT_NAME).error("configuration: %s", message)
    finally:
        _teardown_logging(handlers)


def _teardown_logging(handlers: Sequence[logging.Handler]) -> None:
    root = logging.getLogger()
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()


def _open_state(config: AgentConfig) -> AgentState:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    return AgentState(config.state_db)


def _build_runtime(config: AgentConfig, log: logging.Logger, output_dir: Path | None) -> Runtime:
    state = _open_state(config)
    if output_dir is not None:
        spool = Spool(output_dir, state=None, uploader=None, log=log, secrets=config.secrets())
    else:
        config.require_upload()
        uploader = S3Uploader(config.upload, log, scope=config.scope)
        spool = Spool(config.spool_dir, state=state, uploader=uploader, log=log, secrets=config.secrets())
    return Runtime(config=config, state=state, spool=spool, log=log)


def _print(text: str, secrets: Sequence[str]) -> None:
    sys.stdout.write(scrub(text, secrets) + "\n")


def cmd_test_connection(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    config.require_scope()
    config.require_source()
    secrets = config.secrets()
    _print(f"dialect: {config.source.dialect}", secrets)
    try:
        connection = b1_source.open_connection(config.source)
    except b1_source.B1SourceError as exc:
        _print(f"source: unreachable ({exc})", secrets)
        return EXIT_FAILED
    try:
        clock = connection.source_now()
        _print(f"source clock: {clock.isoformat()}", secrets)
        for company in config.source.companies:
            sql = f'SELECT "Version" FROM {b1_source.quote_schema(company.schema)}."CINF"'
            try:
                _columns, rows = connection.fetch_all(sql)
            except b1_source.B1SourceError as exc:
                _print(f"company {company.alias}: unreachable ({exc})", secrets)
                return EXIT_FAILED
            version = rows[0][0] if rows and rows[0] else None
            _print(f"company {company.alias}: reachable, Business One version {version}", secrets)
    finally:
        connection.close()

    if args.output_dir is not None or args.skip_upload_check:
        _print("upload: check skipped", secrets)
        return EXIT_OK
    config.require_upload()
    try:
        S3Uploader(config.upload, log, scope=config.scope).probe()
    except Exception as exc:  # noqa: BLE001 - reported, never raised past the CLI
        _print(f"upload: bucket {config.upload.bucket} not reachable with this key ({type(exc).__name__}: {exc})", secrets)
        return EXIT_FAILED
    _print(f"upload: bucket {config.upload.bucket}, prefix {scoped_prefix(config.scope)} reachable", secrets)
    return EXIT_OK


def _run_entities(
    config: AgentConfig,
    log: logging.Logger,
    entities: Sequence[dict[str, Any]],
    *,
    mode: str | None,
    from_date: str | None,
    to_date: str | None,
    output_dir: Path | None,
    with_intercompany: bool = False,
) -> int:
    config.require_scope()
    config.require_source()
    runtime = _build_runtime(config, log, output_dir)
    secrets = config.secrets()
    outcomes: list[RunOutcome] = []
    try:
        with RunLock(config.state_dir / LOCK_NAME):
            drained = runtime.spool.drain()
            if drained.uploaded:
                log.info("spool: %d file(s) uploaded from earlier runs", drained.uploaded)
            if drained.pending:
                log.error("spool: %d file(s) from earlier runs still pending", drained.pending)
            if drained.lost:
                log.error("spool: %d file(s) lost (never uploaded); see status", drained.lost)
            if drained.pending >= config.max_pending_files:
                log.error(
                    "spool holds %d pending files (limit %d); not extracting more until uploads work again",
                    drained.pending,
                    config.max_pending_files,
                )
                return EXIT_FAILED
            outcomes = [
                run_extract(runtime, entity, mode=mode, from_date=from_date, to_date=to_date) for entity in entities
            ]
            if with_intercompany:
                outcomes.append(run_intercompany(runtime))
    finally:
        runtime.state.close()
    for outcome in outcomes:
        line = f"{outcome.entity}: {outcome.status} run={outcome.run_id} mode={outcome.mode} rows={outcome.records} batches={outcome.batches}"
        if outcome.error:
            line += f" error={outcome.error}"
        _print(line, secrets)
    if drained.lost:
        _print(f"spool: {drained.lost} file(s) lost, their rows never reached S3 (see status)", secrets)
    failed = [outcome for outcome in outcomes if not outcome.ok]
    if failed or drained.pending or drained.lost:
        return EXIT_FAILED
    return EXIT_OK


def cmd_extract(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    entities = select_entities(load_catalogue(), config, only=[args.entity], log=log)
    if (args.from_date or args.to_date) and args.mode:
        raise ConfigError("--mode cannot be combined with --from-date/--to-date (a date range is a historical read)")
    return _run_entities(
        config, log, entities, mode=args.mode, from_date=args.from_date, to_date=args.to_date, output_dir=args.output_dir
    )


def cmd_extract_all(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    entities = select_entities(load_catalogue(), config, only=args.entity or (), log=log)
    pending_steps = initial_load_in_progress(config)
    if pending_steps is not None:
        message = f"initial load in progress ({pending_steps}); extract-all skipped until it completes"
        log.info(message)
        _print(message, config.secrets())
        return EXIT_OK
    return _run_entities(
        config,
        log,
        entities,
        mode=args.mode,
        from_date=None,
        to_date=None,
        output_dir=args.output_dir,
        with_intercompany=not args.skip_intercompany,
    )


def cmd_refresh_intercompany(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    return _run_entities(
        config, log, [], mode=None, from_date=None, to_date=None, output_dir=args.output_dir, with_intercompany=True
    )


def month_slices(cutoff: datetime, months: int) -> list[tuple[str, str, str | None]]:
    """``(YYYY-MM, from_date, to_date)`` from the cutoff month back ``months`` months, newest first; the newest is open-ended."""
    current = date(cutoff.year, cutoff.month, 1)
    slices: list[tuple[str, str, str | None]] = []
    for index in range(months + 1):
        following = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        to_date = None if index == 0 else (following - timedelta(days=1)).isoformat()
        slices.append((current.strftime("%Y-%m"), current.isoformat(), to_date))
        current = (current - timedelta(days=1)).replace(day=1)
    return slices


@dataclass(frozen=True)
class LoadStep:
    key: str
    entity: dict[str, Any]
    mode: str | None
    from_date: str | None = None
    to_date: str | None = None
    skip_empty: bool = False


def initial_load_steps(catalogue: Sequence[dict[str, Any]], slices: Sequence[tuple[str, str, str | None]]) -> list[LoadStep]:
    """Undated tables whole first, then every dated table month by month, newest month first."""
    dated = [entity for entity in catalogue if b1_queries.plan_from_config(entity).date_field]
    undated = [entity for entity in catalogue if entity not in dated]
    steps = [LoadStep(key=f"full:{entity['entity']}", entity=entity, mode="full") for entity in undated]
    for label, from_date, to_date in slices:
        steps.extend(
            LoadStep(
                key=f"month:{label}:{entity['entity']}",
                entity=entity,
                mode=None,
                from_date=from_date,
                to_date=to_date,
                skip_empty=True,
            )
            for entity in dated
        )
    return steps


def initial_load_completed(config: AgentConfig) -> bool:
    if not config.state_db.is_file():
        return False
    state = _open_state(config)
    try:
        return bool(state.get_meta(f"{INITIAL_LOAD_META}completed_at"))
    finally:
        state.close()


def initial_load_in_progress(config: AgentConfig) -> str | None:
    """A progress text while an initial load has started and not completed, else None."""
    if not config.state_db.is_file():
        return None
    state = _open_state(config)
    try:
        if not state.get_meta(f"{INITIAL_LOAD_META}cutoff") or state.get_meta(f"{INITIAL_LOAD_META}completed_at"):
            return None
        total = state.get_meta(f"{INITIAL_LOAD_META}steps") or "?"
        return f"{len(state.step_records())} of {total} steps"
    finally:
        state.close()


def _integer_cutoffs(
    connection: "b1_source.Connection", config: AgentConfig, catalogue: Sequence[dict[str, Any]]
) -> dict[str, int]:
    """The highest integer position of every dated integer-watermark table at the cutoff, per company."""
    cutoffs: dict[str, int] = {}
    for entity in catalogue:
        plan = b1_queries.plan_from_config(entity)
        if plan.watermark_kind != b1_queries.WATERMARK_INTEGER or not plan.date_field or plan.parent:
            continue
        for company in config.source.companies:
            sql = (
                f"SELECT MAX({b1_source.quote_ident(plan.watermark_field or '')}) "
                f"FROM {b1_source.quote_schema(company.schema)}.{b1_source.quote_ident(plan.table)}"
            )
            _columns, rows = connection.fetch_all(sql)
            value = rows[0][0] if rows and rows[0] else None
            if value is not None:
                cutoffs[b1_queries.watermark_key(plan.entity, company.alias)] = int(value)
    return cutoffs


def _start_initial_load(runtime: Runtime, catalogue: Sequence[dict[str, Any]], months: int) -> tuple[datetime, dict[str, int]]:
    state, config = runtime.state, runtime.config
    stored_cutoff = state.get_meta(f"{INITIAL_LOAD_META}cutoff")
    if stored_cutoff:
        stored_months = int(state.get_meta(f"{INITIAL_LOAD_META}months") or 0)
        if stored_months != months:
            raise ConfigError(
                f"an initial load of {stored_months} months is in progress; run it with --months {stored_months} or use --restart"
            )
        cutoffs = json.loads(state.get_meta(f"{INITIAL_LOAD_META}integer_cutoffs") or "{}")
        return datetime.fromisoformat(stored_cutoff), {key: int(value) for key, value in cutoffs.items()}
    connection = b1_source.open_connection(config.source)
    try:
        cutoff = connection.source_now().replace(microsecond=0)
        cutoffs = _integer_cutoffs(connection, config, catalogue)
    finally:
        connection.close()
    state.set_meta(f"{INITIAL_LOAD_META}months", str(months))
    state.set_meta(f"{INITIAL_LOAD_META}integer_cutoffs", json.dumps(cutoffs, sort_keys=True))
    state.set_meta(f"{INITIAL_LOAD_META}cutoff", cutoff.isoformat())
    runtime.log.info("initial load started: %d months back from the source clock %s", months, cutoff.isoformat())
    return cutoff, cutoffs


def _seed_watermarks(
    runtime: Runtime, catalogue: Sequence[dict[str, Any]], cutoff: datetime, integer_cutoffs: Mapping[str, int]
) -> None:
    """Dated tables continue incrementally from the cutoff; the whole-table reads already set their own."""
    stamp = b1_queries.Watermark.from_stamp(cutoff).text()
    for entity in catalogue:
        plan = b1_queries.plan_from_config(entity)
        if not plan.date_field or plan.watermark_kind is None:
            continue
        for company in runtime.config.source.companies:
            key = b1_queries.watermark_key(plan.entity, company.alias)
            if plan.watermark_kind == b1_queries.WATERMARK_UPDATE_TS:
                value = stamp
            elif key in integer_cutoffs:
                value = b1_queries.Watermark.from_number(integer_cutoffs[key]).text()
            else:
                continue
            runtime.state.update_watermark(key, plan.watermark_field or "", value, "initial-load")


@dataclass(frozen=True)
class LoadWindow:
    start: tuple[int, int]
    end: tuple[int, int]

    @classmethod
    def parse(cls, text: str) -> "LoadWindow":
        match = re.fullmatch(r"(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})", text.strip())
        if not match:
            raise ConfigError("--window must look like 22:00-05:30")
        hours = (int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)))
        if hours[0] > 23 or hours[2] > 23 or hours[1] > 59 or hours[3] > 59 or hours[:2] == hours[2:]:
            raise ConfigError("--window must look like 22:00-05:30")
        return cls(start=hours[:2], end=hours[2:])

    def contains(self, moment: datetime) -> bool:
        now = (moment.hour, moment.minute)
        if self.start < self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end


def _local_now() -> datetime:
    return datetime.now()


def _drain_before_run(runtime: Runtime) -> DrainResult:
    config, log = runtime.config, runtime.log
    drained = runtime.spool.drain()
    if drained.uploaded:
        log.info("spool: %d file(s) uploaded from earlier runs", drained.uploaded)
    if drained.pending:
        log.error("spool: %d file(s) from earlier runs still pending", drained.pending)
    if drained.lost:
        log.error("spool: %d file(s) lost (never uploaded); see status", drained.lost)
    if drained.pending >= config.max_pending_files:
        raise AgentError(
            f"spool holds {drained.pending} pending files (limit {config.max_pending_files}); "
            "not extracting more until uploads work again"
        )
    return drained


def _report(outcome: RunOutcome, secrets: Sequence[str]) -> None:
    line = f"{outcome.entity}: {outcome.status} run={outcome.run_id} mode={outcome.mode} rows={outcome.records} batches={outcome.batches}"
    if outcome.error:
        line += f" error={outcome.error}"
    _print(line, secrets)


def cmd_initial_load(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    if not 1 <= args.months <= 120:
        raise ConfigError("--months must be between 1 and 120")
    window = LoadWindow.parse(args.window) if args.window else None
    config.require_scope()
    config.require_source()
    secrets = config.secrets()
    catalogue = select_entities(load_catalogue(), config, log=log)
    runtime = _build_runtime(config, log, args.output_dir)
    state = runtime.state
    try:
        with RunLock(config.state_dir / LOCK_NAME):
            if args.restart:
                state.reset_initial_load()
                log.info("initial load progress discarded (--restart)")
            if state.get_meta(f"{INITIAL_LOAD_META}completed_at"):
                _print(
                    f"initial load already completed at {state.get_meta(f'{INITIAL_LOAD_META}completed_at')}; "
                    "use --restart to load the history again",
                    secrets,
                )
                return EXIT_OK
            drained = _drain_before_run(runtime)
            if drained.pending or drained.lost:
                _print("spool: earlier files are pending or lost; fix uploads before the initial load (see status)", secrets)
                return EXIT_FAILED
            cutoff, integer_cutoffs = _start_initial_load(runtime, catalogue, args.months)
            steps = initial_load_steps(catalogue, month_slices(cutoff, args.months))
            state.set_meta(f"{INITIAL_LOAD_META}steps", str(len(steps)))
            done = state.step_records()
            for step in steps:
                if step.key in done:
                    continue
                if window is not None and not window.contains(_local_now()):
                    left = sum(1 for item in steps if item.key not in done)
                    _print(f"outside the load window {args.window}: {left} step(s) left; run initial-load again to resume", secrets)
                    return EXIT_OK
                outcome = run_extract(
                    runtime,
                    step.entity,
                    mode=step.mode,
                    from_date=step.from_date,
                    to_date=step.to_date,
                    skip_empty=step.skip_empty,
                )
                _report(outcome, secrets)
                if not outcome.ok:
                    _print(f"initial load stopped at {step.key}; run initial-load again to resume from there", secrets)
                    return EXIT_FAILED
                state.record_step(step.key, outcome.run_id, outcome.records)
                done[step.key] = outcome.records
            newest = month_slices(cutoff, args.months)[0]
            for entity in catalogue:
                name = entity["entity"]
                keys = [step.key for step in steps if step.entity is entity and step.key.startswith("month:")]
                if not keys or sum(done[key] for key in keys) or f"empty:{name}" in done:
                    continue
                outcome = run_extract(runtime, entity, from_date=newest[1], to_date=newest[2])
                _report(outcome, secrets)
                if not outcome.ok:
                    return EXIT_FAILED
                state.record_step(f"empty:{name}", outcome.run_id, 0)
            _seed_watermarks(runtime, catalogue, cutoff, integer_cutoffs)
            outcome = run_intercompany(runtime)
            _report(outcome, secrets)
            if not outcome.ok:
                return EXIT_FAILED
            state.set_meta(f"{INITIAL_LOAD_META}completed_at", _utc_now_text())
    finally:
        state.close()
    total = sum(done.values())
    _print(f"initial load complete: {len(steps)} steps, {total} rows, incremental reads continue from {cutoff.isoformat()}", secrets)
    return EXIT_OK


def _next_slot_seconds(now: float, minutes: int) -> float:
    slot = minutes * 60
    return (int(now // slot) + 1) * slot - now


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _refresh_secrets(secrets: Sequence[str]) -> None:
    for handler in logging.getLogger().handlers:
        if isinstance(handler.formatter, RedactingFormatter):
            handler.formatter.secrets = tuple(secrets)


def cmd_serve(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    if not 15 <= args.interval_minutes <= 1440:
        raise ConfigError("--interval-minutes must be between 15 and 1440")
    cycles = 0
    log.info("serving: one extract-all every %d minutes", args.interval_minutes)
    try:
        while True:
            try:
                current = load_config(config.config_path) if config.config_path is not None else config
                _refresh_secrets(current.secrets())
                if not initial_load_completed(current):
                    pending_steps = initial_load_in_progress(current)
                    log.info(
                        "cycle skipped: %s",
                        f"initial load in progress ({pending_steps})" if pending_steps else "run initial-load first",
                    )
                else:
                    code = _run_entities(
                        current,
                        log,
                        select_entities(load_catalogue(), current, log=log),
                        mode=None,
                        from_date=None,
                        to_date=None,
                        output_dir=args.output_dir,
                        with_intercompany=True,
                    )
                    log.info("cycle finished with exit code %d", code)
            except ConfigError as exc:
                log.error("configuration: %s", exc)
            except AgentError as exc:
                log.error("%s", exc)
            except Exception as exc:  # noqa: BLE001 - a service keeps running; the next cycle tries again
                log.exception("unexpected failure: %s: %s", type(exc).__name__, exc)
            cycles += 1
            if args.cycles and cycles >= args.cycles:
                return EXIT_OK
            wait = _next_slot_seconds(time.time(), args.interval_minutes)
            log.info("next cycle in %d seconds", int(wait))
            _sleep(wait)
    except KeyboardInterrupt:
        log.info("stopping")
        return EXIT_OK


def cmd_inventory(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    if not 1 <= args.months <= 120:
        raise ConfigError("--months must be between 1 and 120")
    config.require_source()
    secrets = config.secrets()
    catalogue = select_entities(load_catalogue(), config, only=args.entity or (), log=log)
    connection = b1_source.open_connection(config.source)
    failures = 0
    try:
        clock = connection.source_now().replace(microsecond=0)
        window_start = month_slices(clock, args.months)[-1][1]
        entities: list[dict[str, Any]] = []
        for entity in catalogue:
            plan = b1_queries.plan_from_config(entity)
            counted = dataclasses.replace(plan, primary_key=())
            companies: dict[str, dict[str, Any]] = {}
            for company in config.source.companies:
                try:
                    sql, params = b1_queries.select_sql(
                        counted,
                        company.schema,
                        mode="historical" if plan.date_field else "full",
                        from_date=window_start if plan.date_field else None,
                    )
                    _columns, rows = connection.fetch_all(f"SELECT COUNT(*) FROM ({sql}) counted", params)
                    companies[company.alias] = {"rows": int(rows[0][0])}
                except b1_source.B1SourceError as exc:
                    failures += 1
                    companies[company.alias] = {"error": scrub(str(exc), secrets)}
            entities.append(
                {"entity": plan.entity, "from_date": window_start if plan.date_field else None, "companies": companies}
            )
    finally:
        connection.close()
    report = {
        "agent_version": AGENT_VERSION,
        "source_clock": clock.isoformat(),
        "months": args.months,
        "window_start": window_start,
        "failures": failures,
        "entities": entities,
    }
    if args.json:
        _print(json.dumps(report, indent=2, ensure_ascii=True), secrets)
    else:
        _print(f"source clock {report['source_clock']}; dated tables counted from {window_start}", secrets)
        for item in entities:
            cells = []
            for alias, value in item["companies"].items():
                cells.append(f"{alias}={value['rows']}" if "rows" in value else f"{alias}=ERROR {value['error']}")
            _print(f"  {item['entity']:<22} {'window' if item['from_date'] else 'whole '} {' '.join(cells)}", secrets)
    return EXIT_FAILED if failures else EXIT_OK


def cmd_status(config: AgentConfig, log: logging.Logger, args: argparse.Namespace) -> int:
    secrets = config.secrets()
    state = _open_state(config)
    try:
        runs = state.list_runs(args.limit)
        watermarks = state.list_watermarks()
        pending = state.spool_pending()
        lost = state.spool_lost()
    finally:
        state.close()
    report = {
        "agent_version": AGENT_VERSION,
        "state_dir": str(config.state_dir),
        "spool_pending": len(pending),
        "spool_lost": len(lost),
        "pending_files": pending,
        "lost_files": lost,
        "watermarks": watermarks,
        "runs": runs,
    }
    if args.json:
        _print(json.dumps(report, indent=2, ensure_ascii=True, default=str), secrets)
        return EXIT_OK
    _print(f"agent {AGENT_VERSION}; state {config.state_dir}; spool pending: {len(pending)}; lost: {len(lost)}", secrets)
    for row in pending:
        line = f"  pending {row['object_name']} attempts={row['attempts']}"
        if row.get("last_error"):
            line += f" error={row['last_error']}"
        _print(line, secrets)
    for row in lost:
        _print(f"  LOST {row['object_name']} ({row['reason']}; {row['lost_at']}) - re-extract the table", secrets)
    _print("watermarks:", secrets)
    for row in watermarks or []:
        _print(f"  {row['entity_name']}: {row['last_watermark_value']} (run {row['last_run_id']}, {row['updated_at']})", secrets)
    if not watermarks:
        _print("  (none yet)", secrets)
    _print(f"last {len(runs)} run(s):", secrets)
    for row in runs:
        line = (
            f"  {row['started_at']} {row['entity_name']:<6} {row['run_type']:<11} {row['status']:<8}"
            f" rows={row['records_extracted'] if row['records_extracted'] is not None else '-'}"
        )
        if row.get("error_message"):
            line += f" error={row['error_message']}"
        _print(line, secrets)
    if not runs:
        _print("  (none yet)", secrets)
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=None, help=f"agent.toml (default: ${CONFIG_ENV} or ./agent.toml)")
    parser.add_argument("--quiet", action="store_true", help="do not echo the log to stderr")
    parser.add_argument("--version", action="version", version=f"{AGENT_NAME} {AGENT_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("test-connection", help="open the source, read CINF in every company, probe the bucket")
    probe.add_argument("--output-dir", type=Path, default=None, help="local delivery: skip the bucket probe")
    probe.add_argument("--skip-upload-check", action="store_true")

    extract = sub.add_parser("extract", help="read one entity across every company")
    extract.add_argument("--entity", required=True)
    extract.add_argument("--mode", choices=("full", "incremental"), default=None, help="default: the catalogue's mode")
    extract.add_argument("--from-date", default=None, help="ISO date: historical read from this document date")
    extract.add_argument("--to-date", default=None, help="ISO date: historical read up to this document date (inclusive)")
    extract.add_argument("--output-dir", type=Path, default=None, help="write files here instead of uploading")

    extract_all = sub.add_parser("extract-all", help="read every configured entity, one run each")
    extract_all.add_argument("--entity", action="append", default=None, help="restrict to these entities (repeatable)")
    extract_all.add_argument("--mode", choices=("full", "incremental"), default=None)
    extract_all.add_argument("--output-dir", type=Path, default=None)
    extract_all.add_argument(
        "--skip-intercompany", action="store_true", help="do not rewrite the IntercompanyPartners mapping at the end"
    )

    refresh = sub.add_parser(
        "refresh-intercompany", help="write the configured intercompany mapping (IntercompanyPartners) and nothing else"
    )
    refresh.add_argument("--output-dir", type=Path, default=None)

    initial_load = sub.add_parser("initial-load", help="load the history month by month, then continue incrementally")
    initial_load.add_argument("--months", type=int, default=DEFAULT_HISTORY_MONTHS)
    initial_load.add_argument("--window", default=None, help="local HH:MM-HH:MM; stop between steps outside it")
    initial_load.add_argument("--restart", action="store_true", help="discard the progress of an earlier initial load")
    initial_load.add_argument("--output-dir", type=Path, default=None)

    serve = sub.add_parser("serve", help="run extract-all on a fixed interval until stopped (the Windows service)")
    serve.add_argument("--interval-minutes", type=int, default=DEFAULT_SERVE_MINUTES)
    serve.add_argument("--cycles", type=int, default=0, help=argparse.SUPPRESS)
    serve.add_argument("--output-dir", type=Path, default=None)

    inventory = sub.add_parser("inventory", help="count the rows of every table per company, without extracting")
    inventory.add_argument("--months", type=int, default=DEFAULT_HISTORY_MONTHS)
    inventory.add_argument("--entity", action="append", default=None)
    inventory.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="watermarks, last runs and pending uploads")
    status.add_argument("--limit", type=int, default=20)
    status.add_argument("--json", action="store_true")
    return parser


def _config_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit
    from_env = os.environ.get(CONFIG_ENV, "").strip()
    if from_env:
        return Path(from_env)
    default = Path.cwd() / "agent.toml"
    return default if default.is_file() else None


COMMANDS = {
    "test-connection": cmd_test_connection,
    "extract": cmd_extract,
    "extract-all": cmd_extract_all,
    "refresh-intercompany": cmd_refresh_intercompany,
    "initial-load": cmd_initial_load,
    "serve": cmd_serve,
    "inventory": cmd_inventory,
    "status": cmd_status,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = _config_path(args.config)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        sys.stderr.write(f"{AGENT_NAME}: {exc}\n")
        _log_config_error(config_path, str(exc))
        return EXIT_CONFIG
    handlers = _setup_logging(config.log_dir, config.log_level, config.secrets(), quiet=args.quiet)
    log = logging.getLogger(AGENT_NAME)
    try:
        return COMMANDS[args.command](config, log, args)
    except ConfigError as exc:
        log.error("configuration: %s", exc)
        return EXIT_CONFIG
    except AgentError as exc:
        log.error("%s", exc)
        return EXIT_FAILED
    except KeyboardInterrupt:
        log.error("interrupted")
        return EXIT_FAILED
    except Exception as exc:  # noqa: BLE001 - the traceback goes through the scrubbing formatter, never raw
        log.exception("unexpected failure: %s: %s", type(exc).__name__, exc)
        return EXIT_FAILED
    finally:
        _teardown_logging(handlers)


if __name__ == "__main__":
    sys.exit(main())
