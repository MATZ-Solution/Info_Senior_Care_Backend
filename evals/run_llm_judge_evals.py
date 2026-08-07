"""
LLM-judge eval runner -- complements run_tool_selection_evals.py (Tier 1,
tool choice) and run_trajectory_evals.py (Tier 2, real tool-calling loop)
with four semantic dimensions that need an LLM judge rather than exact/
substring matching: tool-argument groundedness (no hallucinated args),
tool-call sequence/redundancy, off-topic response handling, and lead-gen
5-phase adherence. See evals/llm_judge_dataset.py for the cases and
evals/llm_judge_grader.py for the grading prompts.

Reuses run_trajectory_evals.py's `target` (same real 5-round tool-calling
loop, same downstream services: Groq, Supabase, Qdrant, Fireworks, Serper)
rather than duplicating it -- this runner only adds a different evaluator
on top of the same trajectory shape.

Requires LANGSMITH_API_KEY in backend/.env (LangSmith account already set up).

Run: uv run python -m evals.run_llm_judge_evals
"""
import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from langsmith import Client, aevaluate
from langsmith.schemas import Example, Run

from database import close_db_pool, init_db_pool
from evals.llm_judge_dataset import ALL_JUDGE_CASES, JudgeEvalCase
from evals.llm_judge_grader import (
    grade_arg_grounding,
    grade_lead_gen_phase,
    grade_off_topic_handling,
)
from evals.run_trajectory_evals import target as run_agent_turn_target

load_dotenv()

# Windows' legacy console/file encoding (cp1252) can't represent some
# punctuation the judge LLM outputs in its reasoning (e.g. U+2011 non-
# breaking hyphen), which crashed print() mid-run before the final tally
# printed. Force UTF-8 on stdout so a judge's word choice never kills the run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _sync_judge_dataset(client: Client, dataset_name: str, cases: list[JudgeEvalCase], description: str) -> str:
    """
    Same delete-and-recreate idiom as langsmith_utils.sync_dataset, kept
    local since JudgeEvalCase's fields (expect_no_new_call, category) don't
    match EvalCase's (expect_no_cards, expect_card_source) -- not worth
    generalizing the shared helper just for this.
    """
    if client.has_dataset(dataset_name=dataset_name):
        client.delete_dataset(dataset_name=dataset_name)
    dataset = client.create_dataset(dataset_name=dataset_name, description=description)
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "inputs": {"history": c.history, "message": c.message},
                "outputs": {"expected_tool": c.expected_tool, "expect_no_new_call": c.expect_no_new_call},
                "metadata": {"case_id": c.id, "category": c.category, "note": c.note},
            }
            for c in cases
        ],
    )
    return dataset_name


async def target(inputs: dict) -> dict:
    return await run_agent_turn_target(inputs)


async def judge_evaluator(run: Run, example: Example) -> list[dict]:
    category = example.metadata.get("category", "")
    outputs = run.outputs or {}
    tool_calls = outputs.get("tool_calls", [])
    tool_names = outputs.get("tool_names", [])
    final_text = outputs.get("final_text", "")
    user_message = example.inputs.get("message", "")

    if category == "arg_grounding":
        results = []
        expected_tool = example.outputs.get("expected_tool")
        if expected_tool:
            matched = any(tc["name"] == expected_tool for tc in tool_calls)
            results.append({"key": "toolNameCorrectness", "score": matched, "comment": f"tool_names={tool_names}"})

        grounded_flags = []
        grading_errors = 0
        reasons = []
        for tc in tool_calls:
            try:
                grade = await grade_arg_grounding(user_message, tc["name"], tc["args"])
            except Exception as e:
                grading_errors += 1
                reasons.append(f"{tc['name']}: grading error ({e})")
                continue
            grounded_flags.append(grade.is_grounded)
            reasons.append(f"{tc['name']}({tc['args']}): {grade.is_grounded} -- {grade.reasoning}")
        results.append({
            "key": "argGroundingCorrectness",
            "score": all(grounded_flags) if grounded_flags else True,
            "comment": " | ".join(reasons) if reasons else "no tool calls to grade",
        })
        results.append({"key": "argGradingErrors", "score": grading_errors})
        return results

    if category == "sequence":
        seen = set()
        has_duplicate = False
        for tc in tool_calls:
            key = (tc["name"], json.dumps(tc["args"], sort_keys=True))
            if key in seen:
                has_duplicate = True
            seen.add(key)
        expect_no_new_call = example.outputs.get("expect_no_new_call", False)
        unexpected_call = expect_no_new_call and bool(tool_names)
        return [{
            "key": "sequenceCorrectness",
            "score": (not has_duplicate) and (not unexpected_call),
            "comment": f"tool_names={tool_names} duplicate_call={has_duplicate} expect_no_new_call={expect_no_new_call}",
        }]

    if category == "off_topic":
        grade = await grade_off_topic_handling(user_message, final_text)
        return [{"key": "offTopicHandlingCorrectness", "score": grade.is_correct, "comment": grade.reasoning}]

    if category == "lead_gen":
        history = example.inputs.get("history", [])
        conversation_so_far = "\n".join(f"{h['role']}: {h['content']}" for h in history) or "(no prior turns)"
        grade = await grade_lead_gen_phase(conversation_so_far, user_message, final_text)
        return [{"key": "leadGenPhaseCorrectness", "score": grade.is_correct, "comment": grade.reasoning}]

    return [{"key": "unknownCategory", "score": 0, "comment": f"unrecognized category {category!r}"}]


def _case_passed(eval_results) -> bool:
    for r in eval_results:
        if r.key == "argGradingErrors":
            if r.score:
                return False
            continue
        if not r.score:
            return False
    return True


async def main():
    if not os.getenv("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY not set in backend/.env")
        return

    client = Client()
    dataset_name = _sync_judge_dataset(
        client, "infomary-llm-judge", ALL_JUDGE_CASES,
        description=(
            "LLM-judge cases (backend/evals/llm_judge_dataset.py ALL_JUDGE_CASES): "
            "tool-arg groundedness, tool-call sequence/redundancy, off-topic handling, "
            "lead-gen 5-phase adherence."
        ),
    )

    # facility_search needs the DB pool initialized -- same workaround as
    # run_trajectory_evals.py since this script has no FastAPI lifespan hook.
    await init_db_pool()
    try:
        results = await aevaluate(
            target,
            data=dataset_name,
            evaluators=[judge_evaluator],
            experiment_prefix="infomary-llm-judge",
            max_concurrency=1,  # sequential -- hits real Groq/Supabase/Qdrant/Fireworks/Serper, plus a judge Groq call per case
        )

        passed = failed = 0
        async for row in results:
            case_id = row["example"].metadata.get("case_id", "?")
            category = row["example"].metadata.get("category", "?")
            eval_results = row["evaluation_results"]["results"]
            detail = ", ".join(f"{r.key}={r.score}" for r in eval_results)
            if _case_passed(eval_results):
                passed += 1
                print(f"  OK   [{category}] {case_id} | {detail}")
            else:
                failed += 1
                print(f"  FAIL [{category}] {case_id} | {detail}")
                # Print the judge's reasoning (or the deterministic comment)
                # for each sub-score so a failure is diagnosable straight
                # from the console, without opening the LangSmith UI.
                for r in eval_results:
                    if r.comment:
                        print(f"        {r.key}: {r.comment}")

        print(f"\n{passed} passed, {failed} failed")
    finally:
        await close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
