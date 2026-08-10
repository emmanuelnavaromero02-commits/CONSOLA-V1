from __future__ import annotations

import json

from refinement.app import publication_verifier_worker


class _CapturingConnection:
    def __init__(self, error: ConnectionError | None = None) -> None:
        self.error = error
        self.payload = b""

    def sendall(self, payload: bytes) -> None:
        if self.error is not None:
            raise self.error
        self.payload = payload


def test_send_result_serializes_successful_response() -> None:
    connection = _CapturingConnection()

    assert publication_verifier_worker._send_result(connection, {"ok": True}) is True
    assert json.loads(connection.payload) == {"ok": True}


def test_send_result_tolerates_client_disconnect_without_killing_worker() -> None:
    connection = _CapturingConnection(BrokenPipeError("client disconnected"))

    assert publication_verifier_worker._send_result(connection, {"ok": False}) is False
