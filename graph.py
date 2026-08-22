"""Graph wiring: which node runs, and what runs after it.

This is where the system stops being a pipeline and becomes agentic. The shape is
no longer a line -- `run_code` can send control *backwards* to `write_code`, so a
snippet that crashed gets rewritten with the traceback in hand and tried again.

The two routing functions below are the whole of the control flow. "Where does
this go after an error?" is answered by reading `route_after_run`, not by tracing
`if` statements scattered through the nodes.
"""

from langgraph.graph import END, START, StateGraph

from nodes.explain import explain
from nodes.give_up import give_up
from nodes.run_code import run_code
from nodes.write_code import write_code
from state import AnalysisState

# Mandatory, not a tuning knob. A cycle plus a model that keeps failing is an
# infinite loop that bills real money on every lap.
MAX_ATTEMPTS = 3


def route_after_write(state: AnalysisState) -> str:
    """Did the model produce runnable code, or refuse the question?"""
    if state.get("unanswerable"):
        return "unanswerable"
    return "ok"


def route_after_run(state: AnalysisState) -> str:
    """Did the snippet work, and if not, is another attempt allowed?"""
    if not state.get("error"):
        return "ok"

    if state.get("attempts", 0) < MAX_ATTEMPTS:
        return "retry"

    return "exhausted"


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
    builder.add_node("give_up", give_up)

    builder.add_edge(START, "write_code")

    # Two routers rather than one, because they answer questions asked at
    # different moments. Folding them together would mean a single function
    # reasoning about two unrelated points in the flow.
    #
    # The dict is not decoration: the router returns a *decision* ("retry"), and
    # the dict says where that decision leads. Keeping them apart means you can
    # rename a node without touching the routing logic, and the set of possible
    # destinations is visible here rather than buried in return statements.
    builder.add_conditional_edges(
        "write_code",
        route_after_write,
        {
            "ok": "run_code",
            "unanswerable": END,
        },
    )

    builder.add_conditional_edges(
        "run_code",
        route_after_run,
        {
            "ok": "explain",
            "retry": "write_code",  # the cycle
            "exhausted": "give_up",
        },
    )

    # Both terminal nodes end the run. They are separate nodes rather than one
    # node with an if, because "we answered" and "we ran out of tries" are
    # different outcomes that happen to share an exit.
    builder.add_edge("explain", END)
    builder.add_edge("give_up", END)

    return builder.compile()
