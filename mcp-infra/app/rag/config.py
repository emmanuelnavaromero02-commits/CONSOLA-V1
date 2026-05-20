import os

# Compose DSN from mcp-infra's PG_* envs, falling back to DATABASE_URL if present
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
EMBED_DIM      = int(os.environ.get("EMBED_DIM",   "1024"))
BEDROCK_REGION = os.environ.get("BEDROCK_REGION") or os.environ.get("AWS_REGION") or "us-east-1"

PARENT_CHUNK_SIZE = int(os.environ.get("PARENT_CHUNK_SIZE", "3000"))
CHILD_CHUNK_SIZE  = int(os.environ.get("CHILD_CHUNK_SIZE",  "600"))
PARENT_OVERLAP    = int(os.environ.get("PARENT_OVERLAP",    "200"))
CHILD_OVERLAP     = int(os.environ.get("CHILD_OVERLAP",     "100"))
TOP_K             = int(os.environ.get("RAG_TOP_K",         "5"))
