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