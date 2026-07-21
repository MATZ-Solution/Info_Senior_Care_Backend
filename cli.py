#!/usr/bin/env python3
"""
CLI harness for testing the Infomary chat agent without the frontend.

Runs the exact same per-turn agent loop as the `/ws/{session_id}` handler in
main.py (same LLM, same tools, same leaked-tool-call retry/dedupe/disclosure
handling, same "rebuild messages from plain history each turn" approach) but
reads/writes the terminal instead of a WebSocket, and doesn't persist to
Postgres.

Usage:
    uv run cli.py
    python cli.py

Commands inside the REPL:
    /reset    start a new session (fresh session_id, clears history)
    /history  print the plain user/assistant history sent to the LLM
    /exit     quit
"""
import asyncio
import json
import os
import re
import time
import uuid

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_groq import ChatGroq

from database import close_db_pool, init_db_pool
from system_prompt.instructions import system_instructions
from tools.agent_tools import facility_search, google_search, save_lead
from tools.explore_mode import ensure_facility_search_ready
from tools.facility_search.search import DISCLOSURE_PREFIX

load_dotenv()

# Same cap as main.py's websocket handler -- last N user+assistant turns
# reconstructed into context per LLM call.
MAX_HISTORY_MESSAGES = 20

_LEAKED_TOOL_CALL_RE = re.compile(r'^\s*(?:<function[=>])?\s*(?:save_lead|google_search|facility_search)\s*\{')


def _looks_like_leaked_tool_call(content: str) -> bool:
    return bool(_LEAKED_TOOL_CALL_RE.match(content or ""))


llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="openai/gpt-oss-120b",
    temperature=0.1,
).bind_tools([google_search, save_lead, facility_search])


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


async def run_turn(system_prompt: str, history: list[dict], user_message: str) -> tuple[str, list[dict]]:
    """Runs one full agent turn (including any tool-calling rounds) and
    returns (assistant_text, facility_cards). Mirrors websocket_endpoint in
    main.py: `messages` is rebuilt fresh from plain-text `history` each call,
    tool-call/tool-response messages never leak into the persisted history."""
    messages: list = [SystemMessage(content=system_prompt)]
    for msg in history[-MAX_HISTORY_MESSAGES:]:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=user_message))

    response = None
    turn_cards: list[dict] = []
    called_this_turn: dict = {}
    disclosure_required = False

    for _ in range(5):
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            err_text = str(e).lower()
            if "tool call validation failed" in err_text or "tool_use_failed" in err_text:
                print("[warn] malformed tool-call generation -- retrying once")
                response = await llm.ainvoke(messages)
            else:
                raise

        if not (hasattr(response, "tool_calls") and response.tool_calls) \
                and _looks_like_leaked_tool_call(response.content or ""):
            print("[warn] tool call leaked as plain text -- retrying once")
            response = await llm.ainvoke(messages)

        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                print(f"[tool] {tool_name} | {json.dumps(tool_args, default=str)[:160]}")
                signature = (tool_name, json.dumps(tool_args, sort_keys=True, default=str))
                if signature in called_this_turn:
                    print(f"[warn] {tool_name} duplicate call this turn -- reusing prior result")
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
                    elif tool_name == "save_lead":
                        result = await save_lead.ainvoke(tool_args)
                    elif tool_name == "facility_search":
                        tool_message = await facility_search.ainvoke(tc)
                        result = tool_message.content
                        if tool_message.artifact:
                            turn_cards.extend(tool_message.artifact)
                        if result.startswith(DISCLOSURE_PREFIX):
                            disclosure_required = True
                    else:
                        result = "Unknown tool"
                        print(f"[warn] unknown tool: {tool_name}")
                    ms = int((time.time() - t_tool) * 1000)
                    print(f"[tool] {tool_name} done | {ms}ms")
                    called_this_turn[signature] = str(result)
                    messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
                except Exception as e:
                    print(f"[error] tool {tool_name} failed: {e}")
                    messages.append(ToolMessage(content=str(e), tool_call_id=tc["id"]))
        else:
            break

    output = response.content if response else "Something went wrong, please try again."
    if _looks_like_leaked_tool_call(output):
        output = "Sorry, I had trouble with that -- could you try rephrasing?"
    if disclosure_required and DISCLOSURE_PREFIX.lower() not in output.lower():
        output = f"{DISCLOSURE_PREFIX} {output}"

    return output, turn_cards


def _new_session() -> tuple[str, str]:
    session_id = str(uuid.uuid4())
    prompt = system_instructions + (
        f"\n\nYour session_id for this conversation is: {session_id}\n"
        "You MUST pass this exact session_id in every single save_lead tool call."
    )
    print(f"[session] {session_id}")
    return session_id, prompt


async def main() -> None:
    print("Connecting to database and provisioning facility search...")
    await init_db_pool()
    await ensure_facility_search_ready()
    print("Ready. Type a message, or /reset /history /exit.\n")

    session_id, system_prompt = _new_session()
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
                session_id, system_prompt = _new_session()
                history = []
                continue
            if user_message == "/history":
                for m in history:
                    print(f"  {m['role']}: {m['content'][:200]}")
                continue

            try:
                t_start = time.time()
                output, cards = await run_turn(system_prompt, history, user_message)
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
