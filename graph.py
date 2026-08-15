"""Graph wiring: which node runs, and what runs after it.

Right now this is a straight line -- write the code, run it, explain the result.
The interesting version comes later, when a conditional edge sends failures back
to `write_code` instead of forward. Keeping that edge out for now means the graph
is boring enough to read in one sitting, which is the point of this stage.

One consequence of the straight line worth knowing: if `run_code` reports an
error, the graph still walks on into `explain`. Nothing here checks. `main.py`
inspects the final state and prints the traceback instead of an answer.
"""

from langgraph.graph import END, START, StateGraph

from nodes.explain import explain
from nodes.run_code import run_code
from nodes.write_code import write_code
from state import AnalysisState


def build_graph():
    """Assemble and compile the graph.

    A function rather than module-level code so that importing this module has
    no side effects -- nothing is built until someone asks for it.
    """
    builder = StateGraph(AnalysisState)

    # Node names are strings, and the edges below refer to them by those
    # strings. Spelling one wrong is a runtime error, not an import error.
    builder.add_node("write_code", write_code)
    builder.add_node("run_code", run_code)
    builder.add_node("explain", explain)

    builder.add_edge(START, "write_code")
    builder.add_edge("write_code", "run_code")
    builder.add_edge("run_code", "explain")
    builder.add_edge("explain", END)

    return builder.compile()