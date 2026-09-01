"""The one place the model is configured.

Two nodes need a model. If each built its own, swapping models would be a
two-file edit and the two could quietly drift apart -- one on a new model, one
left behind, with the difference invisible until output got strange. One factory
function makes the swap a single line.

The API key is never handled here. `ChatGoogleGenerativeAI` reads GOOGLE_API_KEY
from the environment on its own, and `main.py` loads `.env` into the environment
at startup. Nothing in this project should ever print, log, or pass around the
key itself.
"""

import logging
import warnings
from typing import Optional, Tuple

from langchain_google_genai import ChatGoogleGenerativeAI

# The google-genai library warns on every call that automatic function callingis better used through its chat API. This project doesn't use function calling at all, so the warning is pure noise 
logging.getLogger("google_genai.models").setLevel(logging.ERROR)
# To filter out warning from gemini-3.6-flash for not accepting the temperature parameter 
warnings.filterwarnings(
    "ignore",
    message=r".*fixed sampling defaults.*",
    category=UserWarning
)

# MODEL_NAME = "gemini-3.7-flash"
MODEL_NAME = "gemini-3.6-flash"


def get_llm(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=temperature)

# Longest API error worth putting in front of a person. Gemini's 429 payload is ~1500 characters of JSON; the first couple of hundred carry the actual meaning.
ERROR_CHARS = 240

# Called by explain, since it outputs a string
def invoke_model(prompt: str, temperature: float = 0.0) -> Tuple[Optional[str], Optional[str]]:
    """Call the model and return (text, api_error) -- exactly one of them set.

    This exists so that a model call can fail the way `run_code` fails: as data
    the graph can route on, not as an exception that unwinds the whole run. Every
    API failure hit while building this -- a 503 outage, two 429 quota walls --
    killed an otherwise healthy run and printed a hundred lines of library
    traceback. The node that runs *untrusted generated code* was more careful
    about this than the nodes making ordinary network calls.

    `except Exception` is deliberately broad, and safe here only because the try
    block contains exactly one statement. Prompt formatting happens before it, so
    a bug in a template still raises loudly instead of being swallowed and
    reported as an API problem.

    No retry loop of our own: the google-genai client already retries internally
    (that is the tenacity frames in the traceback). Wrapping a second retry
    around it would multiply a 503 into minutes of silent waiting, and would not
    help a 429 at all, since a daily quota does not recover in seconds.
    """
    try:
        response = get_llm(temperature=temperature).invoke(prompt)
    except Exception as exc:
        return None, _describe(exc)

    return response.text, None

# Called by write_code, since it outputs an object
def invoke_structured(prompt: str, schema, temperature: float = 0.0):
    """Like `invoke_model`, but the reply comes back as an object, not text.

    `with_structured_output(schema)` makes the model fill in a declared shape
    instead of writing free text we then have to parse. That removes two pieces
    of guesswork at once: stripping ```python fences the model was asked not to
    add, and recognising a `CANNOT_ANSWER:` prefix by string comparison. A field
    named `can_answer` cannot be misspelled, wrapped in markdown, or phrased
    three different ways.

    What it costs: the reply shape is now enforced by the provider rather than by
    our own parsing, so a model that doesn't support structured output fails
    outright instead of degrading. Verified working on gemini-3.6-flash before
    this was built on.
    """
    model = get_llm(temperature=temperature).with_structured_output(schema)
    try:
        reply = model.invoke(prompt)
    except Exception as exc:
        return None, _describe(exc)

    return reply, None


def _describe(exc: Exception) -> str:
    """One-line, length-capped rendering of an API failure."""
    detail = str(exc).replace("\n", " ")
    if len(detail) > ERROR_CHARS:
        detail = detail[:ERROR_CHARS] + " [...]"
    return f"{type(exc).__name__}: {detail}"
