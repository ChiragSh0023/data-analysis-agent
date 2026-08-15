"""Asks the model for a pandas snippet that answers the question."""

from llm import get_llm
from prompts import CODE_PROMPT
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
    """Graph node: question + schema in, pandas snippet out."""
    prompt = CODE_PROMPT.format(schema=state["schema"], question=state["question"])

    response = get_llm().invoke(prompt)

    # .text, not .content: this version of langchain-core returns content as a
    # list of typed blocks, and .text is the accessor that flattens it back to a
    # plain string.
    return {"code": _strip_fences(response.text)}