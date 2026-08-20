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

    This node used to open with a guard clause, because the straight-line graph
    walked into it even after a failed run. The router now sends failures
    elsewhere, so the guard is gone rather than left behind as dead weight: a
    check that can never fire still has to be read and understood by whoever
    comes next.
    """
    prompt = EXPLAIN_PROMPT.format(
        question=state["question"],
        code=state["code"],
        result=state["result"],
    )

    response = get_llm().invoke(prompt)

    return {"answer": response.text.strip()}