"""
Tier 2 -- slower, full multi-step trajectory eval. Runs the same tool-calling
loop shape as main.py's websocket handler (call tool(s) -> feed result back ->
maybe call more -> final non-tool response), using the real tool objects from
tools/agent_tools.py in-process. This does NOT require the FastAPI server or
websocket to be running -- only the same downstream services main.py itself
depends on (Groq, Supabase, Qdrant, Fireworks, Serper).

Uses langsmith's aevaluate() (async target), same dataset-sync approach as
run_tool_selection_evals.py -- see langsmith_utils.sync_dataset's docstring
for why a real server-registered dataset is needed, not just a local Example.

Run: uv run python -m evals.run_trajectory_evals
"""
import asyncio
import os

from dotenv import load_dotenv
from langchain_core.messages import ToolMessage
from langsmith import Client, aevaluate
from langsmith.schemas import Example, Run

from database import close_db_pool, init_db_pool
from evals.dataset import TRAJECTORY_CASES
from evals.langsmith_utils import sync_dataset
from evals.run_tool_selection_evals import _build_messages, llm
from tools.agent_tools import facility_search, google_search
from tools.facility_search.search import DISCLOSURE_PREFIX

load_dotenv()


async def _run_agent_turn(messages: list) -> tuple[str, list[str], list[dict], list[dict]]:
    """
    Mirrors main.py's per-turn tool-calling loop (for i in range(5)). Invokes
    facility_search/google_search with the full tool-call dict (not just
    args) to get back a ToolMessage with .artifact, same as main.py --
    needed to check the real cards a Phase 8 facility_search call returns
    (expect_no_cards / expect_card_source), not just which tool ran. Also
    collects each call's {"name", "args"} -- needed to verify multi-turn
    continuity (e.g. does a second-turn call actually carry forward
    facility_type from turn 1 alongside the new turn's location?), not just
    that *a* facility_search call happened.
    """
    tool_names_called: list[str] = []
    tool_calls_seen: list[dict] = []
    all_cards: list[dict] = []
    disclosure_required = False
    response = None
    for _ in range(5):
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            # Mirrors main.py's run_turn retry -- Groq occasionally emits a
            # malformed function-call payload; one retry clears it in
            # production, so an eval run shouldn't hard-fail on the same
            # transient error and lose the whole example's score.
            err_text = str(e).lower()
            if "tool call validation failed" in err_text or "tool_use_failed" in err_text:
                response = await llm.ainvoke(messages)
            else:
                raise
        if getattr(response, "tool_calls", None):
            messages.append(response)
            for tc in response.tool_calls:
                tool_names_called.append(tc["name"])
                tool_calls_seen.append({"name": tc["name"], "args": tc["args"]})
                if tc["name"] == "google_search":
                    tool_message = await google_search.ainvoke(tc)
                    result = tool_message.content
                elif tc["name"] == "facility_search":
                    tool_message = await facility_search.ainvoke(tc)
                    result = tool_message.content
                    if tool_message.artifact:
                        all_cards.extend(tool_message.artifact)
                    if result.startswith(DISCLOSURE_PREFIX):
                        disclosure_required = True
                else:
                    result = "Unknown tool"
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        else:
            break
    final_text = response.content if response else ""
    if disclosure_required and DISCLOSURE_PREFIX.lower() not in final_text.lower():
        final_text = f"{DISCLOSURE_PREFIX} {final_text}"
    return final_text, tool_names_called, all_cards, tool_calls_seen


async def target(inputs: dict) -> dict:
    messages = _build_messages(inputs.get("history", []), inputs["message"])
    final_text, tool_names, cards, tool_calls = await _run_agent_turn(messages)
    return {"final_text": final_text, "tool_names": tool_names, "cards": cards, "tool_calls": tool_calls}


def trajectory_evaluator(run: Run, example: Example) -> dict:
    expected_tool = example.outputs.get("expected_tool")
    expect_no_cards = example.outputs.get("expect_no_cards", False)
    expect_card_source = example.outputs.get("expect_card_source")
    expected_args = example.outputs.get("expected_args_contains") or {}
    outputs = run.outputs or {}
    tool_names = outputs.get("tool_names", [])
    cards = outputs.get("cards", [])
    tool_calls = outputs.get("tool_calls", [])
    final_text = (outputs.get("final_text") or "").lower()

    if expected_tool not in tool_names:
        return {
            "key": "trajectory_correct",
            "score": 0,
            "comment": f"expected {expected_tool!r} in trajectory, got {tool_names}",
        }

    if expect_no_cards and cards:
        return {
            "key": "trajectory_correct",
            "score": 0,
            "comment": f"expected no cards, got {len(cards)}: {cards[0]}",
        }

    if expect_card_source:
        if not cards:
            return {
                "key": "trajectory_correct",
                "score": 0,
                "comment": f"expected cards with source={expect_card_source!r}, got none",
            }
        actual_source = cards[0].get("source")
        if actual_source != expect_card_source:
            return {
                "key": "trajectory_correct",
                "score": 0,
                "comment": f"expected card source {expect_card_source!r}, got {actual_source!r}",
            }

    if expected_args:
        # Multi-turn continuity check (e.g. does a second-turn call actually
        # carry facility_type forward from turn 1 alongside the new turn's
        # location?) -- permissive like Tier 1: pass if ANY call to
        # expected_tool in the trajectory satisfies the full subset match,
        # since a duplicate re-call (this harness has no main.py-style
        # dedup guard) shouldn't fail an otherwise-correct trajectory.
        matching_calls = [tc for tc in tool_calls if tc["name"] == expected_tool]
        if not any(
            all(str(expected_value).lower() in str(tc["args"].get(key, "")).lower()
                for key, expected_value in expected_args.items())
            for tc in matching_calls
        ):
            return {
                "key": "trajectory_correct",
                "score": 0,
                "comment": f"expected args {expected_args} in a {expected_tool!r} call, got {[tc['args'] for tc in matching_calls]}",
            }

    case_id = example.metadata.get("case_id", "")
    if case_id == "tj_retired_ltch_type":
        if "cms-certified" not in final_text and "certified" not in final_text:
            return {
                "key": "trajectory_correct",
                "score": 0,
                "comment": f"missing required disclosure sentence in final reply: {final_text[:200]!r}",
            }

    return {"key": "trajectory_correct", "score": 1, "comment": f"tool_names={tool_names} cards={len(cards)}"}


async def main():
    if not os.getenv("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY not set in backend/.env -- see docstring for setup.")
        return

    client = Client()
    dataset_name = sync_dataset(
        client, "infomary-trajectory", TRAJECTORY_CASES,
        description="Tier 2 multi-step trajectory cases (backend/evals/dataset.py TRAJECTORY_CASES).",
    )

    # facility_search has no self-healing DB pool init -- in the real app
    # this is a non-issue since main.py's lifespan initializes the pool once
    # at server startup before any request arrives. This script has no such
    # startup hook, so it must be done explicitly here, or a cold-start
    # facility_search call fails with "DB pool not initialized" and the
    # eval would score a false pass/fail for the wrong reason.
    await init_db_pool()
    try:
        results = await aevaluate(
            target,
            data=dataset_name,
            evaluators=[trajectory_evaluator],
            experiment_prefix="infomary-trajectory",
            max_concurrency=1,  # sequential -- these hit real Supabase/Qdrant/Fireworks/Serper/Groq
        )

        passed = failed = 0
        async for row in results:
            case_id = row["example"].metadata.get("case_id", "?")
            eval_result = row["evaluation_results"]["results"][0]
            if eval_result.score:
                passed += 1
                print(f"  OK   {case_id}")
            else:
                failed += 1
                print(f"  FAIL {case_id} | {eval_result.comment}")

        print(f"\n{passed} passed, {failed} failed")
    finally:
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
