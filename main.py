"""Entry point: one hardcoded question in, one plain-English answer out.

Run it with:

    .venv/bin/python main.py
"""

import sys
import uuid

import pandas as pd
from dotenv import load_dotenv
from langgraph.checkpoint.memory import MemorySaver

from graph import build_graph

# Both are defaults now, overridable from the command line:
#
#   python main.py
#   python main.py "which region had the biggest drop in Q3?"
#   python main.py "what is the average sales?" data/messy_sales.csv
#
# Read straight from sys.argv rather than through argparse. Two positional
# arguments with no flags do not need a parser, and argparse would add a screen
# of setup to save nothing. If this ever grows options (--model, --max-attempts),
# switch then -- that is the point where hand-rolling starts costing more than it
# saves.
DEFAULT_CSV_PATH = "data/messy_sales.csv"
DEFAULT_QUESTION = "what is the average units sold?"

SAMPLE_ROWS = 3


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


def print_history(graph, config) -> None:
    """Shows which nodes ran, in order, and what each one did.

    This is what the checkpointer buys. Without it you see only the final state,
    so a run that retried twice looks exactly like one that succeeded first try.
    Here the cycle is visible as repetition: write_code appearing more than once
    *is* the retry.

    Snapshots come back newest-first, and each one records the node that is about
    to run, so the executed path is read from consecutive pairs: the `next` of the
    earlier snapshot ran, and the later snapshot holds the state it produced.
    """
    snapshots = list(graph.get_state_history(config))
    snapshots.reverse()

    print("\nWhat happened, step by step:")

    step = 0
    for before, after in zip(snapshots, snapshots[1:]):
        if not before.next:
            continue
        node = before.next[0]
        if node.startswith("__"):  # LangGraph's own __start__ bookkeeping
            continue
        step += 1
        print(f"  {step}. {node:<11} {_summarise(node, after.values)}")


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
    # joins every string in the lines list into one big string, putting a newline (\n) between each element
    return "\n".join(lines)


def main() -> None:
    print("------SCRIPT STARTING------")
    # Reads .env into the environment. The Gemini client picks GOOGLE_API_KEY up
    # from there by itself -- the key is never passed around in code, and never
    # printed.
    load_dotenv()

    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    csv_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CSV_PATH

    # MemorySaver keeps checkpoints in this process only, so the history below is
    # available right after the run and gone when the program exits. That is
    # enough to see what the retry cycle did. Surviving a restart -- resuming a
    # crashed run, or inspecting yesterday's -- needs a durable backend
    # (SqliteSaver, one extra package); this stays the smaller step until that is
    # actually wanted.
    graph = build_graph(checkpointer=MemorySaver())

    # A checkpointer files every checkpoint under a thread id, so callers must
    # supply one. A fresh uuid per run keeps runs from being appended to each
    # other; reusing an id is how you would continue an earlier conversation.
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    initial_state = {
        "question": question,
        "csv_path": csv_path,
        "schema": build_schema_summary(csv_path),
    }

    final_state = graph.invoke(initial_state, config=config)

    print(f"\nQuestion:\n  {final_state['question']}")

    # An if/else rather than an early return, so that every path reaches the run
    # history at the bottom -- a declined question has a story worth seeing too.
    if final_state.get("unanswerable"):
        print(f"\nThe question is not answerable because {final_state.get("unanswerable")}")
    else:
        # The generated code is printed on every run, success or failure. The risk
        # this architecture cannot eliminate is code that runs fine and returns a
        # confidently wrong number -- the right average of the wrong column. Seeing
        # what actually ran is the only defence at this stage.
        attempts = final_state.get("attempts", 0)
        retried = "" if attempts <= 1 else f" (last of {attempts} attempts)"
        print(f"\nCode the model wrote{retried}:")
        for line in final_state["code"].splitlines():
            print(f"  {line}")

        # Only the successful path has a result to show. On the give-up path there
        # is nothing here, and `answer` carries the failure report instead.
        if final_state.get("result"):
            print(f"\nRaw result:\n  {final_state['result']}")

        # One field to print either way: `explain` writes a real answer here, and
        # `give_up` writes an honest failure. Deciding which happened is the graph's
        # job, not this function's.
        print(f"\nAnswer:\n  {final_state['answer']}")

    print_history(graph, config)
    print("\n------SCRIPT ENDED------")

if __name__ == "__main__":
    main()