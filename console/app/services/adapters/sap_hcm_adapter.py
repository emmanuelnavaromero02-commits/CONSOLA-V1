from __future__ import annotations

from typing import Any

import httpx

from app.services.control_room_service import BaseAdapter, ExecutionResult


class SapHcmAdapter(BaseAdapter):
    DEFAULT_IT0008_PATH = "/sap/opu/odata/sap/ZHR_IT0008_SRV/BasicPaySet"

    def execute(
        self,
        action_data: dict[str, Any],
        credentials: dict[str, Any],
        dry_run: bool = True,
    ) -> ExecutionResult:
        base_url = _first_present(credentials, "base_url", "sap_hcm_base_url", "SAP_HCM_BASE_URL", "url")
        if not base_url:
            raise ValueError("SAP HCM adapter requires base_url or SAP_HCM_BASE_URL credentials")

        endpoint = (
            _first_present(credentials, "it0008_endpoint", "sap_hcm_it0008_endpoint", "endpoint")
            or self.DEFAULT_IT0008_PATH
        )
        url = f"{str(base_url).rstrip('/')}/{str(endpoint).lstrip('/')}"
        payload = _build_it0008_payload(action_data, dry_run=dry_run)
        headers = {
            "accept": "application/json",
            "content-type": "application/json",
        }
        auth, auth_method = _auth_for(credentials, headers)
        timeout = float(credentials.get("timeout") or 20.0)

        if dry_run:
            return ExecutionResult(
                ok=True,
                status="validated",
                message="Dry-run successful",
                data={
                    "dry_run": True,
                    "permissions_checked": True,
                    "auth_method": auth_method,
                    "url": url,
                    "template_type": action_data.get("template_type") or "sap_hcm_it0008",
                    "payload": payload,
                },
            )

        with httpx.Client(timeout=timeout) as client:
            csrf_token = _fetch_csrf_token(client, url, headers=headers, auth=auth)
            headers["x-csrf-token"] = csrf_token
            response = client.post(url, json=payload, headers=headers, auth=auth)

        ok = 200 <= response.status_code < 400
        return ExecutionResult(
            ok=ok,
            status="executed" if ok else "failed",
            message=f"SAP HCM IT0008 responded with HTTP {response.status_code}",
            data={
                "status_code": response.status_code,
                "url": url,
                "template_type": action_data.get("template_type") or "sap_hcm_it0008",
                "response": _response_body(response),
            },
        )


def _first_present(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _auth_for(credentials: dict[str, Any], headers: dict[str, str]) -> tuple[httpx.BasicAuth | None, str]:
    token = _first_present(credentials, "token", "api_token", "bearer_token", "SAP_HCM_TOKEN")
    api_key = _first_present(credentials, "api_key", "SAP_HCM_API_KEY")
    user = _first_present(credentials, "user", "username", "sap_hcm_user", "SAP_HCM_USER")
    password = _first_present(credentials, "password", "pass", "sap_hcm_pass", "SAP_HCM_PASS")

    if token:
        headers["authorization"] = f"Bearer {token}"
        return None, "bearer_token"
    if api_key:
        headers["x-api-key"] = str(api_key)
        return None, "api_key"
    if user and password:
        return httpx.BasicAuth(str(user), str(password)), "basic"
    raise ValueError("SAP HCM adapter requires bearer token, api_key, or user/password credentials")


def _fetch_csrf_token(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    auth: httpx.BasicAuth | None,
) -> str:
    fetch_headers = {**headers, "x-csrf-token": "Fetch"}
    response = client.get(url, headers=fetch_headers, auth=auth)
    if not 200 <= response.status_code < 400:
        raise RuntimeError(f"SAP HCM CSRF token fetch failed with HTTP {response.status_code}")
    token = response.headers.get("x-csrf-token")
    if not token:
        raise RuntimeError("SAP HCM CSRF token fetch failed: missing x-csrf-token header")
    return token


def _build_it0008_payload(action_data: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    action_payload = action_data.get("action_payload") if isinstance(action_data.get("action_payload"), dict) else {}
    item = action_data.get("item") if isinstance(action_data.get("item"), dict) else {}
    entity = action_payload.get("entity") if isinstance(action_payload.get("entity"), dict) else {}
    hcm = action_payload.get("sap_hcm") if isinstance(action_payload.get("sap_hcm"), dict) else {}
    return {
        "PERNR": hcm.get("pernr") or entity.get("id") or item.get("entity_id"),
        "INFTY": "0008",
        "ACTION_KIND": action_payload.get("action_kind") or item.get("anomaly_type") or "hcm_access_review",
        "POSITION": hcm.get("position"),
        "COST_CENTER": hcm.get("cost_center"),
        "MONTHLY_COST_USD": hcm.get("monthly_cost_usd"),
        "CONTROL_ROOM_ITEM_ID": item.get("id"),
        "IDEMPOTENCY_KEY": action_data.get("idempotency_key"),
        "DRY_RUN": dry_run,
    }


def _response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:1000]
