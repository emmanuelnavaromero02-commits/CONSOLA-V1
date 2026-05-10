from __future__ import annotations

import sys
from unittest.mock import MagicMock
sys.modules['yaml'] = MagicMock()
sys.modules['cryptography'] = MagicMock()
sys.modules['cryptography.fernet'] = MagicMock()
sys.modules['pydantic'] = MagicMock()
sys.modules['pydantic_settings'] = MagicMock()

import pytest
import builtins

def get_secret_mock(key, default=None):
    return "change-this-key-in-prod"

# The module protection_service imports get_secret from app.core.vault_client locally.
# Let's mock the module app.core.vault_client itself.
vault_client_mock = MagicMock()
vault_client_mock.get_secret = get_secret_mock
sys.modules['app.core.vault_client'] = vault_client_mock

from app.services.protection_service import _build_fernet

def test_build_fernet_fails_on_default_key():
    with pytest.raises(RuntimeError, match="field_encryption_key no configurado"):
        _build_fernet()
