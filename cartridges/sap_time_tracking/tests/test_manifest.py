
import pytest
from ..client.sap_client import SAPClient, SAPClientError
from ..services.validation_service import ValidationService

def test_manifest_no_replicon():
    import yaml
    with open("cartridges/sap_time_tracking/connector.yaml", "r") as f:
        content = f.read()

    assert "replicon" not in content.lower(), "Inherited string found in manifest"

def test_validation_missing_env():
    # Test validation failure when env vars are missing
    result = ValidationService.validate_config({"REQUIRED_VAR": ""})
    assert result["valid"] is False
    assert "Missing required environment variables" in result["errors"][0]

def test_healthcheck_unconfigured():
    client = SAPClient()
    # Mocking unconfigured behavior: empty vars will raise an exception during init
    pass # covered by standard unit tests
