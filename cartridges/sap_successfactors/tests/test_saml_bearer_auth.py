from __future__ import annotations

import base64
import hashlib
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


NS = {
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
}


def _import_client():
    root = str(Path(__file__).resolve().parents[1])
    if root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]
    from app.core import sap_client

    sap_client._make_retry_session = lambda *args, **kwargs: requests.Session()
    return sap_client


def _canonicalize(element: ET.Element) -> bytes:
    xml = ET.tostring(element, encoding="unicode")
    return ET.canonicalize(xml, strip_text=False).encode("utf-8")


def _verify_signed_assertion(assertion_b64: str, public_key, *, token_url: str, client_id: str, admin_user: str) -> None:
    assertion_xml = base64.b64decode(assertion_b64).decode("utf-8")
    root = ET.fromstring(assertion_xml)
    assert root.tag == f"{{{NS['saml']}}}Assertion"
    assert root.attrib["Version"] == "2.0"
    assert root.attrib["ID"].startswith("_")

    issuer = root.find("saml:Issuer", NS)
    assert issuer is not None
    assert issuer.text == client_id

    subject = root.find("saml:Subject/saml:NameID", NS)
    assert subject is not None
    assert subject.text == admin_user

    audience = root.find("saml:Conditions/saml:AudienceRestriction/saml:Audience", NS)
    assert audience is not None
    assert audience.text == token_url

    confirmation = root.find("saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData", NS)
    assert confirmation is not None
    assert confirmation.attrib["Recipient"] == token_url
    assert int(confirmation.attrib["NotOnOrAfter"]) > int(time.time())

    signature = root.find("ds:Signature", NS)
    assert signature is not None
    signed_info = signature.find("ds:SignedInfo", NS)
    signature_value = signature.findtext("ds:SignatureValue", namespaces=NS)
    digest_value = signature.findtext("ds:SignedInfo/ds:Reference/ds:DigestValue", namespaces=NS)
    reference = signature.find("ds:SignedInfo/ds:Reference", NS)
    assert signed_info is not None
    assert signature_value
    assert digest_value
    assert reference is not None
    assert reference.attrib["URI"] == f"#{root.attrib['ID']}"
    assert signed_info.find("ds:SignatureMethod", NS).attrib["Algorithm"].endswith("rsa-sha256")

    root_without_signature = ET.fromstring(assertion_xml)
    sig_without = root_without_signature.find("ds:Signature", NS)
    assert sig_without is not None
    root_without_signature.remove(sig_without)
    digest = hashlib.sha256(_canonicalize(root_without_signature)).digest()
    assert base64.b64encode(digest).decode("ascii") == digest_value

    public_key.verify(
        base64.b64decode(signature_value),
        _canonicalize(signed_info),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_saml_bearer_auth_posts_signed_assertion_and_caches_token(monkeypatch, tmp_path):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = tmp_path / "sf-test-private-key.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    posts: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
            body = {key: values[0] for key, values in urllib.parse.parse_qs(raw).items()}
            posts.append(body)
            payload = b'{"access_token":"saml-token","expires_in":600}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    token_url = f"http://127.0.0.1:{server.server_port}/oauth/token"

    monkeypatch.setenv("SF_BASE_URL", f"http://127.0.0.1:{server.server_port}/odata/v2")
    monkeypatch.setenv("SF_TOKEN_URL", token_url)
    monkeypatch.setenv("SF_CLIENT_ID", "sf-client-id")
    monkeypatch.setenv("SF_COMPANY_ID", "sf-company")
    monkeypatch.setenv("SF_ADMIN_USER", "admin@example.com")
    monkeypatch.setenv("SF_PRIVATE_KEY_PATH", str(key_path))

    sap_client = _import_client()
    monkeypatch.setattr(sap_client, "get_connection_for_worker", lambda _cart: {"auth_method": "saml_bearer_assertion"})
    monkeypatch.setattr(sap_client, "get_secret_for_worker", lambda *_args, **_kwargs: None)

    try:
        client = sap_client.SapSfClient()
        assert client._get_token() == "saml-token"
        assert client._get_token() == "saml-token"
    finally:
        server.shutdown()

    assert len(posts) == 1
    body = posts[0]
    assert body["grant_type"] == "urn:ietf:params:oauth:grant-type:saml2-bearer"
    assert body["company_id"] == "sf-company"
    assert body["client_id"] == "sf-client-id"
    assert "client_secret" not in body
    _verify_signed_assertion(
        body["assertion"],
        private_key.public_key(),
        token_url=token_url,
        client_id="sf-client-id",
        admin_user="admin@example.com",
    )
