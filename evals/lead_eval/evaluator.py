import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


load_dotenv(override=True)


class Grade(BaseModel):
    """Evaluate the agent's reply for lead-generation behavior"""
    reasoning: str = Field(
        ...,
        description=(
            "Explain your reasoning for whether the reply is correct or not, AND for the quality_score "
            "you assign -- justify both the pass/fail verdict and the numeric score in the same explanation."
        ),
    )
    is_correct: bool = Field(..., description="True if the reply is correct according to the criteria, otherwise False.")
    quality_score: int = Field(
        ...,
        ge=0,
        le=10,
        description=(
            "Rate the overall quality of the agent's lead-generation behavior on a 0-10 integer scale, "
            "independent of the strict pass/fail verdict -- a reply can be 'correct' (is_correct=True) but "
            "still mediocre (e.g. 6-7) if it's clumsy, and a reply can be 'incorrect' but only mildly so "
            "(e.g. 4-5). Anchor your score as follows: "
            "0-2 = severe violation (e.g. demands contact info or multiple details out of nowhere, diagnoses "
            "a medical condition, or completely ignores/steamrolls an objection); "
            "3-4 = notable violation (e.g. skips a phase, asks more than one question, or pushes past an "
            "objection without empathy or reassurance); "
            "5-6 = minor issues (e.g. forgets to explain why a question is asked, rushes a phase slightly, "
            "reads a bit like a form) but nothing that breaks trust or the flow; "
            "7-8 = solid, on-phase, warm reply with only cosmetic room for improvement; "
            "9-10 = exemplary -- perfectly paced, empathetic, on-phase, asks at most one well-justified "
            "question, and (if applicable) handles an objection with genuine reassurance. "
            "Use the full range; do not default to 7-8 out of habit."
        ),
    )

    @field_validator("quality_score", mode="before")
    @classmethod
    def _clamp_quality_score(cls, v):
        # Groq's json_schema structured-output mode isn't guaranteed to
        # enforce numeric minimum/maximum the way it enforces type/required
        # -- clamp defensively so a rare overshoot doesn't invalidate an
        # otherwise-good grade.
        try:
            v = round(float(v))
        except (TypeError, ValueError):
            return v
        return max(0, min(10, v))

lead_gen_grader_instructions = """You are an evaluation assistant for Infomary, the AI Senior Care Advisor agent for InfoSenior.care.

Output only **VALID** JSON.

Infomary follows a 5-phase conversion flow, always in order, never skipping ahead:
  Phase 1 -- Emotion first: acknowledge the user's feeling and normalize it before anything else.
  Phase 2 -- Expert insight: share one relevant insight tied to their situation.
  Phase 3 -- Soft recommendation + permission: suggest a care type and ask permission to explore options -- never collect details before this permission is given.
  Phase 4 -- Natural detail collection: only after a yes, collect details (location, age, living situation, medical condition, budget) ONE AT A TIME, always explaining why before asking.
  Phase 5 -- Contact capture: only after the user separately agrees to "personal support" from a care advisor, ask for a phone number or email naturally (e.g. "What's the best number or email to contact you?"), never coldly (e.g. never "Can I have your phone number?" or "I need your email to proceed").

It also follows these rules:
- Never ask more than one question in a single reply.
- Never ask for contact info before value has been provided AND permission for personal support has been separately given -- a "yes" to exploring options is NOT the same as permission to be contacted.
- Every question must explain WHY it's being asked, not feel like a cold form.
- Objections ("just looking", "can't afford it", "need to think about it", "we're managing at home") should be met with empathy and reassurance (e.g. the service is free, no pressure, Medicaid/Medicare accepted), not ignored or pushed past.
- Never diagnose a medical condition -- suggest discussing it with a doctor instead.
- Never pressure the user, and never make the user feel like they're filling out a form.
- A plain greeting ("hi", "hello", "how are you") does not require any of the phases -- a warm, brief greeting back is correct; jumping straight into Phase 1/asking for details on a bare greeting is not.

You will be given the conversation so far, the user's latest message, and the agent's reply. Use the conversation so far to judge where the flow actually is (e.g. has permission already been given? has a detail already been collected?) -- evaluate whether the reply is CORRECT:
1. It does not skip ahead of where the conversation would reasonably be in the flow (e.g. it must not ask for contact info out of nowhere on a first message, or on a message that hasn't given permission for personal support).
2. It asks at most ONE question, and briefly explains why if it does.
3. If the user raised an objection, the reply addresses it with empathy and reassurance rather than ignoring it or pushing forward regardless.
4. It doesn't diagnose a medical condition or pressure the user.

In addition to the pass/fail verdict, rate the reply's overall lead-generation quality on a 0-10 integer scale (quality_score), using this rubric:
  0-2  -- Severe violation: e.g. demands contact info or multiple details out of nowhere, diagnoses a medical condition, or completely ignores/steamrolls an objection.
  3-4  -- Notable violation: e.g. skips a phase, asks more than one question, or pushes past an objection without empathy or reassurance.
  5-6  -- Minor issues: e.g. forgets to explain why a question is being asked, rushes a phase slightly, or reads a bit like a form -- but nothing that breaks trust or the flow.
  7-8  -- Solid: on-phase, warm, asks at most one well-justified question, no major issues -- only cosmetic room for improvement.
  9-10 -- Exemplary: perfectly paced, empathetic, on-phase, and (if applicable) handles an objection with genuine reassurance.
is_correct and quality_score are independent judgments: a reply can be technically "correct" (is_correct=True) yet still only score 6-7 if it's clumsy, and a reply can be "incorrect" yet still score 4-5 if the violation is minor. Use the full range -- do not default to 7 or 8 out of habit, and reserve 9-10 for genuinely exemplary replies.

Explain your reasoning in a step-by-step manner, covering both the is_correct verdict and the quality_score you assign, to ensure your reasoning and conclusions are correct and consistent with each other.
"""
GROQ_API_KEY = os.getenv('GROQ_API_KEY')

if not GROQ_API_KEY:
    raise ValueError("GROQ API Key is missing!")

llm = ChatGroq(
    api_key=GROQ_API_KEY,
    model='openai/gpt-oss-20b',
    temperature=0
)

evaluator_llm = llm.with_structured_output(Grade, method="json_schema")


async def lead_gen_correctness(inputs: dict, outputs: dict) -> list[dict]:
    "Evaluates the agent's final reply for lead-generation phase/tone correctness"

    # the last non-empty AIMessage is the agent's actual reply to the user --
    # main.py's loop only appends AIMessages, breaking once one has no
    # tool_calls, so this is reliably the final text response.
    final_text = ""
    for message in reversed(outputs["messages"]):
        if isinstance(message, AIMessage) and message.content:
            final_text = message.content
            break

    if not final_text:
        return [
            {"key": "leadGenCorrectness", "score": True, "comment": "no final text to grade"},
            {"key": "leadGenQualityScore", "score": None, "comment": "no final text to grade"},
            {"key": "leadGenGradingErrors", "score": 0},
        ]

    history = inputs.get("history", [])
    conversation_so_far = "\n".join(f"{turn['role']}: {turn['content']}" for turn in history) or "(no prior turns -- this is the first message)"

    user_message = f"""CONVERSATION SO FAR:
    {conversation_so_far}

    USER'S LATEST MESSAGE: {inputs['question']}
    AGENT REPLY: {final_text}
    """
    try:
        grade: Grade = await evaluator_llm.ainvoke(
            [SystemMessage(lead_gen_grader_instructions), HumanMessage(user_message)]
        )
        return [
            {"key": "leadGenCorrectness", "score": grade.is_correct, "comment": grade.reasoning},
            {"key": "leadGenQualityScore", "score": grade.quality_score, "comment": grade.reasoning},
            {"key": "leadGenGradingErrors", "score": 0},
        ]
    except Exception as e:
        return [
            {"key": "leadGenCorrectness", "score": True, "comment": f"grading error, not scored: {e}"},
            {"key": "leadGenQualityScore", "score": None, "comment": f"grading error, not scored: {e}"},
            {"key": "leadGenGradingErrors", "score": 1},
        ]
