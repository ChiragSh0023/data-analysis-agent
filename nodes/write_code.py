"""Asks the model for a pandas snippet that answers the question."""

from llm import invoke_model
from prompts import CODE_PROMPT, RETRY_SECTION
from state import AnalysisState


def _strip_fences(text: str) -> str:
    """Remove a surrounding ```python ... ``` block if the model added one.

    The prompt asks for bare code, and models wrap it in markdown anyway -- often
    enough that skipping this guarantees an eventual SyntaxError on line 1, from
    code that was otherwise perfectly good.

    The real fix is structured output, where the model returns a typed object and
    the fence question never arises. That is a concept for a later step, so this
    stays a string operation for now.
    """
    text = text.strip()

    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    lines = lines[1:]  # drop the opening ``` or ```python
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]

    return "\n".join(lines).strip()


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
    code, api_error = invoke_model(prompt, temperature=0.3 if is_retry else 0.0)

    if api_error:
        # The model was unreachable, so no attempt actually happened -- `attempts`
        # is deliberately not incremented. Charging an attempt for a call that
        # never reached the model would spend the retry budget on an outage.
        return {"api_error": api_error}

    code = _strip_fences(code)
    
    if code.startswith("CANNOT_ANSWER"):
        # split only on the first colon and leave the rest intact, in case the reason also has colon
        parts = code.split(':', maxsplit=1)
        reason = parts[1].strip() if len(parts) > 1 else "No reason given" # Ex: code came out as "CANNOT_ANSWER" only
        return {"unanswerable": reason, "attempts": attempts + 1}

    # .text, not .content: this version of langchain-core returns content as a
    # list of typed blocks, and .text is the accessor that flattens it back to a
    # plain string.
    return {"code": code, "attempts": attempts + 1}