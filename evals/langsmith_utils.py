"""
Shared LangSmith dataset sync helper for the Tier 1/Tier 2 eval runners.

Discovered by actually running evaluate() against a local Iterable[Example]
(the nil-UUID dataset_id path confirmed valid in the plan's verification):
evaluate()'s default upload_results=True still tries to create a real
server-side experiment tied to that dataset_id, which 404s ("Reference
dataset not found") since a locally-constructed Example was never registered
with LangSmith. So a real dataset has to exist first -- this helper deletes
and recreates it fresh from the current evals/dataset.py cases every run, the
same "always re-sync from code" idempotency pattern already used elsewhere in
this project (e.g. qdrant_index.ensure_collection()), so the LangSmith
dataset never drifts from what's actually being tested.
"""
from langsmith import Client

from evals.dataset import EvalCase


def sync_dataset(client: Client, dataset_name: str, cases: list[EvalCase], description: str) -> str:
    if client.has_dataset(dataset_name=dataset_name):
        client.delete_dataset(dataset_name=dataset_name)
    dataset = client.create_dataset(dataset_name=dataset_name, description=description)
    client.create_examples(
        dataset_id=dataset.id,
        examples=[
            {
                "inputs": {"history": c.history, "message": c.message},
                "outputs": {
                    "expected_tool": c.expected_tool,
                    "expected_args_contains": c.expected_args_contains,
                    "expect_no_cards": c.expect_no_cards,
                    "expect_card_source": c.expect_card_source,
                },
                "metadata": {"case_id": c.id, "category": c.category, "note": c.note},
            }
            for c in cases
        ],
    )
    return dataset_name
