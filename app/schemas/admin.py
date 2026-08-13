"""Admin schemas -- the unified lead shape returned by /admin/leads.

Both lead sources (chatbot `infomary_leads` and form `inquiries`) are
normalized into this single flat shape so the dashboard can render one table
with no per-source branching. Source-specific extras live in `details`.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class UnifiedLead(BaseModel):
    # Globally-unique id, prefixed by source ("form:<uuid>" / "chat:<lead_id>")
    # so the two id spaces never collide.
    id: str
    source: str  # "form" | "chat"

    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    facility_name: Optional[str] = None   # form leads only
    facility_type: Optional[str] = None   # form: category | chat: care_type
    interest: Optional[str] = None        # what they want (form: message | chat: care_need)
    location: Optional[str] = None        # chat leads only
    budget: Optional[str] = None          # chat leads only
    contact_time_preference: Optional[str] = None  # form leads (chat: added later)

    status: str
    created_at: Optional[datetime] = None

    # Everything source-specific, so nothing is lost for a detail view.
    details: dict[str, Any] = {}


class UnifiedLeadsPage(BaseModel):
    items: list[UnifiedLead]
    limit: int
    offset: int
    total: int          # combined count across both sources
    total_form: int
    total_chat: int
    has_more: bool