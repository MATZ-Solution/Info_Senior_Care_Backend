"""
create_agent middleware and supporting state/context schemas for infoAgent
(see app/main.py). Each middleware here re-expresses one piece of behavior
the old hand-rolled run_turn loop used to do inline -- see
C:\\Users\\HP\\.claude\\plans\\so-a-step-by-wiggly-dewdrop.md for the
migration this file is part of, including the empirical findings each
middleware exists to address.
"""
import asyncio
import json
import operator
import re
from typing import Annotated

from typing_extensions import NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRetryMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from logger import log_error
from tools.agent_tools import UserSafeToolError
from tools.facility_search.search import DISCLOSURE_PREFIX

_LEAKED_TOOL_CALL_RE = re.compile(r'^\s*(?:<function[=>])?\s*(?:google_search|facility_search)\s*\{')


def _looks_like_leaked_tool_call(content: str) -> bool:
    return bool(_LEAKED_TOOL_CALL_RE.match(content or ""))


class AgentContext(TypedDict):
    """Per-invocation context threaded through infoAgent.ainvoke(..., context=...)."""

    session_id: str


def _or_reducer(a: bool, b: bool) -> bool:
    return a or b


class CardState(AgentState):
    """
    Extends AgentState with turn_cards/disclosure_required. Declared as a
    middleware-level state_schema (not passed to create_agent directly) --
    verified this composes correctly with context_schema and other
    middleware's own state in the installed langchain==1.3.2 (an older,
    closed LangChain issue about state_schema+middleware being mutually
    exclusive applies to a different code path, langgraph.prebuilt.react_agent,
    not langchain.agents.create_agent as used here).
    """

    turn_cards: NotRequired[Annotated[list, operator.add]]
    disclosure_required: NotRequired[Annotated[bool, _or_reducer]]


def _merge_dicts(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class DedupState(AgentState):
    """Tracks (name, args) signatures already executed this graph run."""

    seen_tool_calls: NotRequired[Annotated[dict, _merge_dicts]]


def _tool_call_signature(tool_call: dict) -> str:
    return json.dumps([tool_call["name"], tool_call["args"]], sort_keys=True, default=str)


class ToolCallDedupMiddleware(AgentMiddleware):
    """
    Skips re-invoking a tool when this exact (name, args) call already ran
    earlier in the same graph run, reusing the cached content/artifact
    instead. Must sit outermost of the wrap_tool_call chain (before
    SessionIdOverrideMiddleware/CardExtractionMiddleware) so a deduped call
    skips those too, not just the real tool execution.

    Two distinct mechanisms, per the migration plan's empirical findings:

    Sequential-round dedup (safe via graph state): LangGraph's Pregel
    supersteps are strictly ordered and state is fully committed between
    them, so a signature written to state.seen_tool_calls after round 1 is
    reliably visible to round 2. Verified necessary: replaying the FIRST
    call's cached ToolMessage object (with its own tool_call_id) for a
    later duplicate call does NOT raise, but silently leaves the later
    call's tool_call unanswered in the message list -- a shape a real
    provider then rejects on the next turn. Fix: always rebuild the
    ToolMessage with the CURRENT request.tool_call["id"], only the
    content/artifact payload is reused from cache.

    Same-message concurrent dedup (best-effort): multiple tool_calls in one
    AIMessage execute concurrently (verified: two identical calls in one
    AIMessage both start within microseconds of each other), so state reads
    before either branch's write commits can't prevent both from running --
    a real TOCTOU race no state-based check alone can close. This is guarded
    with an in-process, per-invocation asyncio.Lock/Future map (NOT stored
    on graph state, and NOT the middleware instance's whole lifetime --
    infoAgent is a single module-level object reused by every concurrent
    user's turn, so unscoped process-local state here would leak across
    unrelated sessions). Scoped by session_id, threaded through via
    context_schema/AgentContext -- the same value SessionIdOverrideMiddleware
    already relies on being present.
    """

    state_schema = DedupState

    def __init__(self):
        super().__init__()
        self._inflight: dict[str, asyncio.Future] = {}

    async def awrap_tool_call(self, request, handler):
        signature = _tool_call_signature(request.tool_call)
        seen = (request.state or {}).get("seen_tool_calls") or {}
        cached = seen.get(signature)
        if cached is not None:
            return ToolMessage(
                content=cached["content"],
                artifact=cached.get("artifact"),
                status=cached.get("status", "success"),
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
            )

        session_id = request.runtime.context.get("session_id", "") if request.runtime.context else ""
        lock_key = f"{session_id}:{signature}"
        inflight = self._inflight.get(lock_key)
        first_caller = inflight is None
        if first_caller:
            inflight = asyncio.get_event_loop().create_future()
            self._inflight[lock_key] = inflight

        # extra_update carries anything an inner middleware (e.g.
        # CardExtractionMiddleware, which can return a Command instead of a
        # bare ToolMessage) wanted to write besides the message itself --
        # e.g. turn_cards/disclosure_required. Only the call that actually
        # ran the tool gets to contribute those; a deduped replay must NOT
        # re-add them (old code's dedup never re-extended turn_cards for a
        # duplicate call either -- doing so here would double the cards).
        extra_update: dict = {}
        try:
            if first_caller:
                inner_result = await handler(request)
                if isinstance(inner_result, Command):
                    tm = inner_result.update["messages"][0]
                    extra_update = {k: v for k, v in inner_result.update.items() if k != "messages"}
                else:
                    tm = inner_result
                payload = {
                    "content": tm.content,
                    "artifact": getattr(tm, "artifact", None),
                    "status": getattr(tm, "status", "success"),
                }
                inflight.set_result(payload)
            else:
                payload = await inflight
        finally:
            if first_caller:
                self._inflight.pop(lock_key, None)

        result_tm = ToolMessage(
            content=payload["content"],
            artifact=payload.get("artifact"),
            status=payload.get("status", "success"),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
        )
        return Command(update={**extra_update, "messages": [result_tm], "seen_tool_calls": {signature: payload}})


class SessionIdOverrideMiddleware(AgentMiddleware):
    """
    Forces save_lead's session_id argument to the real, server-known
    session_id, regardless of what the model supplied.

    Why: save_lead's schema takes session_id as a plain model-supplied tool
    argument, and the system prompt just tells the model to "pass the
    session_id you were given" -- there was never an actual guarantee the
    model gets this right (a long conversation, a copy-paste-shaped
    hallucination, or a dropped value would silently misattribute a lead to
    the wrong session). Verified end-to-end: agent.ainvoke(..., context=
    {"session_id": ...}) makes the real value available here via
    request.runtime.context, and overriding the tool call's args before the
    handler runs forces the correct value even when the model's own args
    are wrong.
    """

    async def awrap_tool_call(self, request, handler):
        if request.tool_call["name"] == "save_lead":
            real_session_id = request.runtime.context["session_id"]
            request = request.override(
                tool_call={
                    **request.tool_call,
                    "args": {**request.tool_call["args"], "session_id": real_session_id},
                }
            )
        return await handler(request)


class CardExtractionMiddleware(AgentMiddleware):
    """
    Pulls .artifact off content_and_artifact tools (google_search,
    facility_search) into state.turn_cards, and flags disclosure_required
    when facility_search's content starts with DISCLOSURE_PREFIX (its
    web-fallback case). Mirrors the old run_turn's inline card-building
    (app/main.py, pre-migration) -- verified .artifact survives
    create_agent's own ToolNode, so this stays a pure post-processing step
    around the handler rather than needing to reimplement tool dispatch.
    """

    state_schema = CardState

    async def awrap_tool_call(self, request, handler):
        inner_result = await handler(request)
        # The inner handler (ToolErrorSafetyNetMiddleware, on a failure) can
        # return a Command instead of a bare ToolMessage -- e.g. to also
        # update state.tool_failures. Unwrap it the same way
        # ToolCallDedupMiddleware does, and carry any such extra keys
        # through untouched (this middleware has nothing to add to a
        # tool_failures entry, but must not silently drop it either).
        if isinstance(inner_result, Command):
            tm = inner_result.update["messages"][0]
            extra_update = {k: v for k, v in inner_result.update.items() if k != "messages"}
        else:
            tm = inner_result
            extra_update = {}

        artifact = getattr(tm, "artifact", None)
        # Cards and the disclosure flag are independent -- a facility_search
        # web-fallback reply can carry the disclosure sentence with an empty
        # artifact list (or vice versa), so neither check gates the other.
        disclosure_required = isinstance(tm.content, str) and tm.content.startswith(DISCLOSURE_PREFIX)

        if not artifact and not disclosure_required:
            return Command(update={**extra_update, "messages": [tm]}) if extra_update else tm

        cards = []
        if artifact:
            if request.tool_call["name"] == "google_search":
                cards = [
                    {
                        "source": "not_certified",
                        "title": r.get("title"),
                        "snippet": r.get("snippet"),
                        "url": r.get("link"),
                    }
                    for r in artifact
                ]
            else:
                # facility_search already tags each card with its own source
                # (cms_certified/not_certified) -- a single call can return a
                # mix, so no blanket-tagging by "which tool got called."
                cards = list(artifact)

        return Command(update={**extra_update, "messages": [tm], "turn_cards": cards, "disclosure_required": disclosure_required})


class DisclosureEnforcementMiddleware(AgentMiddleware):
    """
    Server-enforces the disclosure sentence on the final reply whenever
    CardExtractionMiddleware flagged disclosure_required -- the LLM's own
    paraphrase sometimes drops it even though the tool's content had it
    verbatim. Mirrors the old run_turn's post-loop disclosure check.
    """

    state_schema = CardState

    def after_model(self, state, runtime):
        messages = state["messages"]
        if not messages:
            return None
        last = messages[-1]
        if getattr(last, "tool_calls", None):
            return None  # not the terminal reply yet
        if not state.get("disclosure_required"):
            return None
        content = last.content or ""
        if DISCLOSURE_PREFIX.lower() in content.lower():
            return None
        return {"messages": [last.model_copy(update={"content": f"{DISCLOSURE_PREFIX} {content}"})]}


class ProviderRetryMiddleware(ModelRetryMiddleware):
    """
    Distinctly-named ModelRetryMiddleware subclass -- create_agent rejects
    multiple middleware instances that share a `.name` (defaults to the
    class name), and infoAgent already has a second, differently-configured
    ModelRetryMiddleware for Groq's "tool_use_failed" generation glitch.
    This one is for actual provider/network failures (timeout, rate limit,
    connection error, 5xx) -- see app/main.py's infoAgent_middleware for
    its configuration and placement (outermost of the wrap_model_call
    chain).
    """


class LeakedToolCallRetryMiddleware(AgentMiddleware):
    """
    Retries once when Groq leaves tool_calls empty and puts the pseudo-call
    text straight into .content instead -- which would otherwise be shown
    to the user verbatim as if it were a real final answer. Wraps the model
    node (not the tool node); place innermost of the wrap_model_call chain,
    closer to the raw model call than ModelRetryMiddleware, since this is
    inspecting the raw response shape rather than catching exceptions.
    """

    async def awrap_model_call(self, request, handler):
        response = await handler(request)
        ai_msg = response.result[-1] if response.result else None
        if ai_msg is not None and isinstance(ai_msg, AIMessage) \
                and not ai_msg.tool_calls \
                and _looks_like_leaked_tool_call(ai_msg.content or ""):
            response = await handler(request)
        return response


class ToolFailureState(AgentState):
    """Tracks tool failures (timeouts, unexpected exceptions) for this graph run."""

    tool_failures: NotRequired[Annotated[list, operator.add]]


class ToolErrorSafetyNetMiddleware(AgentMiddleware):
    """
    Belt-and-suspenders: create_agent's default ToolNode does NOT catch
    exceptions raised inside a tool (verified: a raw RuntimeError from a
    tool propagates all the way out of agent.ainvoke uncaught). Today's
    actual tools (_facility_search, web_search) already catch their own
    errors and return a friendly string, and save_lead now raises
    UserSafeToolError on a genuine persistence failure (see
    tools/agent_tools.py) -- but this middleware is the second, universal
    line of defense: any tool call that hangs or raises something
    unanticipated still needs to fail safely. Place innermost of the
    wrap_tool_call chain (last line of defense, after dedup/session-
    override/card-extraction have already run) -- this also means
    ToolCallDedupMiddleware's concurrent-duplicate guard, which awaits the
    first caller's in-flight Future with no timeout of its own, is
    naturally bounded by the timeout here rather than hanging forever too.

    Two things matter for what a failure produces:

    1. ToolMessage.status="error" never reaches Groq -- confirmed by
       reading langchain_groq's message serialization, which emits only
       role/content/tool_call_id for a ToolMessage. So status is a
       local-only signal (useful for our own state/logging via
       tool_failures below), NOT something that by itself stops the model
       from misreading a failure. The only real lever over model behavior
       is the content text, which is why it's phrased as an explicit
       instruction ("do not present this as a success") rather than a
       plain apology.
    2. Exception messages are sanitized before reaching that content: only
       our own deliberately-raised UserSafeToolError's message is user-
       facing (an explicit opt-in per raise site); every other exception
       type -- including a plain RuntimeError from somewhere unexpected, or
       a raw DB/network driver error that might embed a connection string
       or hostname -- gets a generic reason instead. The real exception is
       still logged in full server-side either way.
    """

    state_schema = ToolFailureState

    def __init__(self, timeout_seconds: float = 20.0):
        super().__init__()
        self.timeout_seconds = timeout_seconds

    async def awrap_tool_call(self, request, handler):
        tool_name = request.tool_call["name"]
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await handler(request)
        except TimeoutError:
            log_error(f"Tool call timed out | {tool_name} | limit={self.timeout_seconds}s")
            reason = "timed out"
        except UserSafeToolError as e:
            reason = str(e)
        except Exception as e:
            log_error(f"Tool call failed | {tool_name} | {type(e).__name__}: {e}")
            reason = "an unexpected technical error"

        content = (
            f"This tool call failed ({reason}). Do not present this as a success or "
            f"imply any results were found -- tell the user plainly there was a "
            f"technical issue and offer to try again."
        )
        failure_message = ToolMessage(
            content=content,
            status="error",
            tool_call_id=request.tool_call["id"],
            name=tool_name,
        )
        return Command(
            update={
                "messages": [failure_message],
                "tool_failures": [{"tool": tool_name, "reason": reason, "tool_call_id": request.tool_call["id"]}],
            }
        )


# Fallback text for the final reply -- one canonical generic string (also
# used today for the leaked-tool-call case) plus one specific to total tool
# failure, so that case stays honestly distinct from a generic hiccup.
GENERIC_FALLBACK_TEXT = "Sorry, I had trouble with that -- could you try rephrasing?"
TOTAL_TOOL_FAILURE_TEXT = "I wasn't able to complete that request due to a technical issue. Please try again in a moment."

# Defense-in-depth on top of the tool-level UserSafeToolError sanitization --
# patterns that should never legitimately appear in this assistant's replies.
# Deliberately narrow (unmistakable internal artifacts only) to avoid false
# positives on ordinary text like "There was an error processing your request."
_INTERNAL_LEAK_RE = re.compile(
    r"Traceback \(most recent call last\)"
    r'|File "[^"]+", line \d+'
    r"|\b\w+(?:Error|Exception):\s"
    r"|(?:postgresql|postgres|mysql|mongodb|redis)://"
    r"|\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b",
    re.IGNORECASE,
)


class FinalOutputValidationMiddleware(AgentMiddleware):
    """
    Last-mile validation of the terminal reply, right before it reaches the
    user -- an after_model hook, same pattern as DisclosureEnforcementMiddleware
    (only acts once the model has stopped calling tools). Listed BEFORE
    DisclosureEnforcementMiddleware in infoAgent_middleware: verified
    empirically that after_model hooks execute in REVERSE list order (the
    opposite of wrap_model_call/wrap_tool_call, where first-in-list is
    outermost) -- so listing this one first means it executes SECOND, seeing
    disclosure enforcement's already-edited content, with its own edit (if
    any) having final say.

    Checks run in order, first match wins:

    1. Claimed success despite total tool failure -- deterministic, not a
       heuristic. Ground truth is ToolMessage.status (set by
       ToolErrorSafetyNetMiddleware/ToolCallDedupMiddleware), not turn_cards:
       turn_cards only reflects google_search/facility_search results, so a
       successful save_lead call (no card) alongside a failed facility_search
       would wrongly trip a turn_cards-based check. Only overrides when
       EVERY tool call this turn failed -- a mixed outcome (one tool
       succeeded, another failed) is left to that failed call's own
       directive ToolMessage content instead, since the model may have a
       legitimate partial answer to give.

       disclosure_required can never coincide with this override:
       CardExtractionMiddleware only sets it on a tool call that succeeded,
       so disclosure_required=True implies at least one successful call this
       turn, which alone rules out "every tool call failed."
    2. Non-string content (defensive -- Groq returns plain strings today).
    3. Empty/whitespace content with nothing else to show (blank content is
       correct and intentional when turn_cards are present -- not flagged).
    4. Leaked raw tool-call syntax (consolidates the check that used to live
       inline in app.main.run_turn into this one place).
    5. Internal-error-looking leakage (see _INTERNAL_LEAK_RE above).
    """

    def after_model(self, state, runtime):
        messages = state["messages"]
        if not messages:
            return None
        last = messages[-1]
        if getattr(last, "tool_calls", None):
            return None  # not the terminal reply yet

        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        had_successful_tool_call = any(getattr(tm, "status", "success") != "error" for tm in tool_messages)
        tool_failures = state.get("tool_failures") or []

        if tool_failures and not had_successful_tool_call:
            replacement = TOTAL_TOOL_FAILURE_TEXT
        elif not isinstance(last.content, str):
            replacement = GENERIC_FALLBACK_TEXT
        elif not last.content.strip() and not state.get("turn_cards"):
            replacement = GENERIC_FALLBACK_TEXT
        elif _looks_like_leaked_tool_call(last.content):
            replacement = GENERIC_FALLBACK_TEXT
        elif _INTERNAL_LEAK_RE.search(last.content):
            replacement = GENERIC_FALLBACK_TEXT
        else:
            return None

        return {"messages": [last.model_copy(update={"content": replacement})]}
