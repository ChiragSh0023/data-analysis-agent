"""Turns the raw output of the snippet into a sentence.

This is a second model call purely for wording -- it does no arithmetic and is
told not to. The number it reports was computed by pandas; the model's only job
is to say what that number means in the context of the question.
"""

from llm import get_llm
from prompts import EXPLAIN_PROMPT
from state import AnalysisState


def explain(state: AnalysisState) -> dict:
    """Graph node: question + code + result in, one sentence out.

    Returns nothing at all when the previous node failed. The graph is a straight
    line for now, so a failed run still arrives here; without this guard the node
    would spend an API call asking the model to interpret `None` and get a
    confident sentence about nothing back. `main.py` reports the error instead.

    This guard disappears once the conditional edge exists, because then failures
    never reach this node in the first place.
    """
    if state.get("error"):
        return {}

    prompt = EXPLAIN_PROMPT.format(
        question=state["question"],
        code=state["code"],
        result=state["result"],
    )

    response = get_llm().invoke(prompt)

    return {"answer": response.text.strip()}