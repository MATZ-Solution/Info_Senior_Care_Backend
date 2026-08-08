# """Inquiry schemas."""
# from datetime import datetime
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict, Field


# class InquiryCreate(BaseModel):
#     facility_id: str
#     message: Optional[str] = Field(default=None, max_length=2000)
#     contact_phone: Optional[str] = Field(default=None, max_length=30)
#     contact_time_preference: Optional[str] = Field(default=None, max_length=100)


# class InquiryOut(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     facility_id: str
#     message: Optional[str] = None
#     contact_phone: Optional[str] = None
#     contact_time_preference: Optional[str] = None
#     status: str
#     created_at: datetime

















# """Inquiry schemas."""
# from datetime import datetime
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict, Field


# class InquiryCreate(BaseModel):
#     facility_id: str
#     message: Optional[str] = Field(default=None, max_length=2000)
#     contact_phone: Optional[str] = Field(default=None, max_length=30)
#     contact_time_preference: Optional[str] = Field(default=None, max_length=100)


# class InquiryOut(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     user_name: Optional[str] = None
#     user_email: Optional[str] = None
#     facility_id: str
#     facility_name: Optional[str] = None
#     facility_type_category: Optional[str] = None
#     message: Optional[str] = None
#     contact_phone: Optional[str] = None
#     contact_time_preference: Optional[str] = None
#     status: str
#     created_at: datetime



















# """Inquiry schemas."""
# from datetime import datetime
# from typing import Optional

# from app.schemas.common import UUIDStrMixin
# from pydantic import BaseModel, ConfigDict, Field


# class InquiryCreate(BaseModel):
#     facility_id: str
#     message: Optional[str] = Field(default=None, max_length=2000)
#     contact_phone: Optional[str] = Field(default=None, max_length=30)
#     contact_time_preference: Optional[str] = Field(default=None, max_length=100)


# class InquiryOut(UUIDStrMixin):
#     model_config = ConfigDict(from_attributes=True)

#     id: str
#     user_name: Optional[str] = None
#     user_email: Optional[str] = None
#     facility_id: str
#     facility_name: Optional[str] = None
#     facility_type_category: Optional[str] = None
#     state: Optional[str] = None
#     city: Optional[str] = None
#     message: Optional[str] = None
#     contact_phone: Optional[str] = None
#     contact_time_preference: Optional[str] = None
#     status: str
#     created_at: datetime



























"""Inquiry schemas."""
from datetime import datetime
from typing import Optional

from app.schemas.common import UUIDStrMixin
from pydantic import BaseModel, ConfigDict, Field


class InquiryCreate(BaseModel):
    facility_id: str
    message: Optional[str] = Field(default=None, max_length=2000)
    budget: Optional[str] = Field(default=None, max_length=100)
    contact_phone: Optional[str] = Field(default=None, max_length=30)
    contact_time_preference: Optional[str] = Field(default=None, max_length=100)


class InquiryOut(UUIDStrMixin):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    facility_id: str
    facility_name: Optional[str] = None
    facility_type_category: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    message: Optional[str] = None
    budget: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_time_preference: Optional[str] = None
    status: str
    created_at: datetime