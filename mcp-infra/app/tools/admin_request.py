"""
admin_request — tool the workspace assistant uses when it can't answer a
question with the existing data and tools. Sends an email to the platform
admin describing what the user asked and what the assistant believes is
needed to make it answerable.
"""
from __future__ import annotations

import os
import smtplib
from html import escape
from datetime import datetime, timezone
from email.message import EmailMessage

from app.registry import tool


SMTP_HOST    = os.environ.get("SMTP_HOST", "mailhog")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "1025"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM    = os.environ.get("SMTP_FROM", "noreply@modecissions.local")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "false").lower() == "true"
ADMIN_EMAIL  = os.environ.get("ADMIN_EMAIL", "")


@tool(
    name="request_admin_help",
    description=(
        "Send the user's unanswerable question to the platform admin by email "
        "so they can build the dataset / tool / dashboard required. "
        "USE THIS instead of replying 'no puedo' when the existing GOLD tables "
        "and RAG don't cover the question. Always include WHAT was asked, WHY "
        "you couldn't answer, and WHAT you would need (specific dataset, source "
        "system, new tool, etc.). Returns whether the email was delivered."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "user_email":       {"type": "string", "description": "Email of the user who made the request"},
            "user_name":        {"type": "string", "description": "Display name of the user (optional)"},
            "user_question":    {"type": "string", "description": "Verbatim question the user asked"},
            "why_unanswerable": {"type": "string", "description": "Brief explanation of why you can't answer"},
            "what_is_needed":   {"type": "string", "description": "What dataset / tool / pipeline would unblock it"},
        },
        "required": ["user_question", "why_unanswerable", "what_is_needed"],
    },
)
async def request_admin_help(
    user_question: str,
    why_unanswerable: str,
    what_is_needed: str,
    user_email: str = "",
    user_name: str = "",
) -> dict:
    recipients = _resolve_recipients()
    if not recipients:
        return {"sent": False, "error": "No active admin has escalation_notify=TRUE and ADMIN_EMAIL is not set"}

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    requester = f"{user_name} <{user_email}>" if user_name and user_email \
                else (user_email or user_name or "(usuario desconocido)")

    subject = f"[ΩMEGA by EPIUSE] Solicitud no resuelta — {user_question[:60]}"
    requester_html = escape(requester)
    question_html = escape(user_question)
    why_html = escape(why_unanswerable)
    needed_html = escape(what_is_needed)

    text = (
        f"El asistente del workspace no pudo responder esta solicitud y la deriva "
        f"al equipo administrador.\n\n"
        f"Fecha:     {timestamp}\n"
        f"Usuario:   {requester}\n\n"
        f"--- Pregunta del usuario ---\n{user_question}\n\n"
        f"--- Por qué no se pudo responder ---\n{why_unanswerable}\n\n"
        f"--- Qué se necesita para resolverlo ---\n{what_is_needed}\n"
    )

    html = f"""<!DOCTYPE html>
<html><body style="font-family:Helvetica,Arial,sans-serif;background:#0d1117;color:#e6edf3;padding:32px">
  <div style="max-width:600px;margin:0 auto;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:28px">
    <div style="font-size:18px;font-weight:700;letter-spacing:2px;color:#3fb950;margin-bottom:4px">
      <span style="color:#d29922">Ω</span>MEGA <span style="color:#6e7681;font-size:11px;letter-spacing:1.5px">BY EPIUSE</span>
    </div>
    <div style="font-size:9px;letter-spacing:2px;color:#6e7681;margin-bottom:20px">
      ADMIN REQUEST · {timestamp}
    </div>
    <h2 style="font-size:15px;color:#d29922;margin:0 0 14px;letter-spacing:1px">SOLICITUD NO RESUELTA</h2>
    <p style="font-size:11px;color:#8b949e;margin:0 0 12px"><strong>Usuario:</strong> {requester_html}</p>

    <div style="margin:18px 0">
      <div style="font-size:10px;color:#7c9fff;letter-spacing:.08em;margin-bottom:4px">PREGUNTA</div>
      <div style="font-size:13px;color:#e6edf3;background:#0d1117;padding:10px 12px;border-left:2px solid #7c9fff;border-radius:2px">{question_html}</div>
    </div>

    <div style="margin:18px 0">
      <div style="font-size:10px;color:#e05c5c;letter-spacing:.08em;margin-bottom:4px">POR QUÉ NO PUDE RESPONDER</div>
      <div style="font-size:13px;color:#e6edf3;background:#0d1117;padding:10px 12px;border-left:2px solid #e05c5c;border-radius:2px">{why_html}</div>
    </div>

    <div style="margin:18px 0">
      <div style="font-size:10px;color:#3fb950;letter-spacing:.08em;margin-bottom:4px">QUÉ SE NECESITA</div>
      <div style="font-size:13px;color:#e6edf3;background:#0d1117;padding:10px 12px;border-left:2px solid #3fb950;border-radius:2px">{needed_html}</div>
    </div>
  </div>
</body></html>"""

    try:
        msg = EmailMessage()
        msg["From"]    = SMTP_FROM
        msg["To"]      = ", ".join(recipients)
        msg["Subject"] = subject
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USER:
                smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
        return {"sent": True, "to": recipients, "subject": subject}
    except Exception as exc:                                       # noqa: BLE001
        return {"sent": False, "error": str(exc), "to": recipients}


def _resolve_recipients() -> list[str]:
    """Pull active admins flagged escalation_notify=TRUE; fall back to
    ADMIN_EMAIL env if the table has no opted-in admin."""
    try:
        import psycopg2
        from app.config import settings as s
        conn = psycopg2.connect(host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
                                user=s.pg_user, password=s.pg_password)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email FROM users "
                "WHERE is_active = TRUE AND role = 'admin' "
                "AND escalation_notify = TRUE "
                "ORDER BY email"
            )
            rows = [r[0] for r in cur.fetchall()]
        conn.close()
        if rows:
            return rows
    except Exception:
        pass
    return [ADMIN_EMAIL] if ADMIN_EMAIL else []
