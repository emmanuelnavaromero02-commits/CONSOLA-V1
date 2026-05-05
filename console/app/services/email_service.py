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


def _send_sync(to: str, subject: str, html: str, text: str | None = None) -> None:
    msg = EmailMessage()
    msg["From"]    = SMTP_FROM
    msg["To"]      = to
    msg["Subject"] = subject
    if text:
        msg.set_content(text)
    else:
        # Bare-bones text fallback if caller didn't supply one
        msg.set_content(_html_to_text(html))
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USER:
            smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)


async def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    """Send an email asynchronously. Returns True on success, False otherwise.

    Failures are intentionally swallowed (logged) so a flaky SMTP doesn't take
    down auth flows — the calling endpoint surfaces a generic message either
    way to avoid leaking who is registered.
    """
    try:
        await asyncio.to_thread(_send_sync, to, subject, html, text)
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
      MO<span style="color:#d29922">D</span>ECISSIONS
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
    subject = "Invitación a MODecissions"
    html = _wrap("BIENVENIDO", f"""
      <p>{greeting}</p>
      <p>Te han invitado a usar <strong>MODecissions</strong>. Activa tu cuenta y elige tu password en el siguiente link:</p>
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


def render_password_reset(name: str | None, link: str, ttl_hours: int) -> tuple[str, str]:
    greeting = f"Hola {name}," if name else "Hola,"
    subject = "Reestablecer password — MODecissions"
    html = _wrap("RESET DE PASSWORD", f"""
      <p>{greeting}</p>
      <p>Recibimos una solicitud para restablecer tu password en <strong>MODecissions</strong>.
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
