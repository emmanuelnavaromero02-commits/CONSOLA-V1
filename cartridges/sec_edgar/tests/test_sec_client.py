from __future__ import annotations

import pytest
import requests

from app.core.sec_client import SECClient
from app.core.source_security import SourceSecurityError, validate_url
from app.services.config_loader import load_company_configs
from app.services.preflight_service import validate_facts_payload, validate_metadata_payload


class QueueSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def _response(status: int, payload: dict, headers: dict | None = None):
    res = requests.Response()
    res.status_code = status
    res._content = __import__("json").dumps(payload).encode()
    res.headers.update(headers or {"content-type": "application/json"})
    res.url = "https://data.sec.gov/submissions/CIK0000021344.json"
    return res


def test_client_uses_declared_user_agent_header():
    session = QueueSession([_response(200, {"cik": "0000021344", "name": "COCA COLA CO"})])
    client = SECClient(user_agent="OMEGA test contact@example.com", session=session, sleep=lambda _: None)
    client.get_metadata(["21344"])
    assert session.calls[0][1]["headers"]["User-Agent"] == "OMEGA test contact@example.com"


def test_source_security_rejects_http_and_bad_host():
    with pytest.raises(SourceSecurityError):
        validate_url("http://data.sec.gov/submissions/CIK0000021344.json")
    with pytest.raises(SourceSecurityError):
        validate_url("https://example.com/submissions/CIK0000021344.json")


def test_metadata_preflight_fails_on_name_drift():
    company = load_company_configs()[0]
    payload = {
        "sec_edgar": {
            "metadata": [
                {
                    "cik": company.cik,
                    "name": "CHANGED",
                    "tickers": [company.ticker],
                    "entity_type": company.entity_type,
                    "sic": company.sic,
                }
            ]
        }
    }
    with pytest.raises(Exception, match="metadata drift"):
        validate_metadata_payload(payload, (company,))


def test_facts_preflight_fails_when_configured_concept_missing():
    company = load_company_configs()[0]
    payload = {"sec_edgar": {"companies": [{"cik": company.cik, "raw_facts": {"facts": {}}}]}}
    with pytest.raises(Exception, match="XBRL facts missing"):
        validate_facts_payload(payload, (company,))


def test_entities_initial_allowlist_contains_acmeco_kof_and_coca_cola():
    configs = load_company_configs()
    assert {item.ticker for item in configs} == {"FMX", "KOF", "KO"}
    assert all(item.facts for item in configs)
