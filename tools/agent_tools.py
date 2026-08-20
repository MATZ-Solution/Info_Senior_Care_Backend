import os
import uuid
import asyncio
import traceback
import time
from datetime import datetime
from dotenv import load_dotenv
import resend
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from logger import log_lead, log_email, log_search, log_error, log_success
from tools.explore_mode import search_facilities
from tools.web_search import web_search

load_dotenv()

# Session tracking for progressive saving
_sessions: dict = {}


class UserSafeToolError(RuntimeError):
    """
    Raised by a tool when it wants its message shown to the model verbatim.
    ToolErrorSafetyNetMiddleware (app/middlewares/agent_middleware.py) treats
    this as an explicit opt-in -- only UserSafeToolError's own str(exc) is
    passed through as the failure reason; any other exception type gets a
    generic message instead, so an unanticipated exception (e.g. a raw DB
    driver error with connection details in its message) can't leak
    internals to the model.
    """

class GoogleSearchInput(BaseModel):
    query: str

class SaveLeadInput(BaseModel):
    session_id: str = ""
    name: str = ""
    email: str = ""
    phone: str = ""
    care_need: str = ""
    location: str = ""
    notes: str = ""
    age: str = ""
    gender: str = ""
    living_arrangement: str = ""
    physician: str = ""
    conditions: str = ""
    hospitalizations: str = ""
    medications: str = ""
    allergies: str = ""
    care_type: str = ""
    care_hours: str = ""
    insurance: str = ""
    budget: str = ""
    home_hazards: str = ""
    medical_equipment: str = ""
    other_factors: str = ""
    transportation: str = ""

class FacilitySearchInput(BaseModel):
    # Field descriptions matter here in a way they don't for SaveLeadInput's
    # bare fields: there's no separate "parse" LLM step in this pipeline (see
    # tools/facility_search/search.py) -- the calling agent's own extraction
    # into these args IS Stage 1 of the query pipeline. facility_type in
    # particular feeds a strict 0.4 pg_trgm confidence gate, so a vague
    # description here has a direct, measurable failure mode.
    facility_type: str = Field(
        default="",
        description=(
            "The kind of facility, in the user's own words -- pass whatever the user said "
            "even if you're not sure it's covered (e.g. 'assisted living', 'memory care'). "
            "This tool checks its own certified database and automatically falls back to a "
            "general web search if the type isn't one it covers, so you don't need to know "
            "which types are covered yourself. Leave blank if not mentioned -- do not guess a value."
        ),
    )
    city: str = Field(default="", description="City the user wants to search near, if mentioned.")
    state: str = Field(default="", description="State the user wants to search near, if mentioned.")
    descriptive_text: str = Field(
        default="",
        description=(
            "Open-ended qualities the user cares about, in their own words (e.g. 'caring and "
            "focused on family support', 'good rehab outcomes'). Leave blank if the user only "
            "gave a type/location with no descriptive preference."
        ),
    )

def _build_html_email(lead: dict) -> str:
    # Build HTML table rows for existing lead data
    def row(label, key):
        value = lead.get(key, "")
        if not value or str(value).strip() in ("", "None", "none"):
            return ""
        return f"""
        <tr style="background:#f8f9ff;">
          <td style="padding:12px 20px;border-bottom:1px solid #e0e0e0;width:40%;">
            <p style="margin:0;color:#888;font-size:12px;text-transform:uppercase;letter-spacing:1px;">{label}</p>
          </td>
          <td style="padding:12px 20px;border-bottom:1px solid #e0e0e0;">
            <p style="margin:0;color:#1a1a2e;font-size:14px;">{value}</p>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
        <html lang="en"><head><meta charset="UTF-8"/></head>
        <body style="margin:0;padding:0;background:#f4f6f9;font-family:'Segoe UI',Arial,sans-serif;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:40px 0;">
        <tr><td align="center">
            <table width="600" cellpadding="0" cellspacing="0"
                style="background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
            <tr>
                <td style="background:linear-gradient(135deg,#1a73e8,#0d47a1);padding:40px 48px;text-align:center;">
                <h1 style="margin:0;color:#fff;font-size:26px;font-weight:700;">
                    InfoSenior<span style="color:#90caf9;">.care</span>
                </h1>
                <p style="margin:8px 0 0;color:#bbdefb;font-size:12px;letter-spacing:1.5px;text-transform:uppercase;">
                    New Lead Notification
                </p>
                </td>
            </tr>
            <tr>
                <td style="background:#e8f0fe;padding:18px 48px;border-bottom:1px solid #c5d4f5;">
                <p style="margin:0;color:#1a47a1;font-size:14px;font-weight:600;">
                    A new lead has been captured via Infomary — please follow up promptly.
                </p>
                </td>
            </tr>
            <tr><td style="padding:44px 48px;">
                <p style="margin:0 0 16px;color:#1a1a2e;font-size:18px;font-weight:700;">Contact Information</p>
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;margin-bottom:32px;">
                {row("Full Name","name")}{row("Email Address","email")}{row("Phone Number","phone")}
                {row("Location","location")}{row("Lead ID","lead_id")}{row("Captured At","saved_at")}
                </table>
                <p style="margin:0 0 16px;color:#1a1a2e;font-size:18px;font-weight:700;">Care Needs</p>
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;margin-bottom:32px;">
                {row("Care Need","care_need")}{row("Care Type","care_type")}
                {row("Care Hours","care_hours")}{row("Insurance","insurance")}{row("Budget","budget")}
                </table>
                <p style="margin:0 0 16px;color:#1a1a2e;font-size:18px;font-weight:700;">Personal Details</p>
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;margin-bottom:32px;">
                {row("Age","age")}{row("Gender","gender")}
                {row("Living Arrangement","living_arrangement")}{row("Physician","physician")}
                </table>
                <p style="margin:0 0 16px;color:#1a1a2e;font-size:18px;font-weight:700;">Medical History</p>
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;margin-bottom:32px;">
                {row("Conditions","conditions")}{row("Hospitalizations","hospitalizations")}
                {row("Medications","medications")}{row("Allergies","allergies")}
                </table>
                <p style="margin:0 0 16px;color:#1a1a2e;font-size:18px;font-weight:700;">Additional Info</p>
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;margin-bottom:32px;">
                {row("Home Hazards","home_hazards")}{row("Medical Equipment","medical_equipment")}
                {row("Transportation","transportation")}{row("Other Factors","other_factors")}
                {row("Notes","notes")}
                </table>
                <p style="margin:0;color:#555;font-size:14px;line-height:1.8;">
                This lead was captured automatically via the Infomary AI assistant.
                </p>
            </td></tr>
            <tr>
                <td style="border-top:1px solid #ebebeb;padding:28px 48px;text-align:center;">
                <p style="margin:0 0 4px;color:#1a73e8;font-size:14px;font-weight:700;">InfoSenior.care</p>
                <p style="margin:0;color:#999;font-size:12px;">Automated notification — do not reply.</p>
                </td>
            </tr>
            </table>
        </td></tr>
        </table>
        </body></html>"""

async def _send_lead_confirmation_email(lead: dict) -> dict:
    """Send a warm confirmation email to the lead themselves."""
    lead_email = lead.get("email", "").strip()
    lead_name = lead.get("name", "Friend").strip()
    if not lead_email:
        return {"success": False, "error": "No email provided"}
    log_email(f"Sending confirmation to lead │ {lead_name} │ {lead_email}")
    t = time.time()
    try:
        resend.api_key = os.getenv("RESEND_API_KEY")
        care_need = lead.get("care_need", "")
        location = lead.get("location", "")
        details_line = ""
        if care_need:
            details_line += f"<li>Care need: {care_need}</li>"
        if location:
            details_line += f"<li>Location: {location}</li>"
        html = f"""<!DOCTYPE html>
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
                    <p style="font-size:16px;color:#1a1a2e;">Hi <strong>{lead_name}</strong>,</p>
                    <p style="color:#444;line-height:1.7;">Thank you for reaching out to <strong>InfoSenior.care</strong>. We've received your information and one of our senior care advisors will be in touch with you shortly — completely free of charge.</p>
                    {"<p style='color:#444;'>Here's a summary of what we noted:</p><ul style='color:#444;line-height:1.8;'>" + details_line + "</ul>" if details_line else ""}
                    <p style="color:#444;line-height:1.7;">In the meantime, if you have any questions, feel free to reply to this email or call us back anytime.</p>
                    <p style="color:#444;">Warm regards,<br/><strong>Infomary</strong><br/>InfoSenior.care</p>
                </td></tr>
                <tr><td style="border-top:1px solid #ebebeb;padding:24px 48px;text-align:center;">
                    <p style="margin:0;color:#999;font-size:12px;">InfoSenior.care — Helping families find the right senior care.</p>
                </td></tr>
                </table>
            </td></tr>
            </table>
            </body></html>"""
        resend.Emails.send({
            "from": "InfoSenior.care <onboarding@resend.dev>",
            "to": lead_email,
            "subject": f"We received your request, {lead_name} 💙",
            "html": html,
        })
        ms = int((time.time() - t) * 1000)
        log_success(f"Lead confirmation sent │ to={lead_email} │ {ms}ms")
        return {"success": True}
    except Exception as e:
        log_error(f"Lead confirmation failed │ to={lead_email} │ {e}")
        return {"success": False, "error": str(e)}


async def _send_email(lead: dict) -> dict:
    log_email(f"Sending notification │ lead={lead.get('lead_id')} │ to={lead.get('name','?')} │ need={lead.get('care_need','?')[:60]}")
    t = time.time()
    try:
        resend.api_key = os.getenv("RESEND_API_KEY")
        resend.Emails.send({
            "from": "InfoSenior.care <onboarding@resend.dev>",
            "to": "claudematz456@gmail.com",
            "subject": f"New Lead: {lead.get('name','Unknown')} | {lead.get('care_need','N/A')}",
            "html": _build_html_email(lead)
        })
        ms = int((time.time() - t) * 1000)
        log_success(f"Email sent        │ lead={lead.get('lead_id')} │ took={ms}ms")
        return {"success": True}
    except Exception as e:
        log_error(f"Email failed      │ lead={lead.get('lead_id')} │ {e}")
        return {"success": False, "error": str(e)}

async def _persist_lead(lead: dict, session_id: str, has_name: bool, has_contact: bool, has_email: bool, email_sent: bool) -> bool:
    """
    Shield-protected: runs even if parent function call is cancelled.
    Returns whether the core Supabase upsert succeeded -- _save_lead uses
    this to decide whether it's telling the truth when it says the lead was
    saved. An email-send failure does NOT count against this: the lead is
    genuinely saved even if the confirmation email didn't go out.
    """
    lead_id = lead["lead_id"]

    # Supabase -- source of truth for the dashboard's leads view.
    persisted = False
    try:
        import database as _db
        if _db.db_pool is None:
            await _db.init_db_pool()
        await _db.upsert_lead({
            **lead,
            "session_id": session_id,
            "email_sent": _sessions.get(session_id, {}).get("email_sent", False),
        })
        log_lead(f"Supabase saved | lead={lead_id}")
        persisted = True
    except Exception as e:
        log_error(f"Supabase FAILED | lead={lead_id} | {type(e).__name__}: {e}")
        log_error(traceback.format_exc())

    # Send emails if name + (phone or email) received for first time -- but
    # only for a lead that's actually in the database. Notifying staff of a
    # "new lead" (or confirming to the user) that doesn't exist in Supabase
    # is worse than not sending anything.
    if persisted and has_name and has_contact and not email_sent:
        result = await _send_email(lead)
        if has_email:
            await _send_lead_confirmation_email(lead)
        if result.get("success"):
            if session_id in _sessions:
                _sessions[session_id]["email_sent"] = True
            try:
                import database as _db
                await _db.upsert_lead({**lead, "session_id": session_id, "email_sent": True})
            except Exception as e:
                log_error(f"Supabase email_sent update FAILED | lead={lead_id} | {e}")

    return persisted


async def _save_lead(
    session_id: str = "",
    name: str = "", email: str = "", phone: str = "",
    care_need: str = "", location: str = "", notes: str = "",
    age: str = "", gender: str = "", living_arrangement: str = "",
    physician: str = "", conditions: str = "", hospitalizations: str = "",
    medications: str = "", allergies: str = "", care_type: str = "",
    care_hours: str = "", insurance: str = "", budget: str = "",
    home_hazards: str = "", medical_equipment: str = "",
    other_factors: str = "", transportation: str = "",
) -> str:

    is_new = session_id not in _sessions
    if is_new:
        _sessions[session_id] = {
            "lead_id": str(uuid.uuid4())[:8].upper(),
            "email_sent": False,
            "data": {},
        }

    session = _sessions[session_id]
    lead_id = session["lead_id"]
    email_sent = session["email_sent"]
    action = "new" if is_new else "update"

    # Merge: only overwrite fields that are explicitly provided (non-empty)
    new_fields = {
        "name": name, "email": email, "phone": phone,
        "care_need": care_need, "location": location, "notes": notes,
        "age": age, "gender": gender, "living_arrangement": living_arrangement,
        "physician": physician, "conditions": conditions,
        "hospitalizations": hospitalizations, "medications": medications,
        "allergies": allergies, "care_type": care_type, "care_hours": care_hours,
        "insurance": insurance, "budget": budget, "home_hazards": home_hazards,
        "medical_equipment": medical_equipment, "other_factors": other_factors,
        "transportation": transportation,
    }
    for k, v in new_fields.items():
        if v and v.strip():
            session["data"][k] = v.strip()

    merged = session["data"]
    log_lead(f"save_lead [{action}] | lead={lead_id} | session={session_id[:12]} | fields={list(merged.keys())}")

    lead = {
        "lead_id": lead_id,
        "status": "New",
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **merged,
    }

    has_name    = bool(merged.get("name", "").strip())
    has_contact = bool(merged.get("phone", "").strip() or merged.get("email", "").strip())
    has_email   = bool(merged.get("email", "").strip())

    # asyncio.shield ensures saves complete even if this function call gets cancelled
    try:
        persisted = await asyncio.shield(_persist_lead(lead, session_id, has_name, has_contact, has_email, email_sent))
    except asyncio.CancelledError:
        # _persist_lead continues in background; the caller that would have
        # read our return value already stopped waiting, so there's nothing
        # meaningful to report either way.
        return f"Lead saved. ID: {lead_id}"

    if not persisted:
        raise UserSafeToolError("Failed to save your information due to a technical issue")

    return f"Lead saved. ID: {lead_id}"

async def _google_search(query: str) -> tuple[str, list[dict]]:
    return await web_search(query)

async def _facility_search(
    facility_type: str = "", city: str = "", state: str = "", descriptive_text: str = ""
) -> tuple[str, list[dict] | None]:
    t = time.time()
    try:
        result = await search_facilities(facility_type, city, state, descriptive_text)
        ms = int((time.time() - t) * 1000)
        log_search(f"facility_search   │ type={facility_type!r} city={city!r} state={state!r} │ took={ms}ms")
        return result
    except Exception as e:
        log_error(f"facility_search FAILED │ {e}")
        return "Sorry, I couldn't search facility data right now -- please try again in a moment.", None

google_search = StructuredTool.from_function(
    coroutine=_google_search,
    name="google_search",
    description=(
        """
            General web search for non-facility lookups, e.g. nearest ER/urgent care in an 
            emergency, or other services facility_search doesn't handle. For anything about 
            finding a specific senior care facility (nursing home, home health, hospice, 
            assisted living, memory care, etc.), use facility_search instead it checks our
            certified database first and automatically falls back to a web search itself when needed.
            google_search is only for senior-care-adjacent lookups that genuinely aren't
            about finding a facility (e.g. general emergency resources, nearest ER,
            ombudsman/complaint processes). It is NOT a general-purpose web search
            never call it for anything unrelated to senior care or elder health (recipes,
            news, politics, coding help, homework, financial/investment advice, jokes,
            trivia, etc.).
            Present google_search results conversationally never as a raw list. Always follow up: "I found a
            few strong options near you. Would you like me to connect you with any of
            them directly?"
        """
    ),
    args_schema=GoogleSearchInput,
    response_format="content_and_artifact",
)

save_lead = StructuredTool.from_function(
    coroutine=_save_lead,
    name="save_lead",
    description=(
        "Save or update a senior care lead progressively, ONE call per new piece of "
        "info the user shares -- never a bulk call at the end. Only call this AFTER "
        "the user has explicitly said yes to a customized/personalized-help offer "
        "(e.g. answered yes to 'Would you like options customized to your needs?'); "
        "never before that permission is given, and this has no bearing on whether "
        "facility_search should be called -- that fires on its own, independently, "
        "any time a facility need comes up. Pass session_id plus only the field(s) "
        "the user's latest message actually gave -- leave every other field blank, "
        "never a guessed value."
    ),
    args_schema=SaveLeadInput,
)

facility_search = StructuredTool.from_function(
    coroutine=_facility_search,
    name="facility_search",
    description=(
        """
            Use this whenever the user wants to find a senior care facility of any kind
            nursing home, home health, hospice, inpatient rehab, long-term care hospital,
            facility_search handles finding a senior care facility for the user, end to
            end for ANY location, including outside the US, not just certified US
            matches. Call it whenever the user is looking for a facility of any kind --
            pass whatever facility_type/city/state/descriptive_text you already know, and
            leave the rest blank if you're not sure or the user hasn't said. It
            automatically checks our certified CMS database first, and if there's no
            certified match (wrong type, a location we don't have data for, or a genuine
            zero-result search), it automatically falls back to a general web search and
            discloses that to the user itself. You do not need to track which facility
            types or locations are covered, word any disclosure, or decide between two
            tools -- just call facility_search, every time, regardless of where the
            facility is.

            The only exception: with absolutely nothing to go on at all (no type, no
            location, no descriptive preference whatsoever), ask a brief clarifying
            question yourself first rather than calling any tool.

            When facility_search finds results, the user already sees the actual
            facilities as visual cards (name, location, phone, rating) on screen — your
            reply does NOT need to, and should NOT, re-list those details in prose. Keep
            your reply to a short transition sentence (e.g. "Here are a few options I
            found near Prescott, AZ:") and let the cards carry the detail. Do not invent
            additional facts beyond that short sentence either.

            Every specific fact you state about a facility — its name, address, phone
            number, rating, or any attribute — MUST come from a facility_search result,
            never from memory or pattern-matching your own earlier answers in this
            conversation. Concretely: if the user asks about a facility_type + location
            combination you have NOT already searched earlier in this conversation, you
            MUST call facility_search fresh before answering, even if it looks similar to
            something you answered before (e.g. "hospice in Arizona" then "nursing homes
            in Arizona" are different combinations — the second needs its own call). This
            does NOT mean calling the tool again for an exact repeat of a question you
            already answered in this same conversation — reusing that already-verified
            answer is fine. The rule is: never invent facility details for a combination
            you haven't actually looked up yet.
        """
    ),
    args_schema=FacilitySearchInput,
    response_format="content_and_artifact",
)
