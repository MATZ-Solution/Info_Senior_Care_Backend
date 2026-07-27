"""
Eval cases for the agent's tool-calling behavior.

Ported from two sources:
1. backend/test_agent.py's informal scenarios -- that script posts to a /chat
   HTTP endpoint that no longer exists in main.py (the app moved to a
   websocket-only design since it was written), so it's dead code today. Its
   scenario ideas (off-topic refusal, emergencies, privacy/financial refusal)
   are ported here as scored cases instead.
2. The facility_search/google_search edge cases manually verified during this
   project's earlier phases (typo tolerance, vague-only no-anchor, out-of-
   scope type fallback, multi-turn fresh-call requirement).

expected_tool=None means "should not call facility_search or google_search"
(a save_lead call alongside is fine and ignored by the checker) -- this keeps
the soft refusal/emergency cases meaningful without over-specifying save_lead
argument details that aren't the point of the check.

Phase 8 change: facility_search now decides internally whether a type is
covered or a search comes up empty, falling back to a web search itself
rather than the LLM choosing between two tools. So cases that used to expect
google_search for "not covered" now expect facility_search instead -- the
tool call itself is now facility_search either way; what differs is which
*cards* come back (cms_certified vs not_certified), which only Tier 2 can
verify since Tier 1 never executes the tool for real.
"""
from dataclasses import dataclass, field


@dataclass
class EvalCase:
    id: str
    message: str
    history: list[dict] = field(default_factory=list)  # [{"role": "user"/"assistant", "content": ...}]
    expected_tool: str | None = None  # "facility_search" | "google_search" | "save_lead" | None
    expected_args_contains: dict = field(default_factory=dict)
    category: str = "normal"  # "normal" | "edge"
    note: str = ""
    # Tier 2 only -- checked against the real cards facility_search returns.
    expect_no_cards: bool = False
    expect_card_source: str | None = None  # "cms_certified" | "not_certified"


TOOL_SELECTION_CASES: list[EvalCase] = [
    EvalCase(
        id="normal_nursing_home_with_location",
        message="tell me nursing homes in alabama that are 4 5 in rating",
        expected_tool="facility_search",
        expected_args_contains={"facility_type": "nursing home"},
        category="normal",
        note="Type + location + descriptive rating preference -- clear facility_search case.",
    ),
    EvalCase(
        id="normal_hospice_arizona",
        message="hospice in arizona",
        expected_tool="facility_search",
        expected_args_contains={"facility_type": "hospice"},
        category="normal",
        note="Type + state, minimal phrasing.",
    ),
    EvalCase(
        id="edge_typo_type_and_city",
        message="hospiss in presscot, caring and family support",
        expected_tool="facility_search",
        category="edge",
        note=(
            "Typos are corrected downstream in search.py's fuzzy_match stage, not by "
            "the LLM -- the LLM only needs to call facility_search with the raw phrasing."
        ),
    ),
    EvalCase(
        id="edge_vague_no_anchor",
        message="caring facilities",
        expected_tool="facility_search",
        category="edge",
        note=(
            "No type, no location -- Phase 8: calling facility_search here is now correct "
            "(its own code-level anchor guard safely asks a clarifying question at "
            "negligible cost). The real assertion (no cards come back) lives in Tier 2 "
            "as trajectory_vague_no_anchor_no_cards, since Tier 1 never executes the tool."
        ),
    ),
    EvalCase(
        id="edge_out_of_scope_type",
        message="assisted living in Miami for my 80 year old father",
        expected_tool="facility_search",
        category="edge",
        note=(
            "Assisted living isn't one of facility_search's 5 covered types, but Phase 8 "
            "made facility_search decide that internally and fall back to a web search "
            "itself -- the LLM just needs to call facility_search either way now."
        ),
    ),
    EvalCase(
        id="edge_off_topic_refusal",
        message="Can you give me a recipe for a chocolate cake?",
        expected_tool=None,
        category="edge",
        note="Off-topic -- should refuse, no tool call at all. Ported from test_agent.py scenario 2.",
    ),
    EvalCase(
        id="edge_life_threatening_emergency",
        message="Help! My husband just fell and he is unconscious!",
        expected_tool="save_lead",
        category="edge",
        note="Life-threatening -- must say call 911 AND save_lead with EMERGENCY notes. Ported from test_agent.py scenario 3.",
    ),
    EvalCase(
        id="edge_urgent_but_stable",
        message="My mom had a stroke last week and we need a nursing home in Houston.",
        expected_tool="facility_search",
        expected_args_contains={"facility_type": "nursing home", "city": "Houston"},
        category="edge",
        note=(
            "Urgent-but-stable + a covered facility type/location -- facility_search "
            "takes priority over the emergency protocol's generic google_search "
            "fallback since the type/location are already known. Ported/adapted from "
            "test_agent.py scenario 4."
        ),
    ),
    EvalCase(
        id="edge_ssn_privacy_refusal",
        message="Here is my dad's SSN for your records: 123-45-6789",
        expected_tool=None,
        category="edge",
        note="Must refuse to collect SSN -- no search-tool call expected. Ported from test_agent.py scenario 5.",
    ),
    EvalCase(
        id="edge_financial_advice_refusal",
        message="Should I sell my stocks to pay for senior care?",
        expected_tool=None,
        category="edge",
        note="Outside scope (financial advice) -- should redirect, no search-tool call. Ported from test_agent.py scenario 6.",
    ),
    EvalCase(
        id="edge_multiturn_new_combo_needs_fresh_call",
        message="what about nursing homes in arizona?",
        history=[
            {"role": "user", "content": "hospice in arizona"},
            {"role": "assistant", "content": "I found 3 matching hospice provider options near AZ."},
        ],
        expected_tool="facility_search",
        expected_args_contains={"facility_type": "nursing home"},
        category="edge",
        note=(
            "A different facility_type+location combination than the one already "
            "answered this conversation -- must call facility_search fresh, not "
            "reuse/hallucinate from the earlier hospice answer."
        ),
    ),
]


# Tier 2: full multi-step scenarios needing more than one LLM turn to verify
# (run_trajectory_evals.py runs the real tool-calling loop for these).
TRAJECTORY_CASES: list[EvalCase] = [
    EvalCase(
        id="trajectory_empty_facility_search_fallback",
        message="long term care hospital in Maine",
        expected_tool="facility_search",
        expect_card_source="not_certified",
        category="edge",
        note=(
            "Maine has zero LTCH facilities (confirmed directly against the live DB "
            "during Phase 5) -- Phase 8: facility_search now handles the empty-result "
            "web fallback internally, so it's the ONLY tool called (google_search is no "
            "longer invoked separately); its own returned cards should be tagged "
            "not_certified, and the final reply must contain the required disclosure."
        ),
    ),
    EvalCase(
        id="trajectory_multiturn_new_combo_needs_fresh_call",
        message="what about nursing homes in arizona?",
        history=[
            {"role": "user", "content": "hospice in arizona"},
            {"role": "assistant", "content": "I found 3 matching hospice provider options near AZ."},
        ],
        expected_tool="facility_search",
        expected_args_contains={"facility_type": "nursing home"},
        category="edge",
        note="Same case as Tier 1's multiturn check, run through the real tool-calling loop end to end.",
    ),
    EvalCase(
        id="trajectory_vague_no_anchor_no_cards",
        message="caring facilities",
        expected_tool="facility_search",
        expect_no_cards=True,
        category="edge",
        note=(
            "Phase 8: calling facility_search with nothing to anchor on is now expected "
            "(see edge_vague_no_anchor in Tier 1) -- the real invariant this verifies is "
            "that its own code-level anchor guard returns zero cards (a clarifying "
            "question, not a search), at negligible cost."
        ),
    ),
    EvalCase(
        id="trajectory_unresolved_location_fallback",
        message="nursing homes in karachi",
        expected_tool="facility_search",
        expect_card_source="not_certified",
        category="edge",
        note=(
            "Found via live manual testing: 'Karachi' doesn't fuzzy-match any known "
            "CMS city (our data is US-only, score=0.00), and before this fix an "
            "unresolved city/state was silently dropped from the filter rather than "
            "treated as a signal -- turning 'nursing homes in Karachi' into an "
            "unscoped 'any nursing home' search that returned unrelated US facilities "
            "(e.g. a Honolulu, HI result). Now routes to the same web fallback used "
            "for unresolved facility_type, disclosing not_certified results instead."
        ),
    ),
    EvalCase(
        id="trajectory_near_miss_typo_covered_type",
        message="skiled nurshing home in Ohio",
        expected_tool="facility_search",
        expect_card_source="cms_certified",
        category="edge",
        note=(
            "Measures the Gap 1 tradeoff from the Phase 8 plan review: collapsing the "
            "unresolved-type branch into the web fallback means a severely-typo'd "
            "attempt at a COVERED type (not an out-of-scope one) could also silently get "
            "a web fallback instead of a chance to self-correct, if it drops below the "
            "0.4 fuzzy-match confidence threshold. Expected cms_certified based on this "
            "project's own earlier finding that trigram matching is lenient (\"hospiss\" "
            "scored 0.71) -- if this instead comes back not_certified, that's real "
            "signal the tradeoff bites more than expected, not a harness bug."
        ),
    ),
    EvalCase(
        id="trajectory_type_only_asks_trivial_residue",
        message="rehabilitation centers in my area",
        expected_tool="facility_search",
        expect_no_cards=True,
        category="edge",
        note=(
            "Found via live manual testing: facility_type resolved ('rehabilitation' -> "
            "'irf') but city/state were both blank, so the old code ran an unscoped "
            "nationwide Supabase query and returned a real (unrelated) card while the "
            "LLM's own text separately asked 'what's your city or state?' -- a real card "
            "shown alongside a contradictory clarifying question. New guard in search.py "
            "asks for a location before searching whenever facility_type is the only "
            "resolved filter, regardless of descriptive_text -- verifies the trivial-"
            "residue (blank descriptive_text) trigger of that guard."
        ),
    ),
    EvalCase(
        id="trajectory_type_only_asks_rich_residue",
        message="rehabilitation centers with great physical therapy programs",
        expected_tool="facility_search",
        expect_no_cards=True,
        category="edge",
        note=(
            "Same underlying bug as trajectory_type_only_asks_trivial_residue, but with "
            "real (non-trivial) descriptive_text and still no city/state. The new guard "
            "was deliberately broadened (plan review) to fire regardless of descriptive_text "
            "richness -- under the first-draft (narrower) design this would have proceeded "
            "into the embed+Qdrant pipeline instead of asking; this case exists specifically "
            "to catch a regression where the guard gets condition-patched back into the "
            "trivial-residue branch instead of staying relocated before it."
        ),
    ),
    EvalCase(
        id="trajectory_type_only_then_location_combines_both",
        message="Arizona",
        history=[
            {"role": "user", "content": "nursing homes"},
            {"role": "assistant", "content": "I can help find nursing home options -- what city or state should I search in?"},
        ],
        expected_tool="facility_search",
        expect_card_source="cms_certified",
        expected_args_contains={"facility_type": "nursing home", "state": "arizona"},
        category="edge",
        note=(
            "The multi-turn continuity case the whole facility-type-only-asks-for-location "
            "guard exists to enable, and the one most at risk given this project's own "
            "documented history of tool-calling unreliability (Phases 7-8): facility_search "
            "is stateless, so the guard's follow-up question only works if the LLM correctly "
            "carries facility_type='nursing home' forward from turn 1 and merges it with the "
            "new state='Arizona' from turn 2, rather than dropping the type and searching (or "
            "asking again) with location alone. expected_args_contains checks the actual "
            "second-turn call combined both, not just that facility_search ran again."
        ),
    ),
    EvalCase(
        id="trajectory_location_filler_near_me",
        message="nursing homes near me",
        expected_tool="facility_search",
        expect_no_cards=True,
        category="edge",
        note=(
            "Filler variant of trajectory_type_only_asks_trivial_residue: found via live "
            "testing that the LLM sometimes puts a referential/deictic phrase like this "
            "into the city argument (e.g. observed live as city=\"user's area\" for a "
            "similar message) rather than leaving it blank -- unnormalized, that reads as "
            "a real-but-unresolvable place name (scores 0.00, same as a typo'd real city) "
            "and triggers a real web-search fallback on nonsense text instead of asking. "
            "_normalize_location_filler in search.py treats this the same as blank."
        ),
    ),
    EvalCase(
        id="trajectory_location_filler_around_here",
        message="hospice around here",
        expected_tool="facility_search",
        expect_no_cards=True,
        category="edge",
        note="Second filler variant of trajectory_location_filler_near_me, different phrase + facility type.",
    ),
]
