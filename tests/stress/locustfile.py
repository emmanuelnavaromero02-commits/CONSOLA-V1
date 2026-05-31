from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from gevent.lock import Semaphore
from locust import HttpUser, between, task


ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")

ENABLE_WRITES = os.environ.get("OMEGA_STRESS_ENABLE_WRITES", "").strip().lower() in {"1", "true", "yes"}
ENABLE_COPILOT_WRITES = os.environ.get("OMEGA_STRESS_ENABLE_COPILOT_WRITES", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
REQUIRE_HUBSPOT_OK = os.environ.get("OMEGA_STRESS_REQUIRE_HUBSPOT_OK", "1").strip().lower() in {
    "1",
    "true",
    "yes",
}
POLL_EXTRACT_JOBS = os.environ.get("OMEGA_STRESS_POLL_JOBS", "1").strip().lower() in {"1", "true", "yes"}
ENABLE_GOLD_REFRESH = os.environ.get("OMEGA_STRESS_ENABLE_GOLD_REFRESH", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
CONCURRENT_WRITES = os.environ.get("OMEGA_STRESS_CONCURRENT_WRITES", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
EXTRACT_INTERVAL_SECONDS = float(os.environ.get("OMEGA_STRESS_EXTRACT_INTERVAL_SECONDS", "300"))
GOLD_REFRESH_INTERVAL_SECONDS = float(os.environ.get("OMEGA_STRESS_GOLD_REFRESH_INTERVAL_SECONDS", "300"))
FORGED_WORKSPACE_ID = os.environ.get("OMEGA_STRESS_FORGED_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001")
WAIT_MIN = float(os.environ.get("OMEGA_STRESS_WAIT_MIN", "0.1"))
WAIT_MAX = float(os.environ.get("OMEGA_STRESS_WAIT_MAX", "0.5"))

WRITE_LOCK = Semaphore()
LAST_EXTRACT_AT = 0.0
LAST_GOLD_REFRESH_AT = 0.0
WRITE_WARMUP_COMPLETE = False
WRITE_WARMUP_ERROR: str | None = None


OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "components": {
        "schemas": {
            "StressEntity": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "amount": {"type": "number"},
                    "closed_at": {"type": "string", "format": "date-time"},
                },
            }
        }
    },
}


class OmegaStressUser(HttpUser):
    wait_time = between(WAIT_MIN, WAIT_MAX)

    csrf_token: str = ""

    def on_start(self) -> None:
        if not ADMIN_PASSWORD:
            raise RuntimeError("E2E_ADMIN_PASSWORD is required for stress login")
        self._login()
        if ENABLE_WRITES:
            self._ensure_write_data_ready()

    def _cookie_value(self, *names: str) -> str:
        for cookie in self.client.cookies:
            if cookie.name in names:
                return cookie.value
        return ""

    def _mirror_cookie(self, name: str, value: str | None) -> None:
        if not value:
            return
        # The local stack may emit Secure cookies even on http://localhost to
        # mimic production. Requests will not replay Secure cookies over HTTP,
        # so stress mirrors them as host-only test cookies.
        self.client.cookies.set(name, value, path="/")

    def _csrf_headers(self) -> dict[str, str]:
        token = self.csrf_token or self._cookie_value("csrf_token", "csrftoken")
        return {"X-CSRF-Token": token} if token else {}

    def _login(self) -> None:
        with self.client.get("/login", name="auth:get-login", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"GET /login returned {response.status_code}")
                raise RuntimeError("login page unavailable")
            self.csrf_token = response.cookies.get("csrf_token") or self._cookie_value("csrf_token", "csrftoken")
            self._mirror_cookie("csrf_token", self.csrf_token)

        with self.client.post(
            "/api/auth/login",
            name="auth:login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers=self._csrf_headers(),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"login failed {response.status_code}: {response.text[:200]}")
                raise RuntimeError("admin login failed")
            for cookie_name in ("csrf_token", "mod_session", "refresh_token"):
                self._mirror_cookie(cookie_name, response.cookies.get(cookie_name) or self._cookie_value(cookie_name))
            self.csrf_token = response.cookies.get("csrf_token") or self._cookie_value("csrf_token", "csrftoken")

    def _get_json(self, path: str, *, name: str, expected_status: set[int] | None = None) -> Any:
        expected = expected_status or {200}
        with self.client.get(path, name=name, catch_response=True) as response:
            if response.status_code not in expected:
                response.failure(f"{path} returned {response.status_code}: {response.text[:200]}")
                return None
            try:
                payload = response.json()
            except Exception as exc:
                response.failure(f"{path} returned non-json: {type(exc).__name__}")
                return None
            response.success()
            return payload

    def _post_json(
        self,
        path: str,
        *,
        name: str,
        body: dict[str, Any],
        expected_status: set[int] | None = None,
        timeout_name: str | None = None,
    ) -> Any:
        expected = expected_status or {200}
        with self.client.post(
            path,
            name=timeout_name or name,
            json=body,
            headers=self._csrf_headers(),
            catch_response=True,
        ) as response:
            if response.status_code not in expected:
                response.failure(f"{path} returned {response.status_code}: {response.text[:200]}")
                return None
            try:
                payload = response.json()
            except Exception as exc:
                response.failure(f"{path} returned non-json: {type(exc).__name__}")
                return None
            response.success()
            return payload

    def _ensure_write_data_ready(self) -> None:
        """Prime write-mode dependencies before read tasks start.

        Locust schedules task methods randomly. When write mode resets local
        HubSpot derived files, Gold reads must not race ahead of the first
        extraction/refresh cycle or the test measures script ordering instead
        of product behavior.
        """
        global WRITE_WARMUP_COMPLETE, WRITE_WARMUP_ERROR
        if WRITE_WARMUP_COMPLETE:
            if WRITE_WARMUP_ERROR:
                raise RuntimeError(WRITE_WARMUP_ERROR)
            return

        with WRITE_LOCK:
            if WRITE_WARMUP_COMPLETE:
                if WRITE_WARMUP_ERROR:
                    raise RuntimeError(WRITE_WARMUP_ERROR)
                return
            try:
                self._maybe_extract_deals(force=True)
                if ENABLE_GOLD_REFRESH:
                    self._maybe_refresh_pipeline(force=True)
            except Exception as exc:
                WRITE_WARMUP_ERROR = f"write warmup failed: {exc}"
                WRITE_WARMUP_COMPLETE = True
                raise
            WRITE_WARMUP_COMPLETE = True

    def _maybe_extract_deals(self, *, force: bool = False) -> None:
        now = time.monotonic()
        should_extract = force
        global LAST_EXTRACT_AT
        if not force:
            if CONCURRENT_WRITES:
                should_extract = True
            else:
                with WRITE_LOCK:
                    if now - LAST_EXTRACT_AT >= EXTRACT_INTERVAL_SECONDS:
                        LAST_EXTRACT_AT = now
                        should_extract = True
        elif not CONCURRENT_WRITES:
            LAST_EXTRACT_AT = now

        if not should_extract:
            return

        started = self._post_json(
            "/api/mcp/servers/hubspot/invoke",
            name="hubspot:extract-deals",
            body={"tool": "extract", "args": {"entity": "deals", "mode": "full"}},
        )
        job_id = (started or {}).get("job_id")
        if job_id and POLL_EXTRACT_JOBS:
            for _ in range(10):
                status = self._post_json(
                    "/api/mcp/servers/hubspot/invoke",
                    name="hubspot:get-job-status",
                    body={"tool": "get_job_status", "args": {"job_id": job_id}},
                )
                state = str((status or {}).get("status") or "")
                if state in {"done", "failed"}:
                    if state == "failed":
                        raise RuntimeError(f"HubSpot extract failed: {status}")
                    return
                time.sleep(1)
            raise RuntimeError(f"HubSpot extract did not finish before poll limit: {job_id}")

    def _maybe_refresh_pipeline(self, *, force: bool = False) -> None:
        now = time.monotonic()
        should_refresh = force
        global LAST_GOLD_REFRESH_AT
        if not force:
            if CONCURRENT_WRITES:
                should_refresh = True
            else:
                with WRITE_LOCK:
                    if now - LAST_GOLD_REFRESH_AT >= GOLD_REFRESH_INTERVAL_SECONDS:
                        LAST_GOLD_REFRESH_AT = now
                        should_refresh = True
        elif not CONCURRENT_WRITES:
            LAST_GOLD_REFRESH_AT = now

        if not should_refresh:
            return

        payload = self._post_json(
            "/datasets/pipeline_salud/refresh",
            name="data:refresh-pipeline-salud",
            body={},
            expected_status={200},
        )
        if payload and payload.get("row_count", 0) <= 0:
            raise RuntimeError(f"pipeline_salud refresh produced no rows: {payload}")

    @task(4)
    def read_console_surfaces(self) -> None:
        self._get_json("/api/me", name="read:me")
        self._get_json("/api/cartridges", name="read:cartridges")
        self._get_json("/api/settings", name="read:settings")

    @task(5)
    def read_control_room_and_kpis(self) -> None:
        self._get_json("/api/dashboard/kpis", name="read:kpis")
        self._get_json("/api/control-room/summary", name="read:control-room-summary")
        self._get_json("/api/control-room/dashboard", name="read:control-room-dashboard")

    @task(6)
    def query_gold_dataset(self) -> None:
        payload = self._get_json("/api/data/pipeline_salud?limit=20", name="data:pipeline-salud")
        if payload is None:
            return
        if not isinstance(payload, list):
            raise RuntimeError(f"pipeline_salud response is not a list: {type(payload).__name__}")

    @task(2)
    def read_semantic_and_catalog(self) -> None:
        self._get_json("/api/semantic?cartridge=hubspot", name="read:semantic-hubspot")
        self._get_json("/api/catalog?cartridge=hubspot", name="read:catalog-hubspot")
        self._get_json("/api/datasets/pipeline_salud/lineage", name="read:lineage-pipeline-salud")

    @task(2)
    def studio_introspection(self) -> None:
        payload = self._post_json(
            "/api/studio/introspect-source",
            name="studio:introspect-openapi",
            body={
                "cartridge_id": "hubspot",
                "source_kind": "openapi",
                "spec_content": json.dumps(OPENAPI_SPEC),
            },
        )
        if not payload:
            return
        fields = (payload.get("entities") or [{}])[0].get("fields") or []
        by_name = {field.get("name"): field.get("type") for field in fields}
        if by_name.get("amount") != "float" or by_name.get("closed_at") != "timestamp":
            raise RuntimeError(f"studio introspection lost field types: {by_name}")

    @task(2)
    def hubspot_connection_and_freshness(self) -> None:
        payload = self._post_json(
            "/api/cartridges/hubspot/test_connection",
            name="hubspot:test-connection",
            body={},
        )
        if REQUIRE_HUBSPOT_OK and payload and payload.get("ok") is not True:
            raise RuntimeError(f"HubSpot test_connection not ok: {payload}")
        self._get_json("/api/freshness/hubspot", name="read:freshness-hubspot")

    @task(1)
    def copilot_read_surfaces(self) -> None:
        self._get_json("/api/copilot/memory", name="copilot:memory")
        if not ENABLE_COPILOT_WRITES:
            return
        marker = uuid.uuid4().hex[:8]
        self._post_json(
            "/api/copilot/drafts",
            name="copilot:create-draft",
            body={
                "kind": "memo",
                "title": f"Stress {marker}",
                "body": "Stress draft generated by Locust.",
                "tone": "neutral",
            },
        )
        workflow = self._post_json(
            "/api/copilot/workflow",
            name="copilot:create-workflow",
            body={"intent": f"Stress workflow {marker}"},
        )
        workflow_id = ((workflow or {}).get("workflow") or {}).get("id")
        if workflow_id:
            self._get_json(f"/api/copilot/workflow/{workflow_id}/status", name="copilot:workflow-status")

    @task(1)
    def forged_workspace_isolation_probe(self) -> None:
        with self.client.get(
            "/api/data/pipeline_salud?limit=1",
            name="security:forged-workspace-data",
            headers={"x-workspace-id": FORGED_WORKSPACE_ID},
            catch_response=True,
        ) as response:
            if response.status_code in {401, 403, 404, 422}:
                response.success()
                return
            if response.status_code != 200:
                response.failure(f"unexpected status {response.status_code}: {response.text[:200]}")
                return
            try:
                payload = response.json()
            except Exception as exc:
                response.failure(f"non-json isolation response: {type(exc).__name__}")
                return
            if isinstance(payload, list) and payload:
                response.failure("forged workspace returned rows")
                return
            response.success()

    @task(1)
    def optional_write_load(self) -> None:
        if not ENABLE_WRITES:
            self._get_json("/healthz", name="read:healthz")
            return

        self._maybe_extract_deals()

        if not ENABLE_GOLD_REFRESH:
            return

        self._maybe_refresh_pipeline()
