"""Resource schemas."""
from datetime import datetime
from typing import Optional

from app.schemas.common import UUIDStrMixin
from pydantic import BaseModel, ConfigDict


class ResourceOut(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    category: Optional[str] = None
    content: Optional[str] = None
    created_at: datetime


class ResourceListItem(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    category: Optional[str] = None
    created_at: datetime
