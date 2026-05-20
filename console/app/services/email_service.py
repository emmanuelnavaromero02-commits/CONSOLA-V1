"""
SMTP sender. Uses MailHog in dev, drop-in compatible with AWS SES SMTP in prod
(just change the env vars).

Module is named `email_service` instead of `email` to avoid shadowing the
stdlib `email` package that smtplib relies on.
"""
from __future__ import annotations

import asyncio
import os
import smtplib
from email.message import EmailMessage


SMTP_HOST    = os.environ.get("SMTP_HOST", "mailhog")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "1025"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM    = os.environ.get("SMTP_FROM", "noreply@modecissions.local")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").lower() == "true"

WORKSPACE_URL = os.environ.get("WORKSPACE_URL", "")
CONSOLE_URL   = os.environ.get("CONSOLE_URL", "")


def _send_sync(
    to: str, subject: str, html: str, text: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    msg = EmailMessage()
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    msg["Subject"] = subject
    if text:
        msg.set_content(text)
    else:
        msg.set_content(_html_to_text(html))
    msg.add_alternative(html, subtype="html")

    for (filename, data, mime) in (attachments or []):
        maintype, _, subtype = mime.partition("/")
        msg.add_attachment(data, maintype=maintype or "application",
                           subtype=subtype or "octet-stream",
                           filename=filename)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


async def send_email(
    to: str, subject: str, html: str, text: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> bool:
    """Send an email asynchronously. Returns True on success, False otherwise.

    `attachments` is a list of (filename, bytes, mime_type) tuples.

    Failures are intentionally swallowed (logged) so a flaky SMTP doesn't take
    down auth flows — the calling endpoint surfaces a generic message either
    way to avoid leaking who is registered.
    """
    try:
        await asyncio.to_thread(_send_sync, to, subject, html, text, attachments)
        return True
    except Exception as exc:                                    # noqa: BLE001
        print(f"[email_service] send failed to={to} subject={subject!r}: {exc}", flush=True)
        return False


def _html_to_text(html: str) -> str:
    import re
    txt = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    txt = re.sub(r"</p>", "\n\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", "", txt)
    return txt.strip()


# ── Templates ───────────────────────────────────────────────────────────────

def _wrap(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html><body style="font-family:Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;padding:32px;">
  <div style="max-width:520px;margin:0 auto;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:32px;">
    <div style="font-size:18px;font-weight:700;letter-spacing:2px;color:#3fb950;margin-bottom:4px">
      <span style="color:#d29922">Ω</span>MEGA <span style="color:#6e7681;font-size:11px;letter-spacing:1.5px">BY EPIUSE</span>
    </div>
    <div style="font-size:9px;letter-spacing:2px;color:#6e7681;margin-bottom:24px">
      DECISION INTELLIGENCE PLATFORM
    </div>
    <h2 style="font-size:16px;color:#d29922;margin:0 0 16px;letter-spacing:1px">{title}</h2>
    {body_html}
  </div>
  <div style="max-width:520px;margin:14px auto 0;text-align:center;color:#6e7681;font-size:10px">
    Si no esperabas este correo, ignóralo. El link expira automáticamente.
  </div>
</body></html>"""


def render_invitation(name: str | None, email: str, link: str, ttl_hours: int) -> tuple[str, str]:
    greeting = f"Hola {name}," if name else "Hola,"
    subject = "Invitación a ΩMEGA by EPIUSE"
    html = _wrap("BIENVENIDO", f"""
      <p>{greeting}</p>
      <p>Te han invitado a usar <strong>ΩMEGA by EPIUSE</strong>. Activa tu cuenta y elige tu password en el siguiente link:</p>
      <p style="margin:24px 0;">
        <a href="{link}" style="display:inline-block;background:#d29922;color:#1a1408;text-decoration:none;
                                padding:12px 22px;border-radius:4px;font-weight:600;letter-spacing:1px;
                                font-family:'Share Tech Mono',monospace;font-size:11px">
          ACTIVAR CUENTA →
        </a>
      </p>
      <p style="font-size:11px;color:#8b949e">El link expira en {ttl_hours} horas.</p>
      <p style="font-size:11px;color:#6e7681;word-break:break-all">{link}</p>
    """)
    return subject, html


def render_invitation_with_vpn(
    name: str | None,
    email: str,
    activation_link: str,
    vpn_link: str,
    invite_ttl_hours: int,
    vpn_ttl_hours: int,
    vpn_password: str | None = None,
) -> tuple[str, str]:
    """Single welcome email that combines the activation flow and the VPN
    config delivery. If `vpn_password` is provided, the body explains how
    to open the attached ZIP; otherwise it falls back to the (in-network)
    download link."""
    greeting = f"Hola {name}," if name else "Hola,"
    subject = "Bienvenida a ΩMEGA by EPIUSE"

    if vpn_password:
        vpn_block = f"""
      <p>Adjunto a este correo encontrarás un archivo ZIP con tu configuración
         WireGuard personal. Es un AES-ZIP — necesitas la contraseña para
         abrirlo:</p>
      <p style="margin:12px 0;font-family:'Share Tech Mono',monospace;font-size:13px;">
        <span style="background:#0d1117;color:#3fb950;padding:8px 14px;border-radius:4px;letter-spacing:2px;border:1px solid #30363d">
          {vpn_password}
        </span>
      </p>
      <p style="font-size:11px;color:#8b949e">
         Descomprime con esa contraseña (Windows: 7-Zip; macOS/Linux:
         <code>unzip -P &lt;contraseña&gt; archivo.zip</code>), abre el
         <code>.conf</code> con WireGuard e <strong>activa el túnel</strong>.
      </p>
      <p style="font-size:11px;color:#6e7681">
         (Si necesitas re-descargar el config dentro de la red privada, este
         link funciona mientras tengas el túnel activo:
         <a href="{vpn_link}" style="color:#7c9fff">link de respaldo</a> —
         expira en {vpn_ttl_hours}h.)
      </p>"""
    else:
        vpn_block = f"""
      <p>Descarga tu archivo <code>.conf</code> personal
         <strong>(un solo uso)</strong>:</p>
      <p style="margin:12px 0 24px;">
        <a href="{vpn_link}" style="display:inline-block;background:#3fb950;color:#0d1117;text-decoration:none;
                                    padding:12px 22px;border-radius:4px;font-weight:600;letter-spacing:1px;
                                    font-family:'Share Tech Mono',monospace;font-size:11px">
          DESCARGAR CONFIG VPN →
        </a>
      </p>
      <p style="font-size:11px;color:#8b949e">
         Abre el <code>.conf</code> con WireGuard y activa el túnel.
         El link expira en {vpn_ttl_hours}h y deja de funcionar tras la primera descarga.
      </p>"""

    html = _wrap("BIENVENIDO", f"""
      <p>{greeting}</p>
      <p>Te han invitado a usar <strong>ΩMEGA by EPIUSE</strong>. La plataforma vive
         dentro de una red privada, así que necesitas dos pasos para entrar:</p>

      <h3 style="font-size:13px;color:#3fb950;margin:24px 0 8px;letter-spacing:1px">
         PASO 1 — INSTALA WIREGUARD Y CONÉCTATE
      </h3>
      <p>Descarga el cliente WireGuard para tu sistema:</p>
      <p style="font-size:12px;color:#8b949e;margin:4px 0 12px">
         <a href="https://www.wireguard.com/install/" style="color:#7c9fff">
            wireguard.com/install
         </a>
         &nbsp;·&nbsp; Windows · macOS · Linux · iOS · Android
      </p>
      {vpn_block}

      <h3 style="font-size:13px;color:#d29922;margin:32px 0 8px;letter-spacing:1px">
         PASO 2 — ACTIVA TU CUENTA
      </h3>
      <p>Con el túnel arriba, abre este link y elige tu password:</p>
      <p style="margin:12px 0;">
        <a href="{activation_link}" style="display:inline-block;background:#d29922;color:#1a1408;text-decoration:none;
                                           padding:12px 22px;border-radius:4px;font-weight:600;letter-spacing:1px;
                                           font-family:'Share Tech Mono',monospace;font-size:11px">
          ACTIVAR CUENTA →
        </a>
      </p>
      <p style="font-size:11px;color:#8b949e">El link expira en {invite_ttl_hours} horas.</p>
      <p style="font-size:11px;color:#6e7681;word-break:break-all">{activation_link}</p>

      {_workspace_block()}
    """)
    return subject, html


def _workspace_block() -> str:
    """Step-3 block telling the user where to go once they're activated.
    Empty if WORKSPACE_URL is not configured."""
    if not WORKSPACE_URL:
        return ""
    return f"""
      <h3 style="font-size:13px;color:#7c9fff;margin:32px 0 8px;letter-spacing:1px">
         PASO 3 — ENTRA AL WORKSPACE
      </h3>
      <p>Una vez activada tu cuenta, accede al workspace (apps publicadas,
         asistente IA, decisiones) desde el navegador con la VPN conectada:</p>
      <p style="margin:12px 0 4px;">
        <a href="{WORKSPACE_URL}" style="display:inline-block;background:#7c9fff;color:#0d1117;text-decoration:none;
                                         padding:12px 22px;border-radius:4px;font-weight:600;letter-spacing:1px;
                                         font-family:'Share Tech Mono',monospace;font-size:11px">
          ABRIR WORKSPACE →
        </a>
      </p>
      <p style="font-size:11px;color:#6e7681;word-break:break-all">{WORKSPACE_URL}</p>
      <p style="font-size:11px;color:#8b949e">
         Si necesitas re-entrar al panel admin, usa <a href="{CONSOLE_URL}" style="color:#7c9fff">{CONSOLE_URL}</a>.
      </p>
    """


def render_vpn_config(name: str | None, link: str, ttl_hours: int) -> tuple[str, str]:
    greeting = f"Hola {name}," if name else "Hola,"
    subject = "Acceso VPN — ΩMEGA by EPIUSE"
    html = _wrap("ACCESO VPN", f"""
      <p>{greeting}</p>
      <p>Para conectarte a la plataforma desde fuera de la red corporativa necesitas
         una configuración WireGuard. Descarga tu archivo <code>.conf</code> en el
         siguiente link <strong>(un solo uso)</strong>:</p>
      <p style="margin:24px 0;">
        <a href="{link}" style="display:inline-block;background:#3fb950;color:#0d1117;text-decoration:none;
                                padding:12px 22px;border-radius:4px;font-weight:600;letter-spacing:1px;
                                font-family:'Share Tech Mono',monospace;font-size:11px">
          DESCARGAR CONFIG VPN →
        </a>
      </p>
      <p style="font-size:11px;color:#8b949e">
         Pasos: instala el cliente <strong>WireGuard</strong>
         (<a href="https://www.wireguard.com/install/" style="color:#7c9fff">wireguard.com/install</a>),
         abre el <code>.conf</code> descargado y activa el túnel.
      </p>
      <p style="font-size:11px;color:#8b949e">El link expira en {ttl_hours} horas y deja de funcionar después de la primera descarga.</p>
      <p style="font-size:11px;color:#6e7681;word-break:break-all">{link}</p>
    """)
    return subject, html


def render_password_reset(name: str | None, link: str, ttl_hours: int) -> tuple[str, str]:
    greeting = f"Hola {name}," if name else "Hola,"
    subject = "Reestablecer password — ΩMEGA by EPIUSE"
    html = _wrap("RESET DE PASSWORD", f"""
      <p>{greeting}</p>
      <p>Recibimos una solicitud para restablecer tu password en <strong>ΩMEGA by EPIUSE</strong>.
         Si fuiste tú, click aquí:</p>
      <p style="margin:24px 0;">
        <a href="{link}" style="display:inline-block;background:#d29922;color:#1a1408;text-decoration:none;
                                padding:12px 22px;border-radius:4px;font-weight:600;letter-spacing:1px;
                                font-family:'Share Tech Mono',monospace;font-size:11px">
          ELEGIR NUEVO PASSWORD →
        </a>
      </p>
      <p style="font-size:11px;color:#8b949e">El link expira en {ttl_hours} hora(s). Si no fuiste tú, ignora este correo.</p>
      <p style="font-size:11px;color:#6e7681;word-break:break-all">{link}</p>
    """)
    return subject, html
