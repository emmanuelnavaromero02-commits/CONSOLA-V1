from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_me_password_form_matches_backend_policy_and_refreshes_profile():
    src = read("console-next/src/components/me/MeAccessPanel.tsx")
    assert "const MIN_PASSWORD_LENGTH = 12" in src
    assert "queryClient.invalidateQueries({ queryKey: [\"me\", \"access\"] })" in src
    assert "queryClient.invalidateQueries({ queryKey: [\"me\", \"profile\"] })" in src
    assert 'href="/dashboard"' in src
    assert "contraseña temporal" in src


def test_forced_password_change_allows_me_only_until_password_update():
    main = read("console/app/main.py")
    assert '"/me", "/api/me", "/api/me/change-password", "/auth/logout", "/auth/me"' in main
    assert '"/api/me/change-password"' in main
    assert '"password change required"' in main
