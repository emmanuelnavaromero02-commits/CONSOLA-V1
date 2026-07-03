from __future__ import annotations

from collections.abc import Callable, Mapping


DEFAULT_LOCAL_ORIGINS = "http://localhost:8000,http://localhost:8001"
PRODUCTION_ENVS = {"production", "prod"}


def allowed_origins(
    environ: Mapping[str, str],
    *,
    warn: Callable[[str], None] | None = None,
    default_local_origins: str = DEFAULT_LOCAL_ORIGINS,
) -> list[str]:
    raw_env = environ.get("ALLOWED_ORIGINS")
    app_env = environ.get("APP_ENV", "production").lower()

    if raw_env is None and app_env in PRODUCTION_ENVS:
        raise RuntimeError(
            "ALLOWED_ORIGINS must be set in production (APP_ENV="
            f"{app_env}). Refusing to fall back to the localhost "
            "default with allow_credentials=True."
        )

    raw = raw_env if raw_env is not None else default_local_origins
    if raw_env is not None and not raw_env.strip():
        if app_env in PRODUCTION_ENVS:
            raise RuntimeError(
                "ALLOWED_ORIGINS is set to an empty value in production. "
                "Either unset it (we will refuse to start) or list the "
                "exact origins allowed for credentialed requests."
            )
        if warn:
            warn(
                "ALLOWED_ORIGINS is set but empty; falling back to the "
                "localhost default. This is only safe in dev/test."
            )
        raw = default_local_origins

    origins: list[str] = []
    for chunk in raw.split(","):
        origin = chunk.strip()
        if not origin:
            continue
        if origin == "*":
            if warn:
                warn(
                    "ALLOWED_ORIGINS=* is incompatible with "
                    "allow_credentials=True; ignoring wildcard entry"
                )
            continue
        origins.append(origin)
    return origins
