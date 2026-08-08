"""Admin endpoints -- unified leads view.

GET /admin/leads merges the two lead sources into ONE normalized, date-sorted,
paginated list so the frontend renders a single table with zero per-source
logic:
  • inquiries        -- form leads      (SQLAlchemy / get_db)
  • infomary_leads   -- chatbot leads   (raw asyncpg pool in database.py)

Each source is read independently and resilient: if one is unavailable, the
endpoint still returns the other rather than failing the whole request.

Auth: requires an authenticated user. This list contains PII (names, emails,
phones) and care details, so it must never be public. There is no role system
in the app yet -- gate this to admins at the edge (or add a role check here)
before exposing it to a real dashboard.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import AuthenticatedUser
from app.dependencies import require_user
from app.models.inquiry import Inquiry
from app.schemas.admin import UnifiedLead, UnifiedLeadsPage

router = APIRouter(prefix="/admin", tags=["admin"])


def _sort_key(dt: Optional[datetime]) -> datetime:
    """Comparable key for mixed tz-aware (inquiries) and naive (infomary_leads)
    timestamps -- normalize everything to naive UTC. None sinks to the bottom."""
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _clean(v):
    """Treat the empty-string defaults in infomary_leads as null."""
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _location(city, state) -> Optional[str]:
    """Combine city + state into one "City, State" string (like chatbot leads
    store in `location`). Either part may be missing."""
    parts = [p for p in (_clean(city), _clean(state)) if p]
    return ", ".join(parts) or None


def _from_inquiry(r: Inquiry) -> UnifiedLead:
    return UnifiedLead(
        id=f"form:{r.id}",
        source="form",
        name=r.user_name,
        email=r.user_email,
        phone=r.contact_phone,
        facility_name=r.facility_name,
        facility_type=r.facility_type_category,
        interest=r.message,
        location=_location(r.city, r.state),
        budget=r.budget,
        contact_time_preference=r.contact_time_preference,
        status=r.status or "pending",
        created_at=r.created_at,
        details={
            "facility_id": str(r.facility_id),
            "state": r.state,
            "city": r.city,
            "message": r.message,
        },
    )


def _from_lead(r: dict) -> UnifiedLead:
    return UnifiedLead(
        id=f"chat:{_clean(r.get('lead_id')) or r.get('id')}",
        source="chat",
        name=_clean(r.get("name")),
        email=_clean(r.get("email")),
        phone=_clean(r.get("phone")),
        facility_name=None,
        facility_type=_clean(r.get("care_type")),
        interest=_clean(r.get("care_need")),
        location=_clean(r.get("location")),
        budget=_clean(r.get("budget")),
        contact_time_preference=None,
        status=_clean(r.get("status")) or "New",
        created_at=r.get("created_at"),
        details={
            "session_id": _clean(r.get("session_id")),
            "age": _clean(r.get("age")),
            "gender": _clean(r.get("gender")),
            "living_arrangement": _clean(r.get("living_arrangement")),
            "conditions": _clean(r.get("conditions")),
            "insurance": _clean(r.get("insurance")),
            "notes": _clean(r.get("notes")),
            "email_sent": r.get("email_sent"),
        },
    )


@router.get("/leads", response_model=UnifiedLeadsPage)
async def list_all_leads(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    source: Optional[str] = Query(default=None, description="Filter: 'form' or 'chat'"),
    status: Optional[str] = Query(default=None, description="Filter by raw status value"),
    user: AuthenticatedUser = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Merged, normalized, date-sorted leads from both sources."""
    want_form = source in (None, "form")
    want_chat = source in (None, "chat")

    # To page correctly across two independently-sorted sources, pull the top
    # (offset + limit) of each, merge, sort, then slice -- the global top-N is
    # always contained in the union of each source's top-N.
    take = offset + limit

    form_items: list[UnifiedLead] = []
    total_form = 0
    if want_form:
        stmt = select(Inquiry)
        count_stmt = select(func.count()).select_from(Inquiry)
        if status:
            stmt = stmt.where(Inquiry.status == status)
            count_stmt = count_stmt.where(Inquiry.status == status)
        stmt = stmt.order_by(Inquiry.created_at.desc()).limit(take)
        rows = (await db.execute(stmt)).scalars().all()
        form_items = [_from_inquiry(r) for r in rows]
        total_form = (await db.execute(count_stmt)).scalar_one()

    chat_items: list[UnifiedLead] = []
    total_chat = 0
    if want_chat:
        # Raw asyncpg pool lives in database.py (separate from SQLAlchemy). Read
        # defensively so a chatbot-DB hiccup doesn't take down the whole view.
        try:
            from database import get_db_connection

            async with get_db_connection() as conn:
                if status:
                    rows = await conn.fetch(
                        "SELECT * FROM infomary_leads WHERE status = $1 "
                        "ORDER BY created_at DESC LIMIT $2",
                        status, take,
                    )
                    total_chat = await conn.fetchval(
                        "SELECT COUNT(*) FROM infomary_leads WHERE status = $1", status
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM infomary_leads ORDER BY created_at DESC LIMIT $1",
                        take,
                    )
                    total_chat = await conn.fetchval("SELECT COUNT(*) FROM infomary_leads")
            chat_items = [_from_lead(dict(r)) for r in rows]
        except Exception:
            # Pool not ready / query failed -- return whatever the other source has.
            chat_items = []
            total_chat = 0

    merged = form_items + chat_items
    merged.sort(key=lambda x: _sort_key(x.created_at), reverse=True)
    page = merged[offset:offset + limit]

    total = total_form + total_chat
    return UnifiedLeadsPage(
        items=page,
        limit=limit,
        offset=offset,
        total=total,
        total_form=total_form,
        total_chat=total_chat,
        has_more=(offset + limit) < total,
    )