"""
Bootstrap (or update) an admin user.

Usage (run inside the console container):
    docker compose exec -e BOOTSTRAP_ADMIN_PASSWORD='...' console \
      python -m app.bootstrap_admin <email>

If the user already exists, password is reset and role is set to 'admin'.
"""
import asyncio
import getpass
import os
import sys

from app.services import auth as _auth


async def main(email: str, password: str, name: str | None = None):
    existing = await _auth.get_user_by_email(email)
    if existing:
        await _auth.update_user(
            existing["id"], role="admin", is_active=True, password=password,
            name=name if name else existing.get("name"),
        )
        print(f"Updated existing user {email} → role=admin, password reset.")
    else:
        u = await _auth.create_user(email=email, password=password, name=name, role="admin")
        print(f"Created admin user id={u['id']} email={u['email']}")


# v1.43.4 (Claude H5 / Security R2): refuse the placeholder values
# documented in infra/.env.example so an operator who runs
# ``docker compose up`` against an unrotated .env can't ship an
# admin account with a publicly-known password. The list is the
# literal placeholder strings from the .env.example commit; rotate
# this list every time the placeholders change.
_PLACEHOLDER_PASSWORDS = frozenset({
    "ChangeMeFirstBoot123!",
    "change-me",
    "changeme",
    "password",
    "admin",
})


def _read_password() -> str:
    password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
    if password is None:
        password = getpass.getpass("Bootstrap admin password: ")
    if not password:
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_PASSWORD is empty. Set BOOTSTRAP_ADMIN_PASSWORD "
            "or enter a non-empty password interactively."
        )
    if password.strip() in _PLACEHOLDER_PASSWORDS:
        raise RuntimeError(
            "BOOTSTRAP_ADMIN_PASSWORD is set to a documented placeholder "
            f"value ({password!r}). The placeholders in infra/.env.example "
            "are intentionally invalid so an unrotated .env can't boot. "
            "Generate a real password (e.g. `openssl rand -base64 24`) "
            "and re-run."
        )
    if len(password) < 12:
        raise RuntimeError(
            f"BOOTSTRAP_ADMIN_PASSWORD is only {len(password)} characters. "
            "Bootstrap admin requires at least 12 characters; the account "
            "lands with role=admin, so a weak password here is the worst "
            "case. Generate a stronger one and re-run."
        )
    return password


def _usage() -> str:
    return (
        "Usage: python -m app.bootstrap_admin <email>\n"
        "Set BOOTSTRAP_ADMIN_PASSWORD for non-interactive use. "
        "Optional display name: BOOTSTRAP_ADMIN_NAME."
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(_usage(), file=sys.stderr)
        print("Refusing password via argv because process arguments are visible via ps.", file=sys.stderr)
        sys.exit(2)
    email    = sys.argv[1]
    password = _read_password()
    name     = os.environ.get("BOOTSTRAP_ADMIN_NAME")
    asyncio.run(main(email, password, name))
