# """
# Application entrypoint. Run with:
#     uvicorn app.main:app --reload          (local dev)
#     gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4   (production)
# """
# import logging
# from contextlib import asynccontextmanager

# import sentry_sdk
# from fastapi import FastAPI, Request, status
# from fastapi.exceptions import RequestValidationError
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import JSONResponse
# from slowapi.errors import RateLimitExceeded

# from app.api.v1.endpoints import health
# from app.api.v1.router import api_router
# from app.core.cache import close_cache_client
# from app.core.config import settings
# from app.middlewares.rate_limit import limiter

# logging.basicConfig(
#     level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
#     format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
# )
# logger = logging.getLogger("app")

# if settings.SENTRY_DSN:
#     sentry_sdk.init(dsn=settings.SENTRY_DSN, environment=settings.ENVIRONMENT, traces_sample_rate=0.1)


# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     logger.info("Starting up (environment=%s)", settings.ENVIRONMENT)
#     yield
#     logger.info("Shutting down -- closing cache connections")
#     await close_cache_client()


# app = FastAPI(
#     title="InfoSenior Care API",
#     version="1.0.0",
#     lifespan=lifespan,
#     # Hide interactive docs in production -- avoids exposing the full API
#     # surface/schema publicly; enable explicitly if you want a staging
#     # environment with docs on.
#     docs_url="/docs" if not settings.is_production else None,
#     redoc_url="/redoc" if not settings.is_production else None,
#     openapi_url="/openapi.json" if not settings.is_production else None,
# )

# app.state.limiter = limiter


# @app.exception_handler(RateLimitExceeded)
# async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
#     return JSONResponse(
#         status_code=status.HTTP_429_TOO_MANY_REQUESTS,
#         content={"detail": "Rate limit exceeded -- please slow down and try again shortly."},
#     )


# @app.exception_handler(RequestValidationError)
# async def validation_exception_handler(request: Request, exc: RequestValidationError):
#     return JSONResponse(
#         status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
#         content={"detail": "Invalid request data", "errors": exc.errors()},
#     )


# @app.exception_handler(Exception)
# async def unhandled_exception_handler(request: Request, exc: Exception):
#     # Never leak internal error details (stack traces, DB errors, etc) to
#     # clients -- log the full detail server-side (Sentry captures it too),
#     # return a generic message.
#     logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
#     return JSONResponse(
#         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#         content={"detail": "An unexpected error occurred. Please try again."},
#     )


# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=settings.CORS_ORIGINS,
#     allow_credentials=True,
#     allow_methods=["GET", "POST", "PATCH", "DELETE"],
#     allow_headers=["Authorization", "Content-Type"],
# )

# # Health checks live at the root (no /api/v1 prefix) -- conventional path
# # for load balancer / orchestrator probes.
# app.include_router(health.router)
# app.include_router(api_router, prefix="/api/v1")


"""
Application entrypoint. Run with:
    uvicorn app.main:app --reload          (local dev)
    gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4   (production)
"""
import logging
import os
import json
import re
import time
from contextlib import asynccontextmanager

import sentry_sdk
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
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
from app.middlewares.rate_limit import limiter

from tools.agent_tools import google_search, facility_search
from tools.explore_mode import ensure_facility_search_ready
from tools.facility_search.search import DISCLOSURE_PREFIX
from system_prompt.instructions import system_instructions
from database import (
    init_db_pool,
    close_db_pool,
    save_message,
    fetch_history,
    update_session_title,
    get_all_sessions,
    delete_session,
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

_LEAKED_TOOL_CALL_RE = re.compile(r'^\s*(?:<function[=>])?\s*(?:save_lead|google_search|facility_search)\s*\{')


def _looks_like_leaked_tool_call(content: str) -> bool:
    return bool(_LEAKED_TOOL_CALL_RE.match(content or ""))


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
    # Hide interactive docs in production -- avoids exposing the full API
    # surface/schema publicly; enable explicitly if you want a staging
    # environment with docs on.
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)
 
app.state.limiter = limiter
 
 
@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Rate limit exceeded please try again shortly."},
    )
 
 
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Invalid request data", "errors": exc.errors()},
    )
 
 
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak internal error details (stack traces, DB errors, etc) to
    # clients -- log the full detail server-side (Sentry captures it too),
    # return a generic message.
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
).bind_tools([google_search, facility_search])
 
system_prompt = system_instructions
 
@traceable(name="chat_turn", run_type="chain")
async def run_turn(messages: list, session_id: str) -> dict:
    """
    Runs the full tool-calling loop for one user turn: call tool(s) -> feed
    result back -> maybe call more -> final non-tool response. Wrapped in
    @traceable so the whole turn (including every nested LLM call and tool
    call) lands in LangSmith as a single "chat_turn" run instead of several
    disconnected root runs -- this is what lets an online evaluator inspect
    "which tools were called with what args" for a given turn.
    """
    response = None
    t_start = time.time()
    turn_cards = []
    called_this_turn = {}
    disclosure_required = False
    tool_names_called = []

    for i in range(5):
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            err_text = str(e).lower()
            if "tool call validation failed" in err_text or "tool_use_failed" in err_text:
                log_warn(f"[{session_id[:8]}] malformed tool-call generation -- retrying once")
                response = await llm.ainvoke(messages)
            else:
                raise

        if not (hasattr(response, 'tool_calls') and response.tool_calls) \
                and _looks_like_leaked_tool_call(response.content or ""):
            log_warn(f"[{session_id[:8]}] tool call leaked as plain text -- retrying once")
            response = await llm.ainvoke(messages)

        if hasattr(response, 'tool_calls') and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                tool_names_called.append(tool_name)
                log_tool(f"[{session_id[:8]}] {tool_name} | {json.dumps(tool_args, default=str)[:120]}")
                signature = (tool_name, json.dumps(tool_args, sort_keys=True, default=str))
                if signature in called_this_turn:
                    log_warn(f"[{session_id[:8]}] {tool_name} duplicate call this turn -- reusing prior result, not re-invoking")
                    messages.append(ToolMessage(content=called_this_turn[signature], tool_call_id=tc["id"]))
                    continue
                t_tool = time.time()
                try:
                    if tool_name == "google_search":
                        tool_message = await google_search.ainvoke(tc)
                        result = tool_message.content
                        if tool_message.artifact:
                            turn_cards.extend(
                                {
                                    "source": "not_certified",
                                    "title": r.get("title"),
                                    "snippet": r.get("snippet"),
                                    "url": r.get("link"),
                                }
                                for r in tool_message.artifact
                            )
                    elif tool_name == "facility_search":
                        tool_message = await facility_search.ainvoke(tc)
                        result = tool_message.content
                        if tool_message.artifact:
                            turn_cards.extend(tool_message.artifact)
                        if result.startswith(DISCLOSURE_PREFIX):
                            disclosure_required = True
                    else:
                        result = "Unknown tool"
                        log_warn(f"Unknown tool: {tool_name}")
                    ms = int((time.time() - t_tool) * 1000)
                    log_tool(f"[{session_id[:8]}] {tool_name} done | {ms}ms")
                    called_this_turn[signature] = str(result)
                    messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
                except Exception as e:
                    log_error(f"Tool error | {tool_name} | {e}")
                    messages.append(ToolMessage(content=str(e), tool_call_id=tc["id"]))
        else:
            total_ms = int((time.time() - t_start) * 1000)
            log_llm(f"[{session_id[:8]}] response | {total_ms}ms | {len(response.content)} chars")
            break

    output = response.content if response else "Something went wrong, please try again."
    if _looks_like_leaked_tool_call(output):
        log_warn(f"[{session_id[:8]}] final reply still looked like a leaked tool call after retry -- using fallback text")
        output = "Sorry, I had trouble with that -- could you try rephrasing?"
    if disclosure_required and DISCLOSURE_PREFIX.lower() not in output.lower():
        log_warn(f"[{session_id[:8]}] LLM reply dropped the required disclosure -- prepending it server-side")
        output = f"{DISCLOSURE_PREFIX} {output}"

    return {"output": output, "facility_cards": turn_cards or None, "tool_names_called": tool_names_called}

# ─── WebSocket Route ───────────────────────────────────────────
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    log_divider(f"SESSION {session_id[:12]}")
    log_ws(f"Client connected  │ session={session_id}")
 
    personalized_prompt = system_prompt + f"\n\nYour session_id for this conversation is: {session_id}"
 
    try:
        while True:
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
 
                await save_message(session_id, "user", user_message)
 
                # Runs the whole tool-calling loop as one traced "chat_turn"
                # run in LangSmith (see run_turn above).
                result = await run_turn(messages, session_id)
                output = result["output"]
                turn_cards = result["facility_cards"]
 
                await save_message(session_id, "assistant", output, facility_cards=turn_cards)
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
@app.get("/test-supabase")
async def test_supabase():
    """Quick test to verify Supabase lead write works."""
    from database import upsert_lead, db_pool
    log_api(f"Supabase test | pool_ready={db_pool is not None}")
    try:
        await upsert_lead({
            "lead_id": "TEST-001",
            "session_id": "test-session",
            "name": "Test User",
            "email": "test@test.com",
            "phone": "555-0000",
            "care_need": "Test lead from /test-supabase",
            "care_type": "Assisted Living",
            "location": "Chicago, IL",
            "age": "75", "gender": "", "living_arrangement": "",
            "conditions": "", "insurance": "", "budget": "",
            "notes": "Manual test", "status": "New", "email_sent": False,
        })
        log_api("Supabase test PASSED")
        return {"status": "ok", "message": "Lead written to Supabase successfully"}
    except Exception as e:
        log_error(f"Supabase test FAILED | {type(e).__name__}: {e}")
        return {"status": "error", "message": str(e)}
 
 
@app.get("/history/{session_id}")
async def get_history(session_id: str):
    log_api(f"Fetch history | session={session_id[:12]}")
    try:
        messages = await fetch_history(session_id)
        return {"messages": messages}
    except Exception as e:
        log_error(f"get_history failed | session={session_id[:12]} | {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch history")
 
 
@app.get("/sessions")
async def get_sessions():
    try:
        sessions = await get_all_sessions()
        return {"sessions": sessions}
    except Exception as e:
        log_error(f"get_sessions failed | {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch sessions")
 
 
class GenerateTitleRequest(BaseModel):
    session_id: str
    user_message: str
    ai_response: str
 
 
@app.post("/generate-title")
async def generate_title(req: GenerateTitleRequest):
    try:
        log_api(f"Generate title | session={req.session_id[:12]}")
        title_llm = ChatGroq(api_key=os.getenv("GROQ_API_KEY"), model="llama-3.3-70b-versatile", temperature=0.3)
        prompt = f"Generate a SHORT title and description for this chat: {req.user_message} | {req.ai_response}. Format: Title: [X] Description: [Y]"
        response = await title_llm.ainvoke(prompt)
        title, description = "New Conversation", ""
        for line in response.content.split("\n"):
            if line.startswith("Title:"): title = line.replace("Title:", "").strip()
            elif line.startswith("Description:"): description = line.replace("Description:", "").strip()
        await update_session_title(req.session_id, title, description)
        return {"title": title, "description": description}
    except Exception as e:
        log_error(f"generate_title failed | session={req.session_id[:12]} | {e}")
        return {"title": "New Conversation", "description": ""}
 
 
class DeleteSessionRequest(BaseModel):
    session_id: str
 
 
class UpdateLeadStatusRequest(BaseModel):
    lead_id: str
    status: str
 
 
@app.post("/delete-session")
async def delete_session_endpoint(req: DeleteSessionRequest):
    try:
        log_api(f"Delete session | session={req.session_id[:12]}")
        await delete_session(req.session_id)
        return {"status": "deleted"}
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