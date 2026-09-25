from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from gevent.lock import RLock
from locust import HttpUser, between, task


ADMIN_EMAIL = os.environ.get("E2E_ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("E2E_ADMIN_PASSWORD", "")
LOGIN_PATH = os.environ.get("OMEGA_STRESS_LOGIN_PATH", "/auth/login")
SESSION_COOKIES = os.environ.get("OMEGA_STRESS_SESSION_COOKIES", "").strip()
SESSION_COOKIE_FILE = os.environ.get("OMEGA_STRESS_SESSION_COOKIE_FILE", "").strip()
SESSION_BEARER_TOKEN = os.environ.get("OMEGA_STRESS_BEARER_TOKEN", "").strip()

ENABLE_WRITES = os.environ.get("OMEGA_STRESS_ENABLE_WRITES", "").strip().lower() in {"1", "true", "yes"}
ENABLE_COPILOT_WRITES = os.environ.get("OMEGA_STRESS_ENABLE_COPILOT_WRITES", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
REQUIRE_LIVE_LLM = os.environ.get("OMEGA_STRESS_REQUIRE_LIVE_LLM", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
REQUIRE_HUBSPOT_OK = os.environ.get("OMEGA_STRESS_REQUIRE_HUBSPOT_OK", "1").strip().lower() in {
    "1",
    "true",
    "yes",
}
WORKLOAD = os.environ.get("OMEGA_STRESS_WORKLOAD", "hubspot").strip().lower()
REQUIRE_SF_OK = os.environ.get("OMEGA_STRESS_REQUIRE_SF_OK", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
ENABLE_SF_REFRESH = os.environ.get("OMEGA_STRESS_ENABLE_SF_REFRESH", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
SF_CONN_ID = os.environ.get("OMEGA_STRESS_SF_CONN_ID", "tenant_sf")
SF_BRONZE_SOURCE = os.environ.get("OMEGA_STRESS_SF_BRONZE_SOURCE", "raw/sap_successfactors/PerPerson")
SF_GOLD_DATASETS = tuple(
    item.strip()
    for item in os.environ.get(
        "OMEGA_STRESS_SF_GOLD_DATASETS",
        "sap_successfactors_employee_360,"
        "sap_successfactors_headcount_by_department,"
        "sap_successfactors_org_structure,"
        "sap_successfactors_manager_hierarchy",
    ).split(",")
    if item.strip()
)
POLL_EXTRACT_JOBS = os.environ.get("OMEGA_STRESS_POLL_JOBS", "1").strip().lower() in {"1", "true", "yes"}
JOB_POLL_ATTEMPTS = int(os.environ.get("OMEGA_STRESS_JOB_POLL_ATTEMPTS", "30"))
JOB_POLL_INTERVAL_SECONDS = float(os.environ.get("OMEGA_STRESS_JOB_POLL_INTERVAL_SECONDS", "1"))
ENABLE_GOLD_REFRESH = os.environ.get("OMEGA_STRESS_ENABLE_GOLD_REFRESH", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
ENABLE_INTERNAL_PROBES = os.environ.get("OMEGA_STRESS_ENABLE_INTERNAL_PROBES", "").strip().lower() in {
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

WRITE_LOCK = RLock()
LAST_EXTRACT_AT = 0.0
LAST_GOLD_REFRESH_AT = 0.0
EXTRACT_IN_FLIGHT = False
GOLD_REFRESH_IN_FLIGHT = False
WRITE_WARMUP_COMPLETE = False
WRITE_WARMUP_ERROR: str | None = None
LIVE_LLM_PROBE_COMPLETE = False
LIVE_LLM_PROBE_ERROR: str | None = None


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

HTML_PAGES = (
    ("/", "page:home"),
    ("/marketplace", "page:marketplace"),
    ("/studio", "page:studio"),
    ("/control-room", "page:control-room"),
    ("/workspace", "page:workspace"),
    ("/operations/vault", "page:vault"),
    ("/data/bronze", "page:bronze-query"),
    ("/viewer?type=schema", "page:schema-viewer"),
    ("/viewer?type=pipeline&cartridge=sap_successfactors", "page:sf-pipeline"),
    ("/explorer", "page:explorer"),
)

INTERNAL_SERVICE_ENDPOINTS = (
    ("workspace", "http://127.0.0.1:8001/healthz"),
    ("mcp-infra", "http://127.0.0.1:8010/healthz"),
    ("refinement", "http://127.0.0.1:8500/healthz"),
    ("vault", "http://127.0.0.1:8300/healthz"),
    ("hubspot", "http://127.0.0.1:8210/healthz"),
    ("replicon", "http://127.0.0.1:8201/healthz"),
    ("sap-hcm", "http://127.0.0.1:8202/healthz"),
    ("sap-successfactors", "http://127.0.0.1:8203/healthz"),
    ("sap-s4hana", "http://127.0.0.1:8204/healthz"),
    ("airflow", "http://127.0.0.1:8082/health"),
    ("superset", "http://127.0.0.1:8088/health"),
    ("minio", "http://127.0.0.1:9000/minio/health/live"),
    ("mailhog", "http://127.0.0.1:8025/api/v2/messages"),
)


class OmegaStressUser(HttpUser):
    wait_time = between(WAIT_MIN, WAIT_MAX)

    csrf_token: str = ""
    bearer_token: str = ""

    def on_start(self) -> None:
        if not self._bootstrap_session_from_env() and not ADMIN_PASSWORD:
            raise RuntimeError("E2E_ADMIN_PASSWORD is required for stress login")
        if not self.csrf_token and not self.bearer_token:
            self._login()
        if REQUIRE_LIVE_LLM:
            self._ensure_live_llm_ready()
        self._probe_console_pages()
        if ENABLE_INTERNAL_PROBES:
            self._probe_internal_services()
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
        self.client.cookies.set(name, value, path="/")

    def _set_cookie_string(self, raw: str) -> None:
        for chunk in raw.split(";"):
            if "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            self._mirror_cookie(name.strip(), value.strip())

    def _bootstrap_session_from_env(self) -> bool:
        loaded = False
        if SESSION_COOKIE_FILE:
            path = Path(SESSION_COOKIE_FILE)
            if path.exists():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    raise RuntimeError(f"invalid OMEGA_STRESS_SESSION_COOKIE_FILE: {type(exc).__name__}") from exc
                cookies = payload.get("cookies") if isinstance(payload, dict) else None
                if isinstance(cookies, dict):
                    for name, value in cookies.items():
                        self._mirror_cookie(str(name), str(value))
                    loaded = True
        if SESSION_COOKIES:
            self._set_cookie_string(SESSION_COOKIES)
            loaded = True
        if SESSION_BEARER_TOKEN:
            self.bearer_token = SESSION_BEARER_TOKEN
            loaded = True
        self.csrf_token = self._cookie_value("csrf_token", "csrftoken")
        return loaded

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"} if self.bearer_token else {}

    def _csrf_headers(self) -> dict[str, str]:
        headers = self._auth_headers()
        token = self.csrf_token or self._cookie_value("csrf_token", "csrftoken")
        if token:
            headers["X-CSRF-Token"] = token
        return headers

    def _get_headers(self) -> dict[str, str]:
        return self._auth_headers()

    def _login(self) -> None:
        with self.client.get("/login", name="auth:get-login", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"GET /login returned {response.status_code}")
                raise RuntimeError("login page unavailable")
            self.csrf_token = response.cookies.get("csrf_token") or self._cookie_value("csrf_token", "csrftoken")
            self._mirror_cookie("csrf_token", self.csrf_token)

        with self.client.post(
            LOGIN_PATH,
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
        with self.client.get(path, name=name, headers=self._get_headers(), catch_response=True) as response:
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

    def _get_ok(self, path: str, *, name: str, expected_status: set[int] | None = None) -> None:
        expected = expected_status or {200}
        with self.client.get(path, name=name, headers=self._get_headers(), catch_response=True) as response:
            if response.status_code not in expected:
                response.failure(f"{path} returned {response.status_code}: {response.text[:200]}")
                return
            response.success()

    def _probe_console_pages(self) -> None:
        for path, name in HTML_PAGES:
            self._get_ok(path, name=name)

    def _probe_internal_services(self) -> None:
        self._get_json("/readyz", name="read:readyz")
        for service, url in INTERNAL_SERVICE_ENDPOINTS:
            self._get_ok(url, name=f"svc:{service}")
        self._post_json("/api/mcp/servers/health-check", name="mcp:health-check", body={})

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
                if WORKLOAD == "sap_successfactors":
                    self._maybe_refresh_successfactors_bundle(force=True)
                else:
                    self._maybe_extract_hubspot_bundle(force=True)
                if WORKLOAD != "sap_successfactors" and ENABLE_GOLD_REFRESH:
                    self._maybe_refresh_pipeline(force=True)
            except Exception as exc:
                WRITE_WARMUP_ERROR = f"write warmup failed: {exc}"
                WRITE_WARMUP_COMPLETE = True
                raise
            WRITE_WARMUP_COMPLETE = True

    def _ensure_live_llm_ready(self) -> None:
        global LIVE_LLM_PROBE_COMPLETE, LIVE_LLM_PROBE_ERROR
        if LIVE_LLM_PROBE_COMPLETE:
            if LIVE_LLM_PROBE_ERROR:
                raise RuntimeError(LIVE_LLM_PROBE_ERROR)
            return

        with WRITE_LOCK:
            if LIVE_LLM_PROBE_COMPLETE:
                if LIVE_LLM_PROBE_ERROR:
                    raise RuntimeError(LIVE_LLM_PROBE_ERROR)
                return
            try:
                payload = self._post_json(
                    "/api/copilot/drafts/generate",
                    name="copilot:live-llm-probe",
                    body={
                        "kind": "note",
                        "about": "responde una nota corta para validar el proveedor LLM vivo",
                        "tone": "neutral",
                    },
                    expected_status={200},
                )
                body = (((payload or {}).get("draft") or {}).get("body") or "").strip()
                if len(body) < 5:
                    raise RuntimeError(f"live LLM probe returned an empty draft: {payload}")
            except Exception as exc:
                LIVE_LLM_PROBE_ERROR = f"live LLM probe failed: {exc}"
                LIVE_LLM_PROBE_COMPLETE = True
                raise
            LIVE_LLM_PROBE_COMPLETE = True

    def _maybe_extract_hubspot_bundle(self, *, force: bool = False) -> None:
        now = time.monotonic()
        should_extract = force
        global EXTRACT_IN_FLIGHT, LAST_EXTRACT_AT
        if not force:
            if CONCURRENT_WRITES:
                should_extract = True
            else:
                with WRITE_LOCK:
                    if EXTRACT_IN_FLIGHT:
                        return
                    if now - LAST_EXTRACT_AT >= EXTRACT_INTERVAL_SECONDS:
                        LAST_EXTRACT_AT = now
                        EXTRACT_IN_FLIGHT = True
                        should_extract = True
        elif not CONCURRENT_WRITES:
            EXTRACT_IN_FLIGHT = True
            LAST_EXTRACT_AT = now

        if not should_extract:
            return

        try:
            started = self._post_json(
                "/api/mcp/servers/hubspot/invoke",
                name="hubspot:extract-all",
                body={"tool": "extract_all", "args": {"mode": "full"}},
            )
            job_id = (started or {}).get("job_id")
            if job_id and POLL_EXTRACT_JOBS:
                for _ in range(max(1, JOB_POLL_ATTEMPTS)):
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
                    time.sleep(max(0.1, JOB_POLL_INTERVAL_SECONDS))
                raise RuntimeError(f"HubSpot extract did not finish before poll limit: {job_id}")
        finally:
            if not CONCURRENT_WRITES:
                with WRITE_LOCK:
                    EXTRACT_IN_FLIGHT = False
                    LAST_EXTRACT_AT = time.monotonic()

    def _maybe_refresh_pipeline(self, *, force: bool = False) -> None:
        now = time.monotonic()
        should_refresh = force
        global GOLD_REFRESH_IN_FLIGHT, LAST_GOLD_REFRESH_AT
        if not force:
            if CONCURRENT_WRITES:
                should_refresh = True
            else:
                with WRITE_LOCK:
                    if GOLD_REFRESH_IN_FLIGHT:
                        return
                    if now - LAST_GOLD_REFRESH_AT >= GOLD_REFRESH_INTERVAL_SECONDS:
                        LAST_GOLD_REFRESH_AT = now
                        GOLD_REFRESH_IN_FLIGHT = True
                        should_refresh = True
        elif not CONCURRENT_WRITES:
            GOLD_REFRESH_IN_FLIGHT = True
            LAST_GOLD_REFRESH_AT = now

        if not should_refresh:
            return

        try:
            self._refresh_hubspot_silver_bundle()
            payload = self._post_json(
                "/datasets/pipeline_salud/refresh",
                name="data:refresh-pipeline-salud",
                body={},
                expected_status={200},
            )
            if payload and payload.get("row_count", 0) <= 0:
                raise RuntimeError(f"pipeline_salud refresh produced no rows: {payload}")
        finally:
            if not CONCURRENT_WRITES:
                with WRITE_LOCK:
                    GOLD_REFRESH_IN_FLIGHT = False
                    LAST_GOLD_REFRESH_AT = time.monotonic()

    def _refresh_hubspot_silver_bundle(self) -> None:
        for dataset in ("hubspot_deals_latest", "hubspot_owners_latest", "hubspot_pipelines_latest"):
            payload = self._post_json(
                f"/datasets/{dataset}/refresh",
                name=f"data:refresh-{dataset}",
                body={},
                expected_status={200},
            )
            if payload and payload.get("row_count", 0) < 0:
                raise RuntimeError(f"{dataset} refresh returned invalid row_count: {payload}")

    def _maybe_refresh_successfactors_bundle(self, *, force: bool = False) -> None:
        if not ENABLE_SF_REFRESH:
            return
        now = time.monotonic()
        should_refresh = force
        global GOLD_REFRESH_IN_FLIGHT, LAST_GOLD_REFRESH_AT
        if not force:
            if CONCURRENT_WRITES:
                should_refresh = True
            else:
                with WRITE_LOCK:
                    if GOLD_REFRESH_IN_FLIGHT:
                        return
                    if now - LAST_GOLD_REFRESH_AT >= GOLD_REFRESH_INTERVAL_SECONDS:
                        LAST_GOLD_REFRESH_AT = now
                        GOLD_REFRESH_IN_FLIGHT = True
                        should_refresh = True
        elif not CONCURRENT_WRITES:
            GOLD_REFRESH_IN_FLIGHT = True
            LAST_GOLD_REFRESH_AT = now

        if not should_refresh:
            return

        try:
            for dataset in SF_GOLD_DATASETS:
                payload = self._post_json(
                    f"/datasets/{dataset}/refresh",
                    name=f"sf:refresh-{dataset}",
                    body={},
                    expected_status={200, 202},
                )
                if payload and payload.get("row_count", 0) < 0:
                    raise RuntimeError(f"{dataset} refresh returned invalid row_count: {payload}")
        finally:
            if not CONCURRENT_WRITES:
                with WRITE_LOCK:
                    GOLD_REFRESH_IN_FLIGHT = False
                    LAST_GOLD_REFRESH_AT = time.monotonic()

    @task(4)
    def read_console_surfaces(self) -> None:
        self._get_json("/api/me", name="read:me")
        self._get_json("/api/cartridges", name="read:cartridges")
        self._get_json("/api/settings", name="read:settings")

    @task(2)
    def read_console_pages(self) -> None:
        self._probe_console_pages()

    @task(5)
    def read_control_room_and_kpis(self) -> None:
        self._get_json("/api/dashboard/kpis", name="read:kpis")
        self._get_json("/api/control-room/summary", name="read:control-room-summary")
        self._get_json("/api/control-room/dashboard", name="read:control-room-dashboard")
        if WORKLOAD == "sap_successfactors":
            self._get_json("/api/control-room/sap-successfactors/gold-kpis", name="sf:gold-kpis")

    @task(6)
    def query_gold_dataset(self) -> None:
        if WORKLOAD == "sap_successfactors":
            for dataset in SF_GOLD_DATASETS:
                payload = self._get_json(f"/api/data/{dataset}?limit=20", name=f"sf:data:{dataset}")
                if payload is None:
                    continue
                if not isinstance(payload, list):
                    raise RuntimeError(f"{dataset} response is not a list: {type(payload).__name__}")
            return
        payload = self._get_json("/api/data/pipeline_salud?limit=20", name="data:pipeline-salud")
        if payload is None:
            return
        if not isinstance(payload, list):
            raise RuntimeError(f"pipeline_salud response is not a list: {type(payload).__name__}")

    @task(2)
    def read_semantic_and_catalog(self) -> None:
        if WORKLOAD == "sap_successfactors":
            self._get_json("/api/semantic?cartridge=sap_successfactors", name="sf:semantic")
            self._get_json("/api/catalog?cartridge=sap_successfactors&layer=gold", name="sf:catalog-gold")
            self._get_json("/api/datasets/sap_successfactors_employee_360/lineage", name="sf:lineage-employee-360")
            return
        self._get_json("/api/semantic?cartridge=hubspot", name="read:semantic-hubspot")
        self._get_json("/api/catalog?cartridge=hubspot", name="read:catalog-hubspot")
        self._get_json("/api/datasets/pipeline_salud/lineage", name="read:lineage-pipeline-salud")

    @task(1)
    def service_health_surfaces(self) -> None:
        if not ENABLE_INTERNAL_PROBES:
            self._get_json("/healthz", name="read:healthz")
            return
        self._probe_internal_services()

    @task(2)
    def studio_introspection(self) -> None:
        if WORKLOAD == "sap_successfactors":
            self._get_json("/api/sources", name="sf:sources")
            self._get_json(f"/api/schema?source={SF_BRONZE_SOURCE}", name="sf:schema-source", expected_status={200})
            self._post_json(
                "/api/bronze/query",
                name="sf:bronze-query",
                body={
                    "sql": f"select * from read_parquet('{SF_BRONZE_SOURCE}') limit 20",
                    "sources": [SF_BRONZE_SOURCE],
                    "limit": 20,
                },
                expected_status={200},
            )
            return
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
        if WORKLOAD == "sap_successfactors":
            payload = self._post_json(
                f"/api/cartridges/sap_successfactors/test_connection?conn_id={SF_CONN_ID}",
                name="sf:test-connection",
                body={},
                expected_status={200},
            )
            if REQUIRE_SF_OK and payload and payload.get("ok") is not True:
                raise RuntimeError(f"SuccessFactors test_connection not ok: {payload}")
            self._get_json("/api/vault/connections/sap_successfactors", name="sf:vault-connections")
            self._get_json("/api/control-room/sap-successfactors/gold-kpis", name="sf:kpis-freshness")
            return
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
        dataset = "sap_successfactors_employee_360" if WORKLOAD == "sap_successfactors" else "pipeline_salud"
        with self.client.get(
            f"/api/data/{dataset}?limit=1",
            name="security:forged-workspace-data",
            headers={**self._get_headers(), "x-workspace-id": FORGED_WORKSPACE_ID},
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

        if WORKLOAD == "sap_successfactors":
            self._maybe_refresh_successfactors_bundle()
            return

        self._maybe_extract_hubspot_bundle()

        if not ENABLE_GOLD_REFRESH:
            return

        self._maybe_refresh_pipeline()
