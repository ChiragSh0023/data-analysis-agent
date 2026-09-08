"""Run one analysis and return it as data. Nothing here prints.

This module exists because `main.py` was doing three jobs at once: describing the
CSV, running the graph, and formatting output for a terminal. A web layer needs
the first two and none of the third.

Splitting them means neither presentation layer knows the other exists. `main.py`
renders this to stdout; `server.py` renders the same dict to JSON. Neither can
break the other, and the analysis itself has no opinion about where it is going.
"""

import uuid

import pandas as pd
from langgraph.checkpoint.memory import MemorySaver

from graph import MAX_ATTEMPTS, build_graph

SAMPLE_ROWS = 3


def build_schema_summary(csv_path: str) -> str:
    """Describe a CSV to the model in plain text.

    The model never sees the data itself, only this summary: how big the table
    is, what the columns are called, what type each one holds, and a few example
    rows. The example rows exist to settle questions the dtypes can't answer --
    whether `quarter` looks like "Q3" or "2024-Q3" or a bare 3 -- which is
    exactly the kind of guess that produces code that runs and returns nonsense.

    Three rows is enough for that, and keeping it small matters: on a real file
    every extra row is both tokens spent and actual data leaving the machine.
    """
    df = pd.read_csv(csv_path)

    lines = [f"The DataFrame `df` has {len(df)} rows and {len(df.columns)} columns."]

    lines.append("\nColumns:")
    for name, dtype in df.dtypes.items():
        lines.append(f"  - {name} ({dtype})")

    lines.append(f"\nFirst {SAMPLE_ROWS} rows:")
    lines.append(df.head(SAMPLE_ROWS).to_string(index=False))

    return "\n".join(lines)


def describe_columns(csv_path: str) -> list[dict]:
    """Column names and dtypes as data, for a UI to render as a table.

    Separate from `build_schema_summary` because they have different audiences.
    The summary is prose aimed at a model; this is structured data aimed at a
    template. Formatting one into the other at the call site would mean parsing
    text we already had in a better shape.
    """
    df = pd.read_csv(csv_path)
    return [{"name": str(name), "dtype": str(dtype)} for name, dtype in df.dtypes.items()]


def _summarise(node: str, values: dict) -> str:
    """One-line description of what a node left behind in the state."""
    if node == "write_code":
        if values.get("api_error"):
            return "could not reach the model"
        if values.get("unanswerable"):
            return "declined the question"
        return f"wrote code (attempt {values.get('attempts')})"

    if node == "run_code":
        if values.get("error"):
            return f"failed -- {values['error'].splitlines()[-1]}"
        return f"ran, printed {len((values.get('result') or '').splitlines())} line(s)"

    if node == "explain":
        if values.get("api_error"):
            return "could not reach the model"
        return "worded the answer"

    if node == "give_up":
        return "reported the failure"

    return ""


def _collect_history(graph, config) -> list[dict]:
    """Which nodes ran, in order, and what each one did.

    This is what the checkpointer buys. Without it you see only the final state,
    so a run that retried twice looks exactly like one that succeeded first try.
    The cycle is visible as repetition: `write_code` appearing more than once
    *is* the retry.

    Snapshots come back newest-first, and each records the node *about to* run
    rather than the one that just did, so the executed path is read from
    consecutive pairs: the earlier snapshot's `next` ran, and the later snapshot
    holds the state it produced.

    Returns a list of dicts rather than formatted lines, so a terminal can print
    them and a browser can render them without either reformatting the other's
    output.
    """
    snapshots = list(graph.get_state_history(config))
    snapshots.reverse()

    history = []
    for before, after in zip(snapshots, snapshots[1:]):
        if not before.next:
            continue
        node = before.next[0]
        if node.startswith("__"):  # LangGraph's own __start__ bookkeeping
            continue
        history.append(
            {
                "step": len(history) + 1,
                "node": node,
                "detail": _summarise(node, after.values),
            }
        )

    return history


def _next_action(node: str, state: dict) -> str | None:
    """What is about to happen, given the node that just finished.

    The graph reports node *completions*, but the useful thing to show someone
    waiting is what is starting now. So each finished node is translated into the
    next thing in progress, and the terminal nodes translate to nothing because
    there is no next thing.

    This is also where the retry becomes visible as it happens rather than only
    in the history afterwards: an error with attempts left reads as "found an
    error", which is the honest description of what the system is doing.
    """
    if node == "write_code":
        if state.get("api_error"):
            return None
        if state.get("unanswerable"):
            return None
        return "running the code"

    if node == "run_code":
        if state.get("error"):
            if state.get("attempts", 0) < MAX_ATTEMPTS:
                return "found an error - rewriting the code"
            return "out of attempts - writing up what failed"
        return "explaining the result"

    return None


def stream_analysis(question: str, csv_path: str):
    """Run the graph, yielding progress events and finally the report.

    Two kinds of event, distinguished by `type`:

      {"type": "progress", "detail": "running the code"}
      {"type": "result",   "report": {...}}

    Progress exists because this model regularly takes minutes, and a spinner
    that says "thinking" for three minutes tells you nothing about whether it is
    on its first attempt or its third.

    `stream_mode="updates"` gives one chunk per node, holding exactly the partial
    dict that node returned. Accumulating those partials here rebuilds the state
    as the run goes, which is what `_next_action` needs to tell a retry apart
    from a first attempt.
    """
    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    initial = {
        "question": question,
        "csv_path": csv_path,
        "schema": build_schema_summary(csv_path),
    }

    # The first node starts before the graph reports anything, so its label is
    # emitted here rather than inferred from a completion.
    yield {"type": "progress", "detail": "writing the code"}

    state = dict(initial)
    for chunk in graph.stream(initial, config=config, stream_mode="updates"):
        for node, update in chunk.items():
            if update:
                state.update(update)
            detail = _next_action(node, state)
            if detail:
                yield {"type": "progress", "node": node, "detail": detail}

    final_state = graph.get_state(config).values

    yield {
        "type": "result",
        "report": {
            "question": final_state["question"],
            "code": final_state.get("code"),
            "result": final_state.get("result"),
            "answer": final_state.get("answer"),
            "unanswerable": final_state.get("unanswerable"),
            "attempts": final_state.get("attempts", 0),
            "history": _collect_history(graph, config),
        },
    }


def run_analysis(question: str, csv_path: str) -> dict:
    """Run the graph once against one CSV and return everything worth showing.

    The returned dict is deliberately flat and JSON-safe: no state objects, no
    snapshots, nothing that only makes sense inside this process. `server.py`
    hands it straight to the client.

    `code` is always included when there is one, on success and failure alike.
    The risk this architecture cannot eliminate is code that runs fine and
    returns a confidently wrong number, and showing the code is the only defence
    against it -- so no caller should be able to render a result without it.
    """
    # Runs the streaming version and keeps only the final report, so the two
    # entry points can never disagree about what a run produced.
    for event in stream_analysis(question, csv_path):
        if event["type"] == "result":
            return event["report"]

    raise RuntimeError("the analysis stream ended without producing a result")
