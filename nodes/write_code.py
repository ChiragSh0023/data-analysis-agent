"""Asks the model for a pandas snippet that answers the question."""

from typing import Optional
from pydantic import BaseModel, Field
from llm import invoke_structured
from prompts import CODE_PROMPT, RETRY_SECTION
from state import AnalysisState


class CodeReply(BaseModel):
    """The shape the model must fill in, instead of writing free text we parse.

    This class replaced two pieces of guesswork. The first was `_strip_fences`,
    which cut ```python wrappers off replies that were asked not to have them --
    a fence left in place is a guaranteed SyntaxError on line 1. The second was
    checking `code.startswith("CANNOT_ANSWER")` and splitting on a colon, which
    worked only while the model spelled the token exactly right and never
    prefixed it with anything.

    Both were string comparisons standing in for a decision the model had already
    made. `can_answer` is that decision as a boolean: it cannot be misspelled,
    wrapped in markdown, or phrased three different ways.

    The field descriptions are not comments -- they are sent to the model as part
    of the schema, so they are doing the same job as prompt text.
    """

    can_answer: bool = Field(
        description="True if the question can be answered from the listed columns."
    )
    code: Optional[str] = Field(
        default=None,
        description="The pandas snippet, bare Python with no markdown fences. Null if can_answer is false.",
    )
    reason: Optional[str] = Field(
        default=None,
        description="Short explanation of why the question cannot be answered. Null if can_answer is true.",
    )


def write_code(state: AnalysisState) -> dict:
    """Graph node: question + schema in, pandas snippet out.

    On a retry this node is re-entered with `error` still holding the traceback
    from the run that just failed. That traceback goes into the prompt, which is
    what makes a second attempt worth paying for.
    """
    attempts = state.get("attempts", 0)
    is_retry = bool(state.get("error"))

    retry_section = ""
    if is_retry:
        retry_section = RETRY_SECTION.format(
            code=state["code"], error=state["error"]
        )

    prompt = CODE_PROMPT.format(
        schema=state["schema"],
        question=state["question"],
        retry_section=retry_section,
    )

    # First attempt is deterministic so the same question gives the same code and
    # you are debugging your prompt, not a dice roll. A retry loosens that on
    # purpose: at temperature 0 the model tends to reproduce the code that just
    # failed, and a retry that repeats itself is only an invoice.
    reply, api_error = invoke_structured(
        prompt, CodeReply, temperature=0.3 if is_retry else 0.0
    )

    if api_error:
        # The model was unreachable, so no attempt actually happened -- `attempts`
        # is deliberately not incremented. Charging an attempt for a call that
        # never reached the model would spend the retry budget on an outage.
        return {"api_error": api_error}

    if not reply.can_answer:
        return {
            "unanswerable": reply.reason or "No reason given",
            "attempts": attempts + 1,
        }

    # A schema guarantees the *shape*, not that the fields agree with each other:
    # the model can still answer can_answer=true and leave `code` empty. Treating
    # that as unanswerable keeps a None out of `run_code`, which would otherwise
    # fail in a much more confusing place.
    if not reply.code:
        return {
            "unanswerable": "The model said the question was answerable but returned no code.",
            "attempts": attempts + 1,
        }

    return {"code": reply.code, "attempts": attempts + 1}