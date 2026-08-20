"""
Pins down the chat orchestration behavior that must survive the migration
of run_turn from a hand-rolled `llm.bind_tools([...])` loop to
`create_agent`. See the migration plan for context.

_baseline_run_turn below is a deliberate, frozen copy of the OLD (working,
pre-create_agent) loop reconstructed from `git show HEAD:app/main.py` --
NOT an import from app.main, since app.main's on-disk run_turn is the
broken, mid-migration version this whole test file exists to guard
against. As migration steps land in app.main, later steps in this file
switch to exercising the real thing and this baseline stops being the
system under test -- it stays only as the documented "what must survive"
reference.
"""
import asyncio
import json
import re
import time
import uuid
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from tools.facility_search.search import DISCLOSURE_PREFIX

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------
# Fake tool-calling chat model -- scripted AIMessage/exception sequence,
# `bind_tools` is a no-op since we never rely on real provider tool-schema
# translation here (verified separately against the real ChatGroq wiring
# in Step 8's manual smoke test).
# --------------------------------------------------------------------------
class FakeToolCallingModel(BaseChatModel):
    responses: list[Any] = []
    calls: list[list[BaseMessage]] = []
    _i: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, messages: list[BaseMessage]):
        self.calls.append(messages)
        item = self.responses[self._i]
        self._i += 1
        if isinstance(item, BaseException):
            raise item
        return item

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._next(messages)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        msg = self._next(messages)
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-model"


_LEAKED_TOOL_CALL_RE = re.compile(r'^\s*(?:<function[=>])?\s*(?:google_search|facility_search)\s*\{')


def _looks_like_leaked_tool_call(content: str) -> bool:
    return bool(_LEAKED_TOOL_CALL_RE.match(content or ""))


# --------------------------------------------------------------------------
# Frozen baseline loop -- verbatim port of app/main.py's run_turn as of
# `git show HEAD:app/main.py` (the last known-working, pre-create_agent
# commit), generalized only to take `llm`/`tools` as parameters instead of
# reading module globals.
# --------------------------------------------------------------------------
async def _baseline_run_turn(llm, tools: dict[str, StructuredTool], messages: list, session_id: str) -> dict:
    response = None
    t_start = time.time()
    turn_cards = []
    called_this_turn = {}
    disclosure_required = False
    tool_names_called = []

    for _ in range(6):
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            err_text = str(e).lower()
            if "tool call validation failed" in err_text or "tool_use_failed" in err_text:
                response = await llm.ainvoke(messages)
            else:
                raise

        if not (hasattr(response, "tool_calls") and response.tool_calls) \
                and _looks_like_leaked_tool_call(response.content or ""):
            response = await llm.ainvoke(messages)

        if hasattr(response, "tool_calls") and response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                tool_name = tc["name"]
                tool_args = tc["args"]
                tool_names_called.append(tool_name)
                signature = (tool_name, json.dumps(tool_args, sort_keys=True, default=str))
                if signature in called_this_turn:
                    messages.append(ToolMessage(content=called_this_turn[signature], tool_call_id=tc["id"]))
                    continue
                try:
                    if tool_name == "google_search":
                        tool_message = await tools["google_search"].ainvoke(tc)
                        result = tool_message.content
                        if tool_message.artifact:
                            turn_cards.extend(
                                {"source": "not_certified", "title": r.get("title"),
                                 "snippet": r.get("snippet"), "url": r.get("link")}
                                for r in tool_message.artifact
                            )
                    elif tool_name == "save_lead":
                        result = await tools["save_lead"].ainvoke(tool_args)
                    elif tool_name == "facility_search":
                        tool_message = await tools["facility_search"].ainvoke(tc)
                        result = tool_message.content
                        if tool_message.artifact:
                            turn_cards.extend(tool_message.artifact)
                        if result.startswith(DISCLOSURE_PREFIX):
                            disclosure_required = True
                    else:
                        result = "Unknown tool"
                    called_this_turn[signature] = str(result)
                    messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
                except Exception as e:
                    messages.append(ToolMessage(content=str(e), tool_call_id=tc["id"]))
        else:
            break

    output = response.content if response else "Something went wrong, please try again."
    if _looks_like_leaked_tool_call(output):
        output = "Sorry, I had trouble with that -- could you try rephrasing?"
    if disclosure_required and DISCLOSURE_PREFIX.lower() not in output.lower():
        output = f"{DISCLOSURE_PREFIX} {output}"
    if turn_cards:
        output = ""

    return {"output": output, "facility_cards": turn_cards or None, "tool_names_called": tool_names_called}


# --------------------------------------------------------------------------
# Fake facility_search / google_search tools -- controllable content,
# artifact, and exception, matching the real tools' StructuredTool shape
# (response_format="content_and_artifact") without any real search backend.
# --------------------------------------------------------------------------
class _SearchArgs(BaseModel):
    query: str = ""


class _CallLog(list):
    """Plain list of the args each invocation was called with -- len() is the call count."""


def make_fake_search_tool(name: str, script: list) -> tuple[StructuredTool, _CallLog]:
    """`script[i]` is either an (content, artifact) tuple or an Exception instance, indexed by call count."""
    call_log = _CallLog()

    async def _run(query: str = ""):
        call_log.append(query)
        item = script[min(len(call_log) - 1, len(script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    tool = StructuredTool.from_function(
        coroutine=_run, name=name, description="fake", args_schema=_SearchArgs,
        response_format="content_and_artifact",
    )
    return tool, call_log


def make_real_save_lead_tool():
    from tools.agent_tools import save_lead
    return save_lead


def make_ai_tool_call(tool_name: str, args: dict, call_id: str | None = None) -> AIMessage:
    return AIMessage(content="", tool_calls=[ToolCall(name=tool_name, args=args, id=call_id or f"call_{uuid.uuid4().hex[:8]}")])


@pytest.fixture(autouse=True)
def _reset_save_lead_sessions():
    """tools.agent_tools._sessions is a module-level dict -- isolate tests."""
    import tools.agent_tools as agent_tools
    agent_tools._sessions.clear()
    yield
    agent_tools._sessions.clear()


@pytest.fixture
def stub_persistence(monkeypatch):
    """Stub database.upsert_lead and resend.Emails.send so save_lead never touches real infra."""
    import database

    captured = {"upserts": [], "emails": []}

    async def fake_upsert_lead(lead: dict):
        captured["upserts"].append(dict(lead))

    monkeypatch.setattr(database, "db_pool", object(), raising=False)
    monkeypatch.setattr(database, "upsert_lead", fake_upsert_lead)

    import resend
    def fake_send(payload):
        captured["emails"].append(payload)
        return {"id": "fake"}
    monkeypatch.setattr(resend.Emails, "send", fake_send)

    return captured


# ==========================================================================
# Tests -- each one pins a behavior that must survive the create_agent
# migration. Numbers match the migration plan's Step 1 list.
# ==========================================================================

async def test_1_plain_conversation_no_tools():
    llm = FakeToolCallingModel(responses=[AIMessage(content="Hi there, how can I help?")])
    result = await _baseline_run_turn(llm, {}, [], "sess-1")
    assert result["output"] == "Hi there, how can I help?"
    assert result["facility_cards"] is None
    assert result["tool_names_called"] == []


async def test_2_disclosure_prefix_enforced_even_if_model_omits_it():
    facility_tool, _ = make_fake_search_tool("facility_search", [(f"{DISCLOSURE_PREFIX} Found some general options.", [])])
    llm = FakeToolCallingModel(responses=[
        make_ai_tool_call("facility_search", {"city": "Nowhere"}),
        AIMessage(content="Here's what I found, no disclosure mentioned."),
    ])
    result = await _baseline_run_turn(llm, {"facility_search": facility_tool}, [], "sess-2")
    assert DISCLOSURE_PREFIX.lower() in result["output"].lower()


async def test_3_artifact_becomes_cards_and_blanks_output():
    facility_tool, _ = make_fake_search_tool(
        "facility_search",
        [("some content", [{"source": "cms_certified", "name": "Sunrise Manor"}])],
    )
    llm = FakeToolCallingModel(responses=[
        make_ai_tool_call("facility_search", {"city": "Chicago"}),
        AIMessage(content="Here are some options for you."),
    ])
    result = await _baseline_run_turn(llm, {"facility_search": facility_tool}, [], "sess-3")
    assert result["facility_cards"] == [{"source": "cms_certified", "name": "Sunrise Manor"}]
    assert result["output"] == ""


async def test_4a_sequential_duplicate_call_invoked_once_with_correct_tool_call_id():
    facility_tool, call_log = make_fake_search_tool("facility_search", [("first result", [])])
    llm = FakeToolCallingModel(responses=[
        make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_A"),
        make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_B"),
        AIMessage(content="done"),
    ])
    messages: list = []
    result = await _baseline_run_turn(llm, {"facility_search": facility_tool}, messages, "sess-4a")
    assert len(call_log) == 1, "duplicate call must not re-invoke the underlying tool"
    assert result["tool_names_called"] == ["facility_search", "facility_search"]
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert [tm.tool_call_id for tm in tool_messages] == ["call_A", "call_B"], (
        "the reused (deduped) ToolMessage must carry the CURRENT call's id (call_B), "
        "not the id of the call whose content was cached (call_A) -- see migration plan finding #4"
    )
    assert tool_messages[0].content == tool_messages[1].content == "first result"


async def test_4b_parallel_duplicate_in_one_aimessage_does_not_crash():
    """
    Known scope boundary (migration plan finding #6): this baseline dispatches
    tool_calls from a single AIMessage sequentially (a plain `for` loop, no
    concurrency), so it trivially dedupes correctly here. create_agent's own
    ToolNode executes same-message tool_calls concurrently -- verified
    separately in the migration plan's research -- which is the actual risk
    this test name refers to. This test only pins that the baseline doesn't
    regress; the concurrent case is exercised for real once create_agent
    lands (Step 5).
    """
    facility_tool, call_log = make_fake_search_tool("facility_search", [("only result", [])])
    ai_two_calls = AIMessage(content="", tool_calls=[
        ToolCall(name="facility_search", args={"query": "x"}, id="call_1"),
        ToolCall(name="facility_search", args={"query": "x"}, id="call_2"),
    ])
    llm = FakeToolCallingModel(responses=[ai_two_calls, AIMessage(content="done")])
    messages: list = []
    result = await _baseline_run_turn(llm, {"facility_search": facility_tool}, messages, "sess-4b")
    assert len(call_log) == 1
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert [tm.tool_call_id for tm in tool_messages] == ["call_1", "call_2"]


async def test_5_tool_use_failed_exception_retries_once():
    llm = FakeToolCallingModel(responses=[
        RuntimeError("Groq 400: tool_use_failed"),
        AIMessage(content="recovered"),
    ])
    result = await _baseline_run_turn(llm, {}, [], "sess-5")
    assert result["output"] == "recovered"
    assert len(llm.calls) == 2


async def test_6_leaked_tool_call_text_retries_once():
    llm = FakeToolCallingModel(responses=[
        AIMessage(content='facility_search{"city": "Chicago"}'),
        AIMessage(content="a real answer"),
    ])
    result = await _baseline_run_turn(llm, {}, [], "sess-6")
    assert result["output"] == "a real answer"
    assert len(llm.calls) == 2


async def test_7_save_lead_persists_and_is_recorded_in_tool_names_called(stub_persistence):
    save_lead_tool = make_real_save_lead_tool()
    llm = FakeToolCallingModel(responses=[
        make_ai_tool_call("save_lead", {"session_id": "sess-7", "name": "Bob", "phone": "5551234567"}),
        AIMessage(content="Thanks, Bob!"),
    ])
    result = await _baseline_run_turn(llm, {"save_lead": save_lead_tool}, [], "sess-7")
    assert "save_lead" in result["tool_names_called"]
    # _persist_lead upserts once on save, then again once the notification
    # email fires successfully (to flip email_sent=True) -- see
    # tools/agent_tools.py:239-270. Both must carry the right session/name.
    assert len(stub_persistence["upserts"]) == 2
    assert all(u["session_id"] == "sess-7" and u["name"] == "Bob" for u in stub_persistence["upserts"])
    assert stub_persistence["upserts"][-1]["email_sent"] is True


async def test_9_tool_exception_does_not_crash_the_turn():
    facility_tool, _ = make_fake_search_tool("facility_search", [RuntimeError("simulated API timeout")])
    llm = FakeToolCallingModel(responses=[
        make_ai_tool_call("facility_search", {"city": "Chicago"}),
        AIMessage(content="Sorry, something went wrong with that search."),
    ])
    result = await _baseline_run_turn(llm, {"facility_search": facility_tool}, [], "sess-9")
    assert result["output"]  # completed with *some* output, did not raise


@pytest.mark.xfail(
    strict=True,
    reason=(
        "NOT a baseline-preserving test -- migration plan finding #3: the OLD hand-rolled loop "
        "passed tc['args'] straight to save_lead.ainvoke() with whatever session_id the model "
        "supplied, no override. This is a new correctness guarantee introduced by the migration "
        "(Step 3's SessionIdOverrideMiddleware), so it is EXPECTED to fail against this baseline. "
        "Once create_agent + SessionIdOverrideMiddleware exist, this same assertion is re-run "
        "against the real thing and must pass without the xfail marker."
    ),
)
async def test_8_save_lead_session_id_is_never_trusted_from_the_model(stub_persistence):
    save_lead_tool = make_real_save_lead_tool()
    real_session_id = "REAL-abc123"
    llm = FakeToolCallingModel(responses=[
        make_ai_tool_call("save_lead", {"session_id": "HALLUCINATED-999", "name": "Bob", "phone": "5551234567"}),
        AIMessage(content="Thanks, Bob!"),
    ])
    await _baseline_run_turn(llm, {"save_lead": save_lead_tool}, [], real_session_id)
    assert len(stub_persistence["upserts"]) == 1
    assert stub_persistence["upserts"][0]["session_id"] == real_session_id, (
        "the persisted lead must use the real, server-known session_id, never whatever "
        "the model happened to put in its tool-call args"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "NOT a baseline-preserving test -- migration plan finding #6 / reviewer point 3: in the "
        "OLD loop, the leaked-tool-call retry (`if not tool_calls and _looks_like_leaked_tool_call`) "
        "is NOT wrapped in the try/except that catches tool_use_failed-style exceptions, so a real "
        "exception raised during that specific retry call propagates uncaught out of run_turn. This "
        "is exactly the composition gap the migration's ModelRetryMiddleware (outer) + "
        "LeakedToolCallRetryMiddleware (inner) pairing (Step 6) is meant to close -- expected to fail "
        "here, and re-run against the real middleware stack once it exists."
    ),
)
async def test_10_combined_leaked_call_and_real_exception_compose_without_crashing():
    llm = FakeToolCallingModel(responses=[
        AIMessage(content='facility_search{"city": "Chicago"}'),  # looks leaked -> triggers a bare retry
        RuntimeError("Groq 400: tool_use_failed"),                # that retry itself blows up
        AIMessage(content="finally recovered"),
    ])
    result = await _baseline_run_turn(llm, {}, [], "sess-10")
    assert result["output"] == "finally recovered"
