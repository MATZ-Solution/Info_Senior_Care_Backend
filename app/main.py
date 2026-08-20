"""
Application entrypoint. Run with:
    uvicorn app.main:app --reload          (local dev)
    gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4   (production)
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import groq
import sentry_sdk
import uvicorn
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded

from langsmith import traceable
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from app.api.v1.endpoints import health
from app.api.v1.router import api_router
from app.core.cache import close_cache_client
from app.core.config import settings
from app.core.security import decode_supabase_jwt, parse_authenticated_user
from app.dependencies import optional_user_or_guest
from app.middlewares.rate_limit import limiter
from app.services.guest_session import verify_guest_token

from tools.agent_tools import google_search, save_lead, facility_search
from tools.explore_mode import ensure_facility_search_ready
from system_prompt.instructions import system_instructions
from app.middlewares.agent_middleware import (
    AgentContext,
    CardExtractionMiddleware,
    DisclosureEnforcementMiddleware,
    FinalOutputValidationMiddleware,
    GENERIC_FALLBACK_TEXT,
    LeakedToolCallRetryMiddleware,
    ProviderRetryMiddleware,
    SessionIdOverrideMiddleware,
    ToolCallDedupMiddleware,
    ToolErrorSafetyNetMiddleware,
)
from database import (
    init_db_pool,
    close_db_pool,
    save_message,
    fetch_history,
    update_session_title,
    get_all_sessions,
    delete_session,
    claim_session_if_anonymous,
    SessionAccessDenied,
    get_dashboard_stats,
    get_all_leads,
    update_lead_status,
)
from logger import log_startup, log_ws, log_llm, log_tool, log_api, log_error, log_success, log_warn, log_divider

load_dotenv()

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("app")

if settings.SENTRY_DSN:
    sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.ENVIRONMENT, traces_sample_rate=0.1)

MAX_HISTORY_MESSAGES = 20
TOOL_CALL_TIMEOUT_SECONDS = 20


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_divider("INFOMARY BACKEND STARTING")
    log_startup("Initializing database pool...")
    await init_db_pool()
    log_startup("Provisioning facility search tables...")
    await ensure_facility_search_ready()
    log_startup(f"LLM model: openai/gpt-oss-120b")
    log_startup(f"Tools bound: google_search, facility_search")
    log_divider("READY")
    yield
    log_startup("Shutting down — closing cache connections")
    await close_cache_client()
    log_startup("Shutting down — closing DB pool...")
    await close_db_pool()
    log_startup("Shutdown complete.")


app = FastAPI(
    title="InfoSenior Care API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "The service is temporarily busy due to high demand. Please try again in a few moments."},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Invalid request data", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An unexpected error occurred. Please try again."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Health checks live at the root (no /api/v1 prefix) -- conventional path
# for load balancer / orchestrator probes.
app.include_router(health.router)
app.include_router(api_router, prefix="/api/v1")

llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="openai/gpt-oss-120b",
    temperature=0.1,
)

system_prompt = system_instructions

infoAgent_middleware = [
    ProviderRetryMiddleware(
        max_retries=3,
        retry_on=(groq.RateLimitError, groq.APIConnectionError, groq.InternalServerError),
        backoff_factor=2.0,
        initial_delay=1.0,
        max_delay=20.0,
        jitter=True,
        on_failure="error",
    ),
    ModelRetryMiddleware(
        max_retries=1,
        retry_on=lambda e: "tool call validation failed" in str(e).lower() or "tool_use_failed" in str(e).lower(),
        on_failure="error",
    ),
    LeakedToolCallRetryMiddleware(),
    ToolCallDedupMiddleware(),
    SessionIdOverrideMiddleware(),
    CardExtractionMiddleware(),
    ToolErrorSafetyNetMiddleware(timeout_seconds=TOOL_CALL_TIMEOUT_SECONDS),
    FinalOutputValidationMiddleware(),
    DisclosureEnforcementMiddleware(),
]

infoAgent = create_agent(
    model=llm,
    tools=[google_search, save_lead, facility_search],
    middleware=infoAgent_middleware,
    context_schema=AgentContext,
)


@traceable(name="chat_turn", run_type="chain")
async def run_turn(messages: list, session_id: str) -> dict:
    """
    Runs one user turn via infoAgent's own model -> tool -> model loop, then
    extracts the pieces the websocket layer needs. Wrapped in @traceable so
    the whole turn (including every nested LLM call and tool call) lands in
    LangSmith as a single "chat_turn" run instead of several disconnected
    root runs -- this is what lets an online evaluator inspect "which tools
    were called with what args" for a given turn.
    """
    t_start = time.time()
    turn_length_before = len(messages)

    try:
        result = await infoAgent.ainvoke({"messages": messages}, context={"session_id": session_id})
    except groq.AuthenticationError as e:
        log_error(f"[{session_id[:8]}] GROQ AUTHENTICATION FAILED -- check GROQ_API_KEY | {e}")
        return {
            "output": "We're currently experiencing a service issue and can't process your request right now. Please try again later.", "facility_cards": None, 
            "tool_names_called": []
        }
    except groq.RateLimitError as e:
        log_warn(f"[{session_id[:8]}] Groq rate limited after retries exhausted | {e}")
        return {
            "output": "The service is temporarily busy due to high demand. Please try again in a few moments.", 
            "facility_cards": None, 
            "tool_names_called": []
        }
    except groq.APIError as e:
        # Catches everything else: APIConnectionError/APITimeoutError,
        # InternalServerError ("provider unavailable"), and any
        # BadRequestError the tool_use_failed retry above didn't recover
        # from -- all genuinely exhausted their own retry budget already.
        log_error(f"[{session_id[:8]}] Groq API error after retries exhausted | {type(e).__name__}: {e}")
        return {
            "output": "The service is temporarily unavailable right now. Please try again later.", 
            "facility_cards": None, 
            "tool_names_called": []
        }
    except Exception as e:
        log_error(
            f"[{session_id[:8]}] Unexpected agent error | "
            f"{type(e).__name__}: {e}"
        )
        return {
            "output": (
                "Something went wrong while processing your request. "
                "Please try again later."
            ),
            "facility_cards": None,
            "tool_names_called": [],
        }

    result_messages = result["messages"]
    if not result_messages:
        log_error(f"[{session_id[:8]}] infoAgent returned an empty messages list")
        return {"output": GENERIC_FALLBACK_TEXT, "facility_cards": None, "tool_names_called": []}

    new_messages = result_messages[turn_length_before:]

    response = result_messages[-1]
    output = response.content

    tool_names_called = [m.name for m in new_messages if isinstance(m, ToolMessage)]
    for name in tool_names_called:
        log_tool(f"[{session_id[:8]}] {name}")

    for failure in result.get("tool_failures") or []:
        log_warn(f"[{session_id[:8]}] tool failed | {failure['tool']} | {failure['reason']}")

    total_ms = int((time.time() - t_start) * 1000)
    log_llm(f"[{session_id[:8]}] response | {total_ms}ms | {len(str(output))} chars")

    turn_cards = result.get("turn_cards") or None
    if turn_cards:
        output = ""

    # Final structural safety net on run_turn's own contract with
    # websocket_endpoint -- deliberately redundant with the middleware
    # above (which should already guarantee a good `output`), cheap
    # insurance against this dict ever going out malformed. Blank output is
    # only ever correct when turn_cards are present.
    if not isinstance(output, str):
        output = "" if turn_cards else GENERIC_FALLBACK_TEXT
    elif not output and not turn_cards:
        output = GENERIC_FALLBACK_TEXT
    if turn_cards is not None and not isinstance(turn_cards, list):
        turn_cards = None
    if not isinstance(tool_names_called, list):
        tool_names_called = []

    return {"output": output, "facility_cards": turn_cards, "tool_names_called": tool_names_called}


def _resolve_ws_token(token: str) -> tuple[Optional[str], bool]:
    """
    Resolve a WS first-frame token to (user_id, is_guest). Returns
    (None, False) if the token doesn't verify -- callers decide what to do
    with that (fall back to anonymous, never reject the connection outright).
    """
    if token.startswith("guest_"):
        return verify_guest_token(token), True
    try:
        return parse_authenticated_user(decode_supabase_jwt(token)).user_id, False
    except HTTPException:
        return None, False


# ─── WebSocket Route ───────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    log_divider(f"SESSION {session_id[:12]}")
    log_ws(f"Client connected  │ session={session_id}")

    personalized_prompt = system_prompt + f"\n\nYour session_id for this conversation is: {session_id}\nYou MUST pass this exact session_id in every single save_lead tool call."

    user_id: Optional[str] = None
    pending_turn: Optional[dict] = None
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=15)
    except (asyncio.TimeoutError, WebSocketDisconnect):
        log_ws(f"Client sent nothing before timeout │ session={session_id}")
        await websocket.close(code=1008)
        return
    except ValueError:
        log_ws(f"Malformed first frame │ session={session_id}")
        await websocket.close(code=1003)
        return

    if "token" in first:
        resolved_id, is_guest = _resolve_ws_token(first.get("token") or "")
        if resolved_id is None:
            # Never reject the connection over a bad/expired token -- fall
            # back to anonymous, but tell the client explicitly so it isn't
            # silently downgraded (e.g. so it can trigger a token refresh).
            await websocket.send_json({"auth": "failed", "fallback": "anonymous"})
        else:
            user_id = resolved_id
            if not is_guest:
                # Guest ids aren't guaranteed stable across reconnects (a
                # fresh uuid4 per POST /api/v1/auth/guest call unless the
                # client persists the token itself), so only real Supabase
                # users get to adopt a pre-existing anonymous session.
                await claim_session_if_anonymous(session_id, user_id)
    elif "message" in first:
        pending_turn = first
    else:
        log_ws(f"Unrecognized first frame shape │ session={session_id}")
        await websocket.close(code=1003)
        return

    try:
        while True:
            if pending_turn is not None:
                data, pending_turn = pending_turn, None
            else:
                data = await websocket.receive_json()
            user_message = data.get("message", "")
            history = data.get("history", [])[-MAX_HISTORY_MESSAGES:]

            log_ws(f"[{session_id[:8]}] user: {user_message[:80]}")

            # Scoped to one turn -- a bad LLM/tool generation (e.g. Groq
            # rejecting a malformed tool call with a 400) used to propagate
            # all the way out of the while loop into the connection-level
            # handler below, which sends one reply and then ends the
            # function, killing the whole websocket and forcing the client
            # to reconnect. Catching per-turn instead means one bad turn just
            # gets an apologetic reply, and the same connection keeps going.
            try:
                messages = [SystemMessage(content=personalized_prompt)]
                for msg in history:
                    if msg["role"] == "user":
                        messages.append(HumanMessage(content=msg["content"]))
                    elif msg["role"] == "assistant":
                        messages.append(AIMessage(content=msg["content"]))
                messages.append(HumanMessage(content=user_message))

                try:
                    await save_message(session_id, "user", user_message, user_id=user_id)
                except SessionAccessDenied:
                    # Someone else's session_id -- can't persist here, and
                    # there's no HTTP status code to hand back mid-socket, so
                    # send one explicit error frame and close instead.
                    log_ws(f"Ownership conflict │ session={session_id}")
                    await websocket.send_json({
                        "response": None,
                        "error": "session_owned_by_another_account",
                        "message": "This conversation belongs to a different account. Please start a new chat.",
                    })
                    await websocket.close(code=4001)
                    return

                # Runs the whole tool-calling loop as one traced "chat_turn"
                # run in LangSmith (see run_turn above).
                result = await run_turn(messages, session_id)
                output = result["output"]
                turn_cards = result["facility_cards"]

                await save_message(session_id, "assistant", output, facility_cards=turn_cards, user_id=user_id)
                await websocket.send_json({"response": output, "facility_cards": turn_cards})

            except Exception as e:
                log_error(f"Turn error        │ session={session_id} │ {e}")
                err_text = str(e).lower()
                if "429" in err_text or "rate_limit" in err_text or "rate limit" in err_text:
                    await websocket.send_json({"response": "I'm getting a lot of requests right now -- please wait a few seconds and try again."})
                else:
                    await websocket.send_json({"response": "Sorry, I had trouble with that -- could you try rephrasing?"})

    except WebSocketDisconnect:
        log_ws(f"Client disconnected │ session={session_id}")
    except Exception as e:
        log_error(f"WebSocket error   │ session={session_id} │ {e}")


# ─── Utility Routes ────────────────────────────────────────────
# @app.get("/test-supabase")
# async def test_supabase():
#     """Quick test to verify Supabase lead write works."""
#     from database import upsert_lead, db_pool
#     log_api(f"Supabase test | pool_ready={db_pool is not None}")
#     try:
#         await upsert_lead({
#             "lead_id": "TEST-001",
#             "session_id": "test-session",
#             "name": "Test User",
#             "email": "test@test.com",
#             "phone": "555-0000",
#             "care_need": "Test lead from /test-supabase",
#             "care_type": "Assisted Living",
#             "location": "Chicago, IL",
#             "age": "75", "gender": "", "living_arrangement": "",
#             "conditions": "", "insurance": "", "budget": "",
#             "notes": "Manual test", "status": "New", "email_sent": False,
#         })
#         log_api("Supabase test PASSED")
#         return {"status": "ok", "message": "Lead written to Supabase successfully"}
#     except Exception as e:
#         log_error(f"Supabase test FAILED | {type(e).__name__}: {e}")
#         return {"status": "error", "message": str(e)}


@app.get("/history/{session_id}")
async def get_history(session_id: str, current_user=Depends(optional_user_or_guest)):
    log_api(f"Fetch history | session={session_id[:12]}")
    requester_id = current_user.user_id if current_user else None
    try:
        messages = await fetch_history(session_id, requester_user_id=requester_id)
        return {"messages": messages}
    except SessionAccessDenied:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        log_error(f"get_history failed | session={session_id[:12]} | {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history")


@app.get("/sessions")
async def get_sessions(current_user=Depends(optional_user_or_guest)):
    try:
        requester_id = current_user.user_id if current_user else None
        sessions = await get_all_sessions(user_id=requester_id)
        return {"sessions": sessions}
    except Exception as e:
        log_error(f"get_sessions failed | {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch sessions")


class GenerateTitleRequest(BaseModel):
    session_id: str
    user_message: str
    ai_response: str


@app.post("/generate-title")
async def generate_title(req: GenerateTitleRequest, current_user=Depends(optional_user_or_guest)):
    requester_id = current_user.user_id if current_user else None
    try:
        log_api(f"Generate title | session={req.session_id[:12]}")
        title_llm = ChatGroq(api_key=os.getenv("GROQ_API_KEY"), model="llama-3.3-70b-versatile", temperature=0.3)
        prompt = f"Generate a SHORT description for this chat: {req.user_message} | {req.ai_response}. Format: Description: [X]"
        response = await title_llm.ainvoke(prompt)
        description = "New Conversation", ""
        for line in response.content.split("\n"):
            if line.startswith("Description:"): description = line.replace("Description:", "").strip()
        await update_session_title(req.session_id, description, requester_user_id=requester_id)
        return {"description": description}
    except SessionAccessDenied:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        log_error(f"generate_title failed | session={req.session_id[:12]} | {e}")
        return {"description": ""}


class DeleteSessionRequest(BaseModel):
    session_id: str


class UpdateLeadStatusRequest(BaseModel):
    lead_id: str
    status: str


@app.post("/delete-session")
async def delete_session_endpoint(req: DeleteSessionRequest, current_user=Depends(optional_user_or_guest)):
    requester_id = current_user.user_id if current_user else None
    try:
        log_api(f"Delete session | session={req.session_id[:12]}")
        await delete_session(req.session_id, requester_user_id=requester_id)
        return {"status": "deleted"}
    except SessionAccessDenied:
        raise HTTPException(status_code=404, detail="Session not found")
    except Exception as e:
        log_error(f"delete_session failed | session={req.session_id[:12]} | {e}")
        raise HTTPException(status_code=500, detail="Failed to delete session")


# ─── Dashboard Routes ──────────────────────────────────────────
@app.get("/dashboard/stats")
async def dashboard_stats():
    try:
        stats = await get_dashboard_stats()
        return stats
    except Exception as e:
        log_error(f"dashboard_stats failed | {e}")
        raise HTTPException(status_code=503, detail="Dashboard unavailable — DB may be down")


@app.get("/dashboard/leads")
async def dashboard_leads(limit: int = 100, offset: int = 0, status: str = None):
    try:
        leads = await get_all_leads(limit=limit, offset=offset, status=status)
        for lead in leads:
            for k, v in lead.items():
                if hasattr(v, 'isoformat'):
                    lead[k] = v.isoformat()
        return {"leads": leads}
    except Exception as e:
        log_error(f"dashboard_leads failed | {e}")
        raise HTTPException(status_code=503, detail="Failed to fetch leads")


@app.post("/dashboard/leads/status")
async def update_status(req: UpdateLeadStatusRequest):
    valid = ['New', 'Contacted', 'Qualified', 'Converted', 'Not Interested']
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid}")
    try:
        log_api(f"Update status | lead={req.lead_id} | {req.status}")
        await update_lead_status(req.lead_id, req.status)
        return {"status": "updated"}
    except Exception as e:
        log_error(f"update_status failed | lead={req.lead_id} | {e}")
        raise HTTPException(status_code=500, detail="Failed to update status")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)