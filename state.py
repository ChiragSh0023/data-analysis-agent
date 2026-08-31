"""The single object that travels through every node in the graph.

Each node receives the whole state and returns a *partial* dict containing only
the keys it changed. LangGraph merges that partial back in. Keeping it flat and
boring is deliberate: every field here is something a node reads or writes, and
nothing else.

`error` and `result` are separate fields rather than one combined `outcome`, so
that later, when the retry cycle exists, the routing function can branch on a
plain `is None` check instead of inspecting a payload.

TypedDict is a way to write down the shape of a dictionary — which keys it has and what type of value each key holds.
"""

from typing import Optional, TypedDict

# total=False makes all keys optional
# TypedDict helps to declare what value type keys will hold
class AnalysisState(TypedDict, total=False):
    question: str
    csv_path: str
    schema: str
    code: str
    result: Optional[str]
    error: Optional[str]
    unanswerable: Optional[str]
    answer: Optional[str]
    # The model itself could not be reached -- quota, outage, network. Kept apart
    # from `error` on purpose: `error` means the generated code crashed, which is
    # worth retrying, while this means the API is unavailable, which is not.
    # Folding them together would spend all three attempts re-hitting a quota
    # wall that is not going to move.
    api_error: Optional[str]
    # How many times write_code has run. The router compares this against
    # MAX_ATTEMPTS in graph.py; without it a failing model loops forever.
    attempts: int
