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
from langchain_google_genai import ChatGoogleGenerativeAI

# The google-genai library warns on every call that automatic function calling
# is better used through its chat API. This project doesn't use function calling
# at all, so the warning is pure noise -- and noise that reads like an error is
# worse than useless when you're trying to see whether your own code worked.
# Narrowed to this one logger on purpose: a blanket warnings filter would also
# hide the next warning, which might matter.
# Since the annoying message is logged at WARNING level, setting the threshold to ERROR means warnings are now suppressed — they fall below the bar — while genuine ERROR and CRITICAL messages still get through
logging.getLogger("google_genai.models").setLevel(logging.ERROR)
# To filter out warning from gemini-3.6-flash for not accepting the temperature parameter 
warnings.filterwarnings(
    "ignore",
    message=r".*fixed sampling defaults.*",
    category=UserWarning
)

# Pinned to a specific version rather than the floating "gemini-flash-latest"
# alias. An alias would silently change the model under you, so a prompt that
# worked yesterday could behave differently today with nothing in the code to
# explain it. Pinning means upgrades happen when you edit this line.
#
# Note that gemini-2.5-flash is retired -- the API still lists it, but calling it
# on a new key returns 404. If this line ever starts 404ing, list the live models
# with:
#   from google import genai
#   genai.Client(api_key=...).models.list()
# MODEL_NAME = "gemini-3.7-flash"
MODEL_NAME = "gemini-3.6-flash"


def get_llm(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    """Build a model client.

    Temperature defaults to 0 so the same question produces the same snippet.
    That matters more than it sounds: when something breaks, you want to be
    debugging your prompt, not a dice roll you can't reproduce.

    It stays an argument rather than a constant because it is a real tradeoff,
    not just a convention. Once retries exist, a second attempt that failed at
    temperature 0 will tend to reproduce the same wrong code -- some randomness
    is what lets it try a different approach instead of the same one twice.
    """
    return ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=temperature)