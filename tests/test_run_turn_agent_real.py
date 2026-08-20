"""
Exercises the REAL app.main.run_turn as it evolves through the
create_agent migration (see
C:\\Users\\HP\\.claude\\plans\\so-a-step-by-wiggly-dewdrop.md). Unlike
tests/test_run_turn_agent.py's frozen baseline (which pins what the OLD
code did and never changes), this file targets the actual migration
target and is expected to have xfail markers removed step by step as
each piece of middleware lands.

Same scenario numbering as the migration plan's Step 1 list.
"""
import asyncio
from typing import Any

import httpx
import groq
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolCall, ToolMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.middlewares.agent_middleware import GENERIC_FALLBACK_TEXT, TOTAL_TOOL_FAILURE_TEXT, ToolErrorSafetyNetMiddleware
from tools.agent_tools import UserSafeToolError
from tools.facility_search.search import DISCLOSURE_PREFIX


def make_groq_connection_error() -> groq.APIConnectionError:
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return groq.APIConnectionError(request=req)


def make_groq_status_error(cls: type, status_code: int, message: str = "error"):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    resp = httpx.Response(status_code, request=req)
    return cls(message, response=resp, body=None)

pytestmark = pytest.mark.asyncio


class FakeToolCallingModel(BaseChatModel):
    responses: list[Any] = []
    calls: list[list[BaseMessage]] = []
    _i: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    def _next(self, messages):
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


class _SearchArgs(BaseModel):
    query: str = ""


class _CallLog(list):
    pass


def make_fake_search_tool(name: str, script: list) -> tuple[StructuredTool, _CallLog]:
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


def make_ai_tool_call(tool_name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[ToolCall(name=tool_name, args=args, id=call_id)])


@pytest.fixture(autouse=True)
def _reset_save_lead_sessions():
    import tools.agent_tools as agent_tools
    agent_tools._sessions.clear()
    yield
    agent_tools._sessions.clear()


@pytest.fixture
def stub_persistence(monkeypatch):
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


@pytest.fixture
def patch_agent(monkeypatch):
    """
    Swaps app.main.infoAgent for a fresh create_agent built on a scripted
    FakeToolCallingModel + whichever tools this test needs -- app.main.run_turn
    always reads the module-level `infoAgent`, so patching that (not passing
    the agent in) is what actually exercises the real run_turn code path.
    """
    import app.main as main_module

    def _patch(responses: list, extra_tools: dict[str, StructuredTool] | None = None):
        from tools.agent_tools import save_lead
        tools_by_name = {"save_lead": save_lead}
        if extra_tools:
            tools_by_name.update(extra_tools)
        llm = FakeToolCallingModel(responses=responses)
        # Speed up any real backoff delays (ModelRetryMiddleware.awrap_model_call
        # does `await asyncio.sleep(delay)`, which is real wall-clock time even
        # though it's non-blocking) so retry-exhaustion tests don't take
        # seconds -- mutates the SAME shared instances app.main uses, via
        # monkeypatch so it's auto-restored after each test.
        for mw in main_module.infoAgent_middleware:
            if isinstance(mw, ModelRetryMiddleware):
                monkeypatch.setattr(mw, "initial_delay", 0.01)
                monkeypatch.setattr(mw, "max_delay", 0.05)
            # Same idea for ToolErrorSafetyNetMiddleware's per-tool-call
            # timeout -- tests that need a hung tool to actually trip it
            # shouldn't have to wait TOOL_CALL_TIMEOUT_SECONDS (20s) for it.
            if isinstance(mw, ToolErrorSafetyNetMiddleware):
                monkeypatch.setattr(mw, "timeout_seconds", 0.1)
        agent = create_agent(
            model=llm,
            tools=list(tools_by_name.values()),
            middleware=list(main_module.infoAgent_middleware),
            context_schema=main_module.AgentContext,
        )
        monkeypatch.setattr(main_module, "infoAgent", agent)
        return llm, main_module

    return _patch


def make_messages(text: str = "hi") -> list:
    return [SystemMessage(content="system prompt"), HumanMessage(content=text)]


# ==========================================================================
# Step 2 status: core create_agent plumbing only, no middleware.
# ==========================================================================

async def test_1_plain_conversation_no_tools(patch_agent):
    _, main_module = patch_agent([AIMessage(content="Hi there, how can I help?")])
    result = await main_module.run_turn(make_messages(), "sess-1")
    assert result["output"] == "Hi there, how can I help?"
    assert result["tool_names_called"] == []


async def test_2_disclosure_prefix_enforced_even_if_model_omits_it(patch_agent):
    facility_tool, _ = make_fake_search_tool("facility_search", [(f"{DISCLOSURE_PREFIX} Found some general options.", [])])
    _, main_module = patch_agent(
        [make_ai_tool_call("facility_search", {"city": "Nowhere"}), AIMessage(content="Here's what I found.")],
        extra_tools={"facility_search": facility_tool},
    )
    result = await main_module.run_turn(make_messages(), "sess-2")
    assert DISCLOSURE_PREFIX.lower() in result["output"].lower()


async def test_3_artifact_becomes_cards_and_blanks_output(patch_agent):
    facility_tool, _ = make_fake_search_tool(
        "facility_search", [("some content", [{"source": "cms_certified", "name": "Sunrise Manor"}])],
    )
    _, main_module = patch_agent(
        [make_ai_tool_call("facility_search", {"city": "Chicago"}), AIMessage(content="Here are some options.")],
        extra_tools={"facility_search": facility_tool},
    )
    result = await main_module.run_turn(make_messages(), "sess-3")
    assert result["facility_cards"] == [{"source": "cms_certified", "name": "Sunrise Manor"}]
    assert result["output"] == ""


async def test_4a_sequential_duplicate_call_invoked_once(patch_agent):
    facility_tool, call_log = make_fake_search_tool("facility_search", [("first result", [])])
    _, main_module = patch_agent(
        [
            make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_A"),
            make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_B"),
            AIMessage(content="done"),
        ],
        extra_tools={"facility_search": facility_tool},
    )
    await main_module.run_turn(make_messages(), "sess-4a")
    assert len(call_log) == 1, "duplicate call must not re-invoke the underlying tool"


async def test_5_tool_use_failed_exception_retries_once(patch_agent):
    llm, main_module = patch_agent([RuntimeError("Groq 400: tool_use_failed"), AIMessage(content="recovered")])
    result = await main_module.run_turn(make_messages(), "sess-5")
    assert result["output"] == "recovered"


async def test_6_leaked_tool_call_text_retries_once(patch_agent):
    llm, main_module = patch_agent([
        AIMessage(content='facility_search{"city": "Chicago"}'),
        AIMessage(content="a real answer"),
    ])
    result = await main_module.run_turn(make_messages(), "sess-6")
    assert result["output"] == "a real answer"


async def test_7_save_lead_persists_and_is_recorded(patch_agent, stub_persistence):
    _, main_module = patch_agent([
        make_ai_tool_call("save_lead", {"session_id": "sess-7", "name": "Bob", "phone": "5551234567"}),
        AIMessage(content="Thanks, Bob!"),
    ])
    result = await main_module.run_turn(make_messages(), "sess-7")
    assert "save_lead" in result["tool_names_called"]
    assert any(u["session_id"] == "sess-7" and u["name"] == "Bob" for u in stub_persistence["upserts"])


async def test_8_save_lead_session_id_is_never_trusted_from_the_model(patch_agent, stub_persistence):
    real_session_id = "REAL-abc123"
    _, main_module = patch_agent([
        make_ai_tool_call("save_lead", {"session_id": "HALLUCINATED-999", "name": "Bob", "phone": "5551234567"}),
        AIMessage(content="Thanks, Bob!"),
    ])
    await main_module.run_turn(make_messages(), real_session_id)
    assert any(u["session_id"] == real_session_id for u in stub_persistence["upserts"]), (
        "the persisted lead must use the real, server-known session_id, never whatever "
        "the model happened to put in its tool-call args"
    )


async def test_9_tool_exception_does_not_crash_the_turn(patch_agent):
    facility_tool, _ = make_fake_search_tool("facility_search", [RuntimeError("simulated API timeout")])
    _, main_module = patch_agent(
        [make_ai_tool_call("facility_search", {"city": "Chicago"}), AIMessage(content="Sorry, something went wrong.")],
        extra_tools={"facility_search": facility_tool},
    )
    # Call infoAgent directly (not run_turn) so status/tool_failures -- which
    # run_turn deliberately doesn't expose in its return dict -- are visible.
    result = await main_module.infoAgent.ainvoke(
        {"messages": make_messages()}, context={"session_id": "sess-9"},
    )
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status == "error"
    assert "do not present this as a success" in tool_messages[0].content.lower()
    failures = result.get("tool_failures") or []
    assert any(f["tool"] == "facility_search" for f in failures)
    # run_turn itself must still degrade gracefully on top of this.
    _, main_module = patch_agent(
        [make_ai_tool_call("facility_search", {"city": "Chicago"}), AIMessage(content="Sorry, something went wrong.")],
        extra_tools={"facility_search": facility_tool},
    )
    run_turn_result = await main_module.run_turn(make_messages(), "sess-9b")
    assert run_turn_result["output"]


async def test_10_combined_leaked_call_and_real_exception_compose_without_crashing(patch_agent):
    _, main_module = patch_agent([
        AIMessage(content='facility_search{"city": "Chicago"}'),
        RuntimeError("Groq 400: tool_use_failed"),
        AIMessage(content="finally recovered"),
    ])
    result = await main_module.run_turn(make_messages(), "sess-10")
    assert result["output"] == "finally recovered"


# ==========================================================================
# Provider/API error handling (see the "Provider/API error handling for
# infoAgent's model calls" section of the migration plan).
# ==========================================================================

async def test_11_rate_limit_retried_then_succeeds(patch_agent):
    _, main_module = patch_agent([
        make_groq_status_error(groq.RateLimitError, 429, "rate limited"),
        AIMessage(content="recovered after rate limit"),
    ])
    result = await main_module.run_turn(make_messages(), "sess-11")
    assert result["output"] == "recovered after rate limit"


async def test_12_authentication_error_fails_fast_with_no_retries(patch_agent, monkeypatch):
    llm, main_module = patch_agent([
        make_groq_status_error(groq.AuthenticationError, 401, "invalid api key"),
        AIMessage(content="should never be reached"),
    ])
    logged = []
    monkeypatch.setattr(main_module, "log_error", lambda msg: logged.append(msg))

    result = await main_module.run_turn(make_messages(), "sess-12")

    assert llm._i == 1, "an auth error must not consume any retry attempts -- retrying a bad key can't succeed"
    assert result["output"]  # some graceful fallback text -- exact copy is main.py's concern, not this test's
    assert result["facility_cards"] is None
    assert result["tool_names_called"] == []
    assert any("GROQ AUTHENTICATION FAILED" in m for m in logged), "auth failures must be logged distinctly/loudly"


async def test_13_provider_unavailable_exhausts_retries_then_degrades_gracefully(patch_agent):
    # Outer provider-retry middleware is configured max_retries=3, i.e. 4
    # total attempts (1 initial + 3 retries) -- script a failure for each.
    _, main_module = patch_agent([make_groq_status_error(groq.InternalServerError, 500, "down") for _ in range(4)])
    result = await main_module.run_turn(make_messages(), "sess-13")
    assert result["output"]  # completed with *some* graceful output
    assert result["output"] != ""
    assert result["facility_cards"] is None
    assert result["tool_names_called"] == []


async def test_14_connection_error_is_retried(patch_agent):
    _, main_module = patch_agent([
        make_groq_connection_error(),
        AIMessage(content="recovered after connection error"),
    ])
    result = await main_module.run_turn(make_messages(), "sess-14")
    assert result["output"] == "recovered after connection error"


# ==========================================================================
# Tool execution robustness (see the "Tool execution robustness for
# infoAgent" section of the migration plan).
# ==========================================================================

def make_hanging_tool(name: str = "facility_search") -> StructuredTool:
    async def _hangs(query: str = ""):
        await asyncio.sleep(999)
        return "never reached", []
    return StructuredTool.from_function(
        coroutine=_hangs, name=name, description="hangs", args_schema=_SearchArgs,
        response_format="content_and_artifact",
    )


async def test_15_tool_timeout_sets_error_status_and_content(patch_agent):
    hang_tool = make_hanging_tool()
    _, main_module = patch_agent(
        [make_ai_tool_call("facility_search", {"city": "Chicago"}), AIMessage(content="done")],
        extra_tools={"facility_search": hang_tool},
    )
    result = await asyncio.wait_for(
        main_module.infoAgent.ainvoke({"messages": make_messages()}, context={"session_id": "sess-15"}),
        timeout=5,  # test-level net -- patch_agent shrinks the real timeout to 0.1s
    )
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status == "error"
    assert "timed out" in tool_messages[0].content.lower()
    failures = result.get("tool_failures") or []
    assert any(f["tool"] == "facility_search" and f["reason"] == "timed out" for f in failures)


async def test_15b_tool_timeout_degrades_gracefully_via_run_turn(patch_agent):
    hang_tool = make_hanging_tool()
    _, main_module = patch_agent(
        [make_ai_tool_call("facility_search", {"city": "Chicago"}), AIMessage(content="Sorry, that timed out.")],
        extra_tools={"facility_search": hang_tool},
    )
    result = await asyncio.wait_for(main_module.run_turn(make_messages(), "sess-15b"), timeout=5)
    assert result["output"]


async def test_16_concurrent_duplicate_bounded_by_inner_timeout(patch_agent):
    """
    Validates the architectural claim ToolErrorSafetyNetMiddleware's design
    depends on: it sits innermost specifically so ToolCallDedupMiddleware's
    concurrent-duplicate guard (which awaits the first caller's in-flight
    Future with no timeout of its own) is bounded by the safety net's
    timeout rather than hanging forever alongside a genuinely hung tool.
    """
    hang_tool = make_hanging_tool()
    ai_two_calls = AIMessage(content="", tool_calls=[
        ToolCall(name="facility_search", args={"query": "x"}, id="call_1"),
        ToolCall(name="facility_search", args={"query": "x"}, id="call_2"),
    ])
    _, main_module = patch_agent([ai_two_calls, AIMessage(content="done")], extra_tools={"facility_search": hang_tool})

    result = await asyncio.wait_for(
        main_module.infoAgent.ainvoke({"messages": make_messages()}, context={"session_id": "sess-16"}),
        timeout=5,  # would fire (and fail the test) if either call hung forever
    )
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 2
    assert {tm.tool_call_id for tm in tool_messages} == {"call_1", "call_2"}
    assert all(tm.status == "error" for tm in tool_messages)


async def test_17_save_lead_db_failure_is_sanitized_and_skips_email(patch_agent, stub_persistence, monkeypatch):
    import database

    sensitive = "postgresql://user:pass@10.0.0.5:5432/db"

    async def failing_upsert(lead: dict):
        raise RuntimeError(f"connection to {sensitive} failed")

    monkeypatch.setattr(database, "upsert_lead", failing_upsert)

    _, main_module = patch_agent([
        make_ai_tool_call("save_lead", {"session_id": "sess-17", "name": "Bob", "phone": "5551234567"}),
        AIMessage(content="done"),
    ])

    result = await main_module.infoAgent.ainvoke(
        {"messages": make_messages()}, context={"session_id": "sess-17"},
    )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    save_lead_msg = next(m for m in tool_messages if m.name == "save_lead")
    assert "Lead saved" not in save_lead_msg.content
    assert sensitive not in save_lead_msg.content, "raw exception text must not leak into what the model sees"
    assert save_lead_msg.status == "error"

    assert stub_persistence["emails"] == [], "must not notify/confirm a lead that was never actually saved"

    failures = result.get("tool_failures") or []
    save_lead_failure = next(f for f in failures if f["tool"] == "save_lead")
    assert sensitive not in save_lead_failure["reason"], "sanitized reason must not leak the raw exception either"


async def test_18_sequential_duplicate_of_failed_call_retains_error_status(patch_agent):
    facility_tool, call_log = make_fake_search_tool("facility_search", [RuntimeError("boom")])
    _, main_module = patch_agent(
        [
            make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_A"),
            make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_B"),
            AIMessage(content="done"),
        ],
        extra_tools={"facility_search": facility_tool},
    )
    result = await main_module.infoAgent.ainvoke(
        {"messages": make_messages()}, context={"session_id": "sess-18"},
    )
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(call_log) == 1, "duplicate call must not re-invoke the failing tool"
    assert len(tool_messages) == 2
    assert [tm.tool_call_id for tm in tool_messages] == ["call_A", "call_B"]
    assert all(tm.status == "error" for tm in tool_messages), (
        "the deduped replay must also report status='error', not silently default back to 'success'"
    )


# ==========================================================================
# Final output validation (see the "Final output validation for infoAgent"
# section of the migration plan).
# ==========================================================================

async def test_19_total_tool_failure_overrides_claimed_success(patch_agent):
    facility_tool, _ = make_fake_search_tool("facility_search", [RuntimeError("boom")])
    _, main_module = patch_agent(
        [
            make_ai_tool_call("facility_search", {"city": "Chicago"}),
            AIMessage(content="Great, I found some options for you!"),
        ],
        extra_tools={"facility_search": facility_tool},
    )
    result = await main_module.run_turn(make_messages(), "sess-19")
    assert result["output"] == TOTAL_TOOL_FAILURE_TEXT


async def test_20_mixed_outcome_successful_search_no_override(patch_agent):
    facility_tool, _ = make_fake_search_tool("facility_search", [RuntimeError("boom")])
    google_tool, _ = make_fake_search_tool(
        "google_search", [("some info", [{"title": "A", "snippet": "B", "link": "C"}])],
    )
    model_final_text = "Here's what I found, though one search had trouble."
    _, main_module = patch_agent(
        [
            make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_A"),
            make_ai_tool_call("google_search", {"query": "ER near me"}, call_id="call_B"),
            AIMessage(content=model_final_text),
        ],
        extra_tools={"facility_search": facility_tool, "google_search": google_tool},
    )
    result = await main_module.infoAgent.ainvoke(
        {"messages": make_messages()}, context={"session_id": "sess-20"},
    )
    assert result["messages"][-1].content == model_final_text, "must not be overridden -- one tool call succeeded"
    assert result.get("turn_cards")


async def test_21_save_lead_success_plus_failed_search_no_override(patch_agent, stub_persistence):
    """
    The exact case flagged in review: save_lead produces no card at all, so
    a turn_cards-only condition would wrongly override this. status-based
    ground truth must not.
    """
    facility_tool, _ = make_fake_search_tool("facility_search", [RuntimeError("boom")])
    model_final_text = "I've saved your information successfully!"
    _, main_module = patch_agent(
        [
            make_ai_tool_call("save_lead", {"session_id": "sess-21", "name": "Bob", "phone": "5551234567"}, call_id="call_A"),
            make_ai_tool_call("facility_search", {"city": "Chicago"}, call_id="call_B"),
            AIMessage(content=model_final_text),
        ],
        extra_tools={"facility_search": facility_tool},
    )
    result = await main_module.infoAgent.ainvoke(
        {"messages": make_messages()}, context={"session_id": "sess-21"},
    )
    assert result["messages"][-1].content == model_final_text
    assert not result.get("turn_cards")


async def test_22_empty_content_no_cards_replaced(patch_agent):
    _, main_module = patch_agent([AIMessage(content="")])
    result = await main_module.run_turn(make_messages(), "sess-22")
    assert result["output"] == GENERIC_FALLBACK_TEXT


async def test_22b_empty_content_with_cards_left_alone(patch_agent):
    facility_tool, _ = make_fake_search_tool(
        "facility_search", [("data", [{"source": "cms_certified", "name": "X"}])],
    )
    _, main_module = patch_agent(
        [make_ai_tool_call("facility_search", {"city": "Chicago"}), AIMessage(content="")],
        extra_tools={"facility_search": facility_tool},
    )
    result = await main_module.infoAgent.ainvoke(
        {"messages": make_messages()}, context={"session_id": "sess-22b"},
    )
    assert result["messages"][-1].content == "", "blank content is correct/intentional when cards are present"
    assert result.get("turn_cards")


async def test_23_internal_error_leakage_replaced(patch_agent):
    leaking_text = 'Traceback (most recent call last):\n  File "x.py", line 1\nValueError: bad'
    _, main_module = patch_agent([AIMessage(content=leaking_text)])
    result = await main_module.run_turn(make_messages(), "sess-23")
    assert result["output"] == GENERIC_FALLBACK_TEXT
    assert "Traceback" not in result["output"]


async def test_23b_ordinary_error_wording_not_flagged(patch_agent):
    ordinary = "There was an error processing your request earlier, but let's try again."
    _, main_module = patch_agent([AIMessage(content=ordinary)])
    result = await main_module.run_turn(make_messages(), "sess-23b")
    assert result["output"] == ordinary
