import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


_pg_dsn_env = os.environ.get("DATABASE_URL", "").replace("postgresql+psycopg2://", "postgresql://")
if _pg_dsn_env:
    PG_DSN = _pg_dsn_env
else:
    pg_password = os.environ.get("PG_PASSWORD")
    if not pg_password:
        raise RuntimeError("PG_PASSWORD is required when DATABASE_URL is not set.")
    PG_DSN = (
        f"postgresql://{os.environ.get('PG_USER', 'postgres')}:"
        f"{pg_password}@"
        f"{os.environ.get('PG_HOST', 'postgres')}:"
        f"{os.environ.get('PG_PORT', '5432')}/"
        f"{os.environ.get('PG_DB', 'modecissions')}"
    )

EMBED_MODEL    = os.environ.get("EMBED_MODEL",     "amazon.titan-embed-text-v2:0")
EMBED_DIM      = _env_int("EMBED_DIM", 1024)
BEDROCK_REGION = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "us-east-1"

PARENT_CHUNK_SIZE = _env_int("PARENT_CHUNK_SIZE", 3000)
CHILD_CHUNK_SIZE  = _env_int("CHILD_CHUNK_SIZE", 600)
PARENT_OVERLAP    = _env_int("PARENT_OVERLAP", 200)
CHILD_OVERLAP     = _env_int("CHILD_OVERLAP", 100)
TOP_K             = _env_int("RAG_TOP_K", 5)
