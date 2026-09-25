#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


TOKEN = os.environ.get("FAKE_HUBSPOT_TOKEN", "pat-na1-acceptance-token")


def _object(record_id: str, properties: dict) -> dict:
    updated = properties.get("hs_lastmodifieddate") or properties.get("lastmodifieddate")
    created = properties.get("createdate") or "2026-05-01T00:00:00Z"
    return {
        "id": record_id,
        "createdAt": created,
        "updatedAt": updated or created,
        "archived": False,
        "properties": properties,
    }


OBJECTS = {
    "/crm/v3/objects/deals": [
        _object(
            "deal-001",
            {
                "dealname": "ACME Expansion",
                "amount": "12000",
                "amount_in_home_currency": "12000",
                "dealstage": "appointmentscheduled",
                "pipeline": "default",
                "closedate": "2026-06-15T00:00:00Z",
                "createdate": "2026-05-01T10:00:00Z",
                "hs_lastmodifieddate": "2026-05-31T09:00:00Z",
                "hubspot_owner_id": "owner-001",
                "hs_deal_stage_probability": "0.25",
                "dealtype": "newbusiness",
                "hs_is_closed": "false",
                "hs_is_closed_won": "false",
                "hs_forecast_amount": "3000",
                "hs_projected_amount": "3000",
                "hs_lastactivitydate": "2026-05-01T12:00:00Z",
                "hs_deal_amount_calculation_preference": "manual",
                "num_associated_contacts": "2",
            },
        ),
        _object(
            "deal-002",
            {
                "dealname": "Globex Renewal",
                "amount": "5000",
                "amount_in_home_currency": "5000",
                "dealstage": "closedwon",
                "pipeline": "default",
                "closedate": "2026-05-20T00:00:00Z",
                "createdate": "2026-04-15T10:00:00Z",
                "hs_lastmodifieddate": "2026-05-30T15:00:00Z",
                "hubspot_owner_id": "owner-001",
                "hs_deal_stage_probability": "1.0",
                "dealtype": "existingbusiness",
                "hs_is_closed": "true",
                "hs_is_closed_won": "true",
                "hs_forecast_amount": "5000",
                "hs_projected_amount": "5000",
                "hs_lastactivitydate": "2026-05-20T14:00:00Z",
                "hs_deal_amount_calculation_preference": "manual",
                "num_associated_contacts": "1",
            },
        ),
    ],
    "/crm/v3/objects/companies": [
        _object(
            "company-001",
            {
                "name": "ACME",
                "domain": "acme.example",
                "industry": "Manufacturing",
                "city": "Austin",
                "state": "TX",
                "country": "US",
                "hubspot_owner_id": "owner-001",
                "lifecyclestage": "customer",
                "annualrevenue": "1000000",
                "numberofemployees": "120",
                "createdate": "2026-01-01T00:00:00Z",
                "hs_lastmodifieddate": "2026-05-31T08:00:00Z",
            },
        )
    ],
    "/crm/v3/objects/contacts": [
        _object(
            "contact-001",
            {
                "firstname": "Ada",
                "lastname": "Lovelace",
                "email": "ada@example.com",
                "company": "ACME",
                "jobtitle": "CFO",
                "lifecyclestage": "customer",
                "hubspot_owner_id": "owner-001",
                "createdate": "2026-01-02T00:00:00Z",
                "lastmodifieddate": "2026-05-31T08:10:00Z",
            },
        )
    ],
    "/crm/v3/objects/line_items": [
        _object(
            "line-001",
            {
                "name": "Enterprise Seat",
                "quantity": "10",
                "price": "1200",
                "amount": "12000",
                "hs_product_id": "prod-001",
                "hs_sku": "ENT-SEAT",
                "createdate": "2026-05-01T10:00:00Z",
                "hs_lastmodifieddate": "2026-05-31T09:00:00Z",
            },
        )
    ],
}

OWNERS = [
    {
        "id": "owner-001",
        "email": "seller@example.com",
        "firstName": "Sofia",
        "lastName": "Sales",
        "userId": 1001,
        "archived": False,
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-05-31T09:30:00Z",
    }
]

PIPELINES = [
    {
        "id": "default",
        "label": "Sales Pipeline",
        "displayOrder": 0,
        "archived": False,
        "stages": [
            {
                "id": "appointmentscheduled",
                "label": "Appointment Scheduled",
                "displayOrder": 1,
                "metadata": {"probability": "0.25", "isClosed": "false"},
            },
            {
                "id": "closedwon",
                "label": "Closed Won",
                "displayOrder": 9,
                "metadata": {"probability": "1.0", "isClosed": "true"},
            },
        ],
    }
]


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeHubSpot/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print("fake-hubspot " + fmt % args, flush=True)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {TOKEN}"

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"status": "error", "message": "Unauthorized"})
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        limit = int((query.get("limit") or ["100"])[0])
        if parsed.path == "/crm/v3/owners":
            self._json(200, {"results": OWNERS[:limit]})
            return
        if parsed.path == "/crm/v3/pipelines/deals":
            self._json(200, {"results": PIPELINES})
            return
        if parsed.path in OBJECTS:
            self._json(200, {"results": OBJECTS[parsed.path][:limit]})
            return
        self._json(404, {"status": "error", "message": f"Unknown path: {parsed.path}"})


def main() -> None:
    port = int(os.environ.get("FAKE_HUBSPOT_PORT", "18030"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"fake-hubspot listening on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
