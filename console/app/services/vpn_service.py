"""WG-easy admin API client.

Issues WireGuard client configs for users so the console can attach a one-time
download link to the invitation email.

Env:
  VPN_API_URL              e.g. http://10.0.1.10:51821
  VPN_API_PASSWORD         wg-easy admin password
  VPN_CLIENT_ALLOWED_IPS   AllowedIPs to inject in the rendered .conf (split-
                           tunnel). Default: '10.0.0.0/16,10.8.0.0/24' so user
                           internet traffic does NOT go through the VPN.
  VPN_CLIENT_STRIP_DNS     If 'true' (default), remove `DNS = ...` lines from
                           the rendered .conf so the client keeps its own DNS.
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

API_URL  = os.environ.get("VPN_API_URL", "").rstrip("/")
API_PASS = os.environ.get("VPN_API_PASSWORD", "")

CLIENT_ALLOWED_IPS = os.environ.get("VPN_CLIENT_ALLOWED_IPS", "10.0.0.0/16,10.8.0.0/24").strip()
CLIENT_STRIP_DNS   = os.environ.get("VPN_CLIENT_STRIP_DNS", "true").lower() == "true"


class VPNError(Exception):
    pass


async def _session(client: httpx.AsyncClient) -> None:
    if not API_URL or not API_PASS:
        raise VPNError("VPN_API_URL / VPN_API_PASSWORD no configurados")
    r = await client.post(f"{API_URL}/api/session", json={"password": API_PASS})
    if r.status_code >= 400:
        raise VPNError(f"WG-easy login failed: {r.status_code} {r.text[:120]}")


async def create_client(name: str) -> str:
    """Create a WG-easy client and return its id."""
    async with httpx.AsyncClient(timeout=15) as c:
        await _session(c)
        r = await c.post(f"{API_URL}/api/wireguard/client", json={"name": name})
        if r.status_code >= 400:
            raise VPNError(f"create_client failed: {r.status_code} {r.text[:200]}")
        # Newer wg-easy returns {success: true, id: "..."}; older returns 200 with no body.
        # Resolve the id from the listing as a fallback.
        try:
            data = r.json()
            if isinstance(data, dict) and data.get("id"):
                return str(data["id"])
        except Exception:
            pass
        # fallback: list clients and find the most recently created one with this name
        r2 = await c.get(f"{API_URL}/api/wireguard/client")
        clients = r2.json() if r2.status_code < 400 else []
        matches = [x for x in clients if x.get("name") == name]
        if not matches:
            raise VPNError(f"client '{name}' was created but not found in listing")
        # sort by createdAt descending if available
        matches.sort(key=lambda x: x.get("createdAt", ""), reverse=True)
        return str(matches[0]["id"])


async def get_config(client_id: str) -> str:
    """Fetch the .conf text for the given client and rewrite it for split-tunnel
    + bring-your-own-DNS so a connected user keeps regular internet access."""
    async with httpx.AsyncClient(timeout=15) as c:
        await _session(c)
        r = await c.get(f"{API_URL}/api/wireguard/client/{client_id}/configuration")
        if r.status_code >= 400:
            raise VPNError(f"get_config failed: {r.status_code} {r.text[:200]}")
        return _rewrite_conf(r.text)


def _rewrite_conf(text: str) -> str:
    """Force AllowedIPs to our VPC ranges and (optionally) strip DNS lines.
    wg-easy v14 always emits 0.0.0.0/0 + ::/0 + DNS — overriding here keeps
    every client config consistent regardless of when it was created."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        low = stripped.lower()
        if CLIENT_STRIP_DNS and low.startswith("dns"):
            # matches "DNS = ..." or "DNS=..."
            if re.match(r"dns\s*=", low):
                continue
        if low.startswith("allowedips") and re.match(r"allowedips\s*=", low):
            indent = line[:len(line) - len(stripped)]
            lines.append(f"{indent}AllowedIPs = {CLIENT_ALLOWED_IPS}")
            continue
        lines.append(line)
    out = "\n".join(lines)
    if not out.endswith("\n"):
        out += "\n"
    return out


async def get_qrcode_svg(client_id: str) -> str:
    async with httpx.AsyncClient(timeout=15) as c:
        await _session(c)
        r = await c.get(f"{API_URL}/api/wireguard/client/{client_id}/qrcode.svg")
        if r.status_code >= 400:
            raise VPNError(f"qrcode failed: {r.status_code} {r.text[:200]}")
        return r.text


async def delete_client(client_id: str) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        await _session(c)
        r = await c.delete(f"{API_URL}/api/wireguard/client/{client_id}")
        if r.status_code >= 400 and r.status_code != 404:
            raise VPNError(f"delete_client failed: {r.status_code} {r.text[:200]}")
