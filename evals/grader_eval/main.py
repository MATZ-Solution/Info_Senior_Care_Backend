import asyncio
import os

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_groq import ChatGroq

from database import close_db_pool, init_db_pool
from evals.grader_eval.dataset import dataset_name
from evals.grader_eval.evaluator import agent_trajectory_correctness
from evals.langsmith_client import get_langsmith_client
from system_prompt.instructions import system_instructions
from tools.agent_tools import facility_search, google_search, save_lead

load_dotenv()

# Same construction as app/main.py's `llm` -- rebuilt locally rather than
# imported from main.py to avoid pulling in FastAPI app/route registration
# as a side effect.
llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="openai/gpt-oss-120b",
    temperature=0.1,
).bind_tools([google_search, save_lead, facility_search])


async def run_search_node(inputs: dict) -> dict:
    question = inputs.get('question', '')
    if not question or not question.strip():
        return {"messages": []}

    messages = [
        SystemMessage(content=system_instructions + "\n\nYour session_id for this conversation is: eval-session"),
        HumanMessage(content=question),
    ]

    # Mirrors main.py's per-turn tool-calling loop (call tool(s) -> feed
    # result back -> maybe call more -> final non-tool response), kept to 6
    # rounds like the real app. The evaluator only needs the AIMessages'
    # tool_calls, so real tool results are fed back but not otherwise used
    # here.
    for _ in range(6):
        try:
            response = await llm.ainvoke(messages)
        except Exception as e:
            err_text = str(e).lower()
            if "tool call validation failed" in err_text or "tool_use_failed" in err_text:
                response = await llm.ainvoke(messages)
            else:
                raise

        messages.append(response)
        if not getattr(response, "tool_calls", None):
            break

        for tc in response.tool_calls:
            if tc["name"] == "google_search":
                tool_message = await google_search.ainvoke(tc)
                result = tool_message.content
            elif tc["name"] == "facility_search":
                tool_message = await facility_search.ainvoke(tc)
                result = tool_message.content
            elif tc["name"] == "save_lead":
                # Never actually invoke the real save_lead during eval runs --
                # it writes to production Supabase and sends real Resend
                # emails (including to a hardcoded notification address).
                # agent_trajectory_correctness only grades the call's
                # arguments, which are already captured on the AIMessage
                # above, so a stub reply is enough to keep the trajectory
                # going without any real side effects.
                result = "Lead saved. ID: EVAL-STUB (not persisted -- eval run)"
            else:
                result = "Unknown tool"
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    return {"messages": messages}

async def run_evaluation():
    client = get_langsmith_client()

    # facility_search needs the DB pool initialized -- in the real app this
    # is a non-issue since main.py's lifespan initializes it once at server
    # startup; this script has no such hook, so it's done here instead.
    await init_db_pool()
    try:
        experiment_results = await client.aevaluate(
            run_search_node,
            data=dataset_name,
            evaluators=[agent_trajectory_correctness],
            experiment_prefix="experiment-infomary-tool-call-grading 1.0"
        )
    finally:
        await close_db_pool()
    return experiment_results


results = asyncio.run(run_evaluation())
