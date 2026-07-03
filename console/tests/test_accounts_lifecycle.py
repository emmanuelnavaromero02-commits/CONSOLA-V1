import re

import pytest
from fastapi import HTTPException

from app.domains.accounts.lifecycle import (
    normalize_email_or_400,
    password_min_length,
    safe_filename,
    token_link,
    validate_password_or_400,
    vpn_configured,
    vpn_token_link,
)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def test_normalize_email_or_400_trims_and_lowercases():
    assert (
        normalize_email_or_400("  USER@Example.COM  ", email_re=EMAIL_RE)
        == "user@example.com"
    )


def test_normalize_email_or_400_rejects_invalid_email():
    with pytest.raises(HTTPException) as excinfo:
        normalize_email_or_400("invalid", email_re=EMAIL_RE)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "invalid email"


def test_validate_password_or_400_uses_configured_length():
    assert validate_password_or_400("long-enough", min_length=8) == "long-enough"

    with pytest.raises(HTTPException) as excinfo:
        validate_password_or_400("short", min_length=8, field="new_password")

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "el password debe tener al menos 8 caracteres"


def test_password_min_length_defaults_to_twelve():
    class Auth:
        pass

    assert password_min_length(Auth()) == 12


def test_token_links_and_vpn_config_state():
    assert token_link("https://console.example/", "/activate", "tok") == (
        "https://console.example/activate?token=tok"
    )
    assert vpn_token_link("https://console.example/", "vpn-token") == (
        "https://console.example/vpn-config/vpn-token"
    )
    assert vpn_configured({"VPN_API_URL": "https://vpn", "VPN_API_PASSWORD": "x"}) is True
    assert vpn_configured({"VPN_API_URL": "https://vpn"}) is False


def test_safe_filename_removes_unsafe_characters_and_caps_length():
    assert safe_filename(" User+Name@example.com ") == "User_Name_example.com"
    assert len(safe_filename("a" * 200)) == 120
