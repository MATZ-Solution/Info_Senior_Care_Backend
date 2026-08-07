#!/usr/bin/env python3
"""
CLI harness for testing the Infomary chat agent without the frontend.

Imports `run_turn` and `system_prompt` directly from app.main and calls them
the exact same way the `/ws/{session_id}` WebSocket handler does -- there is
only one implementation of the tool-calling loop (app.main.run_turn), not a
separately-maintained copy in this file. This reads/writes the terminal
instead of a WebSocket, and doesn't persist chat messages to Postgres
(facility_search itself still needs the DB pool for facility data, which is
why init_db_pool() is still called below).

Note: importing app.main also builds/configures the actual FastAPI app
(CORS, Sentry, rate limiter, routers) as a side effect -- that's the cost of
reusing its code directly instead of duplicating it. No server actually
starts from that import, so it's safe to do here.

Usage:
    uv run cli.py
    python cli.py

Commands inside the REPL:
    /reset    start a new session (fresh session_id, clears history)
    /history  print the plain user/assistant history sent to the LLM
    /exit     quit
"""
import asyncio
import time
import uuid

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.main import run_turn, system_prompt
from database import close_db_pool, init_db_pool
from tools.explore_mode import ensure_facility_search_ready

load_dotenv()

# Same cap app.main's websocket handler uses -- last N user+assistant turns
# reconstructed into context per LLM call.
MAX_HISTORY_MESSAGES = 20


def _print_cards(cards: list[dict]) -> None:
    if not cards:
        return
    print(f"\n--- {len(cards)} facility card(s) ---")
    for i, c in enumerate(cards, 1):
        if c.get("source") == "cms_certified":
            loc = ", ".join(x for x in (c.get("city"), c.get("state")) if x)
            print(f"[{i}] CERTIFIED  {c.get('name')} | {c.get('facility_type_label')} | {loc} | {c.get('phone', '')}")
            if c.get("highlight"):
                print(f"     {c['highlight']}")
        else:
            print(f"[{i}] WEB        {c.get('title')} | {c.get('url')}")
            if c.get("snippet"):
                print(f"     {c['snippet']}")
    print("---\n")


def _build_messages(session_prompt: str, history: list[dict], user_message: str) -> list:
    """Same message-building shape as app.main.websocket_endpoint -- kept
    here rather than imported since it's plain list construction, not agent
    logic. The actual tool-calling loop (the part worth sharing) is
    app.main.run_turn itself."""
    messages: list = [SystemMessage(content=session_prompt)]
    for msg in history[-MAX_HISTORY_MESSAGES:]:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_message))
    return messages


def _new_session() -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    prompt = system_prompt + f"\n\nYour session_id for this conversation is: {session_id}"
    print(f"[session] {session_id}")
    return session_id, prompt


async def main() -> None:
    print("Connecting to database and provisioning facility search...")
    await init_db_pool()
    await ensure_facility_search_ready()
    print("Ready. Type a message, or /reset /history /exit.\n")

    session_id, session_prompt = _new_session()
    history: list[dict] = []

    try:
        while True:
            try:
                user_message = await asyncio.to_thread(input, "you> ")
            except EOFError:
                break
            user_message = user_message.strip()
            if not user_message:
                continue
            if user_message == "/exit":
                break
            if user_message == "/reset":
                session_id, session_prompt = _new_session()
                history = []
                continue
            if user_message == "/history":
                for m in history:
                    print(f"  {m['role']}: {m['content'][:200]}")
                continue

            try:
                t_start = time.time()
                messages = _build_messages(session_prompt, history, user_message)
                result = await run_turn(messages, session_id)
                output = result["output"]
                cards = result["facility_cards"] or []
                ms = int((time.time() - t_start) * 1000)
                history.append({"role": "user", "content": user_message})
                history.append({"role": "assistant", "content": output})
                print(f"\nbot> {output}")
                _print_cards(cards)
                print(f"[{ms}ms]\n")
            except Exception as e:
                print(f"[error] turn failed: {e}")
    finally:
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
