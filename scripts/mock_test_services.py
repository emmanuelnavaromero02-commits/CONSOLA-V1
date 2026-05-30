from __future__ import annotations

import argparse
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _schema_response(args: dict) -> dict:
    cartridge_id = args.get("cartridge_id")
    entity = args.get("entity")
    if cartridge_id == "replicon" and entity == "User":
        return {"result": {"entity": "User"}}
    if cartridge_id == "sap_hcm" and entity in {"EmployeeActions", "HRPA_EE_PA_SRV/PA0000Set", "PA0000Set"}:
        return {
            "result": {
                "entity": "EmployeeActions",
                "odata_entity": "HRPA_EE_PA_SRV/PA0000Set",
            }
        }
    return {"result": {"error": f"entity '{entity}' not found"}}


class MockHandler(BaseHTTPRequestHandler):
    server_version = "OmegaHermeticMock/1.0"

    def _request_id(self) -> str:
        return self.headers.get("X-Request-ID") or str(uuid.uuid4())

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Request-ID", self._request_id())
        self.end_headers()
        self.wfile.write(body)

    def _has_auth(self) -> bool:
        return bool(self.headers.get("X-Api-Key") and self.headers.get("X-Internal-Service"))

    def _has_valid_internal_auth(self) -> bool:
        return (
            bool(self.headers.get("X-Api-Key"))
            and self.headers.get("X-Api-Key") != "not-the-real-one"
            and self.headers.get("X-Internal-Service") == "console"
        )

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/health", "/healthz"}:
            self._send_json(200, {"ok": True, "status": "ok", "service": self.server.kind})
            return
        if self.path in {"/mcp/tools", "/skills/entities"}:
            self._send_json(401, {"detail": "Authentication required"})
            return
        self._send_json(404, {"detail": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path in {"/mcp/invoke", "/skills/test_connection"}:
            if not self._has_auth():
                self._send_json(401, {"detail": "Authentication required"})
                return
            if not self._has_valid_internal_auth():
                self._send_json(403, {"detail": "Forbidden"})
                return
        if self.path == "/mcp/invoke":
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
            except Exception:
                body = {}
            if body.get("tool") == "cartridge_get_schema":
                self._send_json(200, _schema_response(body.get("args") or {}))
                return
            self._send_json(200, {"result": {}})
            return
        if self.path == "/skills/test_connection":
            self._send_json(401, {"detail": "Authentication required"})
            return
        self._send_json(404, {"detail": "Not found"})

    def log_message(self, fmt: str, *args) -> None:
        return


def _serve(kind: str, port: int) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), MockHandler)
    server.kind = kind
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True)
    parser.add_argument("--ports", required=True)
    args = parser.parse_args()
    ports = [int(item.strip()) for item in args.ports.split(",") if item.strip()]
    threads = [
        threading.Thread(target=_serve, args=(args.kind, port), daemon=True)
        for port in ports
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


if __name__ == "__main__":
    main()
