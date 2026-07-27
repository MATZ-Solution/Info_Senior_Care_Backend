import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import cache_get, cache_set
from app.core.database import get_db
from app.models.resource import Resource
from app.schemas.resource import ResourceListItem, ResourceOut

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("", response_model=list[ResourceListItem])
async def list_resources(
    category: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    cache_key = f"resources:list:{category}:{limit}"
    cached = await cache_get(cache_key)
    if cached is not None:
        return [ResourceListItem(**item) for item in cached]

    stmt = select(Resource.id, Resource.title, Resource.category, Resource.created_at)
    if category:
        stmt = stmt.where(Resource.category == category)
    stmt = stmt.order_by(Resource.created_at.desc()).limit(limit)

    rows = (await db.execute(stmt)).all()
    items = [ResourceListItem(**row._mapping) for row in rows]
    await cache_set(cache_key, [item.model_dump(mode="json") for item in items], ttl_seconds=900)
    return items


@router.get("/{resource_id}", response_model=ResourceOut)
async def get_resource(resource_id: str, db: AsyncSession = Depends(get_db)):
    try:
        res_uuid = uuid.UUID(resource_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid resource id")

    resource = (await db.execute(select(Resource).where(Resource.id == res_uuid))).scalar_one_or_none()
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return ResourceOut.model_validate(resource)
