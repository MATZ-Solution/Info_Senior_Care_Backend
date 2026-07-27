# """Shared/generic response schemas."""
# from pydantic import BaseModel, field_validator


# class MessageResponse(BaseModel):
#     message: str


# class HealthResponse(BaseModel):
#     status: str


# class ReadinessResponse(BaseModel):
#     status: str
#     database: bool
#     cache: bool


# class UUIDStrMixin(BaseModel):
#     """
#     Shared base for any schema with UUID-typed id fields (id, facility_id,
#     user_id, etc). SQLAlchemy returns native uuid.UUID objects, but our
#     schemas type these fields as `str` (simpler for client consumption/
#     OpenAPI). This mixin coerces UUID -> str automatically on validation so
#     every endpoint doesn't need to remember to call str(...) manually.
#     """

#     @field_validator("*", mode="before")
#     @classmethod
#     def _coerce_uuid_to_str(cls, value):
#         import uuid as _uuid

#         if isinstance(value, _uuid.UUID):
#             return str(value)
#         return value






































"""Shared/generic response schemas."""
from pydantic import BaseModel, field_validator


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    database: bool
    cache: bool
    # Added for the TypeSense integration. Defaulted so that any existing
    # caller or test constructing this without `search` keeps working.
    search: bool = False


class UUIDStrMixin(BaseModel):
    """
    Shared base for any schema with UUID-typed id fields (id, facility_id,
    user_id, etc). SQLAlchemy returns native uuid.UUID objects, but our
    schemas type these fields as `str` (simpler for client consumption/
    OpenAPI). This mixin coerces UUID -> str automatically on validation so
    every endpoint doesn't need to remember to call str(...) manually.
    """

    @field_validator("*", mode="before")
    @classmethod
    def _coerce_uuid_to_str(cls, value):
        import uuid as _uuid

        if isinstance(value, _uuid.UUID):
            return str(value)
        return value
