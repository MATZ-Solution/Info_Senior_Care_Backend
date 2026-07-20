"""
Tier 1 -- fast tool-selection eval: one Groq call per case, checking only
WHICH tool (if any) the agent calls first for a given conversation state.
Does not execute the tools themselves (see run_trajectory_evals.py for that).

Uses the LangSmith SDK's evaluate(). Cases are synced into a real LangSmith
dataset each run (see langsmith_utils.sync_dataset) -- evaluate()'s default
upload_results=True needs a server-registered dataset to attach the
experiment to; a locally-constructed Example with a nil dataset_id 404s
("Reference dataset not found"), confirmed by actually running it. The
evaluator itself is a plain deterministic Python function (tool name +
args-subset match), not an LLM-as-judge -- no extra model calls beyond the
one being evaluated.

Requires LANGSMITH_API_KEY in backend/.env (LangSmith account already set up).

Run: uv run python -m evals.run_tool_selection_evals
"""
import os

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langsmith import Client, evaluate
from langsmith.schemas import Example, Run

from evals.langsmith_utils import sync_dataset

from evals.dataset import EvalCase, TOOL_SELECTION_CASES
from system_prompt.instructions import system_instructions
from tools.agent_tools import facility_search, google_search, save_lead

load_dotenv()

# Same construction as main.py's `llm` -- rebuilt locally rather than imported
# from main.py to avoid pulling in FastAPI app/route registration as a side effect.
llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="llama-3.3-70b-versatile",
    temperature=0.1,
).bind_tools([google_search, save_lead, facility_search])

_EVAL_SESSION_SUFFIX = (
    "\n\nYour session_id for this conversation is: eval-session\n"
    "You MUST pass this exact session_id in every single save_lead tool call."
)


def _build_messages(history: list[dict], message: str) -> list:
    messages = [SystemMessage(content=system_instructions + _EVAL_SESSION_SUFFIX)]
    for msg in history:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
    messages.append(HumanMessage(content=message))
    return messages


def target(inputs: dict) -> dict:
    messages = _build_messages(inputs.get("history", []), inputs["message"])
    response = llm.invoke(messages)
    tool_calls = getattr(response, "tool_calls", None) or []
    return {"tool_calls": [{"name": tc["name"], "args": tc["args"]} for tc in tool_calls]}


def tool_call_evaluator(run: Run, example: Example) -> dict:
    expected_tool = example.outputs.get("expected_tool")
    expected_args = example.outputs.get("expected_args_contains") or {}
    actual_tool_calls = (run.outputs or {}).get("tool_calls", [])
    actual_names = [tc["name"] for tc in actual_tool_calls]

    if expected_tool is None:
        # Soft cases (refusals/off-topic/emergency-adjacent): only the
        # search tools matter here -- a save_lead call alongside is fine.
        called_search = any(name in ("facility_search", "google_search") for name in actual_names)
        return {
            "key": "tool_correct",
            "score": 0 if called_search else 1,
            "comment": f"tool_calls={actual_names}",
        }

    matching = [tc for tc in actual_tool_calls if tc["name"] == expected_tool]
    if not matching:
        return {
            "key": "tool_correct",
            "score": 0,
            "comment": f"expected {expected_tool!r}, got {actual_names}",
        }

    args = matching[0]["args"]
    for key, expected_value in expected_args.items():
        actual_value = str(args.get(key, "")).lower()
        if str(expected_value).lower() not in actual_value:
            return {
                "key": "tool_correct",
                "score": 0,
                "comment": f"arg {key!r} expected to contain {expected_value!r}, got {args.get(key)!r}",
            }
    return {"key": "tool_correct", "score": 1, "comment": "OK"}


def main():
    if not os.getenv("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY not set in backend/.env -- see docstring for setup.")
        return

    client = Client()
    dataset_name = sync_dataset(
        client, "infomary-tool-selection", TOOL_SELECTION_CASES,
        description="Tier 1 single-turn tool-choice cases (backend/evals/dataset.py TOOL_SELECTION_CASES).",
    )
    results = evaluate(
        target,
        data=dataset_name,
        evaluators=[tool_call_evaluator],
        experiment_prefix="infomary-tool-selection",
    )

    passed = failed = 0
    for row in results:
        case_id = row["example"].metadata.get("case_id", "?")
        eval_result = row["evaluation_results"]["results"][0]
        if eval_result.score:
            passed += 1
            print(f"  OK   {case_id}")
        else:
            failed += 1
            print(f"  FAIL {case_id} | {eval_result.comment}")

    print(f"\n{passed} passed, {failed} failed")
    print(f"LangSmith experiment: {results.url}")


if __name__ == "__main__":
    main()
