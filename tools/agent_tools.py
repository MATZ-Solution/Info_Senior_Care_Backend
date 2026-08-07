import time
from dotenv import load_dotenv
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from logger import log_search, log_error
from tools.explore_mode import search_facilities
from tools.web_search import web_search

load_dotenv()

class GoogleSearchInput(BaseModel):
    query: str

class FacilitySearchInput(BaseModel):
    # Field descriptions matter here in a way they don't for a bare-fields
    # schema -- there's no separate "parse" LLM step in this pipeline (see
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

async def _google_search(query: str) -> tuple[str, list[dict]]:
    return await web_search(query)

google_search = StructuredTool.from_function(
    coroutine=_google_search,
    name="google_search",
    description=(
        "General web search for non-facility lookups, e.g. nearest ER/urgent care in an "
        "emergency, or other services facility_search doesn't handle. For anything about "
        "finding a specific senior care facility (nursing home, home health, hospice, "
        "assisted living, memory care, etc.), use facility_search instead -- it checks our "
        "certified database first and automatically falls back to a web search itself when needed."
    ),
    args_schema=GoogleSearchInput,
    response_format="content_and_artifact",
)

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

facility_search = StructuredTool.from_function(
    coroutine=_facility_search,
    name="facility_search",
    description=(
        "Use this whenever the user wants to find a senior care facility of any kind -- "
        "nursing home, home health, hospice, inpatient rehab, long-term care hospital, "
        "assisted living, memory care, or anything similar -- REGARDLESS of location, "
        "including outside the US. Pass facility_type/city/state when known, and "
        "descriptive_text for open-ended qualities like 'caring, family-focused.' It "
        "checks our certified CMS database first and automatically falls back to a "
        "general web search on its own when there's no certified match (including when "
        "the location isn't in our US database at all) -- you don't need to know which "
        "types or locations are covered, or call a second tool yourself, ever."
    ),
    args_schema=FacilitySearchInput,
    response_format="content_and_artifact",
)