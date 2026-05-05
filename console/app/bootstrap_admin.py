"""
Bootstrap (or update) an admin user.

Usage (run inside the console container):
    docker compose exec console python -m app.bootstrap_admin <email> <password> [name]

If the user already exists, password is reset and role is set to 'admin'.
"""
import asyncio
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


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m app.bootstrap_admin <email> <password> [name]", file=sys.stderr)
        sys.exit(2)
    email    = sys.argv[1]
    password = sys.argv[2]
    name     = sys.argv[3] if len(sys.argv) > 3 else None
    asyncio.run(main(email, password, name))
