"""Inquiry confirmation email (Resend).

Sends the user a professional "we received your request" email right after
their facility inquiry is saved. Mirrors the chatbot lead email in
tools/agent_tools.py and reuses the same RESEND_API_KEY.

Design:
  • Best-effort: emailing must NEVER block or fail the inquiry submit. The
    caller runs this after commit and swallows/logs any error.
  • No PII beyond what the user already gave; sent only to the user's own email.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# RESEND_FROM = "InfoSenior.care <claudematz456@gmail.com>"
RESEND_FROM = os.getenv("RESEND_FROM", "InfoSenior.care <onboarding@resend.dev>")

def _confirmation_html(name: str, facility_name: Optional[str], location: Optional[str],
                       budget: Optional[str], timeline: Optional[str]) -> str:
    rows = ""
    if facility_name:
        rows += f"<li>Facility: <strong>{facility_name}</strong></li>"
    if location:
        rows += f"<li>Location: {location}</li>"
    if budget:
        rows += f"<li>Budget: {budget}</li>"
    if timeline:
        rows += f"<li>Timeline: {timeline}</li>"
    summary = (
        "<p style='color:#444;'>Here's a summary of your request:</p>"
        f"<ul style='color:#444;line-height:1.8;'>{rows}</ul>" if rows else ""
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"/></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#1a73e8,#0d47a1);padding:40px 48px;text-align:center;">
          <h1 style="margin:0;color:#fff;font-size:26px;font-weight:700;">InfoSenior<span style="color:#90caf9;">.care</span></h1>
          <p style="margin:8px 0 0;color:#bbdefb;font-size:13px;">Your Senior Care Journey Starts Here</p>
        </td>
      </tr>
      <tr><td style="padding:40px 48px;">
        <p style="font-size:16px;color:#1a1a2e;">Hi <strong>{name}</strong>,</p>
        <p style="color:#444;line-height:1.7;">Thank you for your request through <strong>InfoSenior.care</strong>.
        We've received it and a senior care advisor will reach out to you shortly — completely free of charge.</p>
        {summary}
        <p style="color:#444;line-height:1.7;">If you have any questions in the meantime, just reply to this email.</p>
        <p style="color:#444;">Warm regards,<br/><strong>Infomary</strong><br/>InfoSenior.care</p>
      </td></tr>
      <tr><td style="border-top:1px solid #ebebeb;padding:24px 48px;text-align:center;">
        <p style="margin:0;color:#999;font-size:12px;">InfoSenior.care — Helping families find the right senior care.</p>
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""


def send_inquiry_confirmation(
    *,
    to_email: Optional[str],
    name: Optional[str],
    facility_name: Optional[str] = None,
    location: Optional[str] = None,
    budget: Optional[str] = None,
    timeline: Optional[str] = None,
) -> bool:
    """Send the confirmation email. Returns True on success, False otherwise.
    Never raises — safe to call in a fire-and-forget/best-effort manner."""
    if not to_email:
        return False
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        logger.warning("RESEND_API_KEY not set; skipping inquiry confirmation email")
        return False

    display_name = (name or "there").strip() or "there"
    try:
        import resend

        resend.api_key = api_key
        resend.Emails.send({
            "from": RESEND_FROM,
            "to": to_email,
            "subject": f"We received your request, {display_name} 💙",
            "html": _confirmation_html(display_name, facility_name, location, budget, timeline),
        })
        logger.info("Inquiry confirmation email sent to %s", to_email)
        return True
    except Exception as e:
        # Best-effort: log and move on. The inquiry itself already succeeded.
        logger.error("Inquiry confirmation email failed to %s: %s", to_email, e)
        return False