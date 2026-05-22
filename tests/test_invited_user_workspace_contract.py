from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_invited_users_are_assigned_to_default_workspace_on_create():
    """Invited users must not activate into a session with no workspace.

    The authenticated dependency chain resolves tenant/workspace context from
    user_workspace_roles, so invitation-based onboarding must grant the same
    initial membership as direct user creation.
    """
    source = read("console/app/services/auth.py")
    section = source.split("async def create_invited_user", 1)[1].split(
        "async def activate_user", 1
    )[0]
    assert "async with p.acquire() as conn" in section
    assert "async with conn.transaction()" in section
    assert "await _assign_default_workspace_role(conn, row[\"id\"], role, workspace_id)" in section
