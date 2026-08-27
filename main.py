"""Entry point: one hardcoded question in, one plain-English answer out.

Run it with:

    .venv/bin/python main.py
"""

import sys

import pandas as pd
from dotenv import load_dotenv

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
DEFAULT_CSV_PATH = "data/sales.csv"
DEFAULT_QUESTION = "What is the average sales?"

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
    # joins every string in the lines list into one big string, putting a newline (\n) between each element
    return "\n".join(lines)


def main() -> None:
    # Reads .env into the environment. The Gemini client picks GOOGLE_API_KEY up
    # from there by itself -- the key is never passed around in code, and never
    # printed.
    load_dotenv()

    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    csv_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CSV_PATH

    graph = build_graph()

    initial_state = {
        "question": question,
        "csv_path": csv_path,
        "schema": build_schema_summary(csv_path),
    }

    final_state = graph.invoke(initial_state)

    print(f"\nQuestion:\n  {final_state['question']}")

    if final_state.get("unanswerable"):
        print(f"\nThe question is not answerable because {final_state.get("unanswerable")}\n")
        return

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
    print(f"\nAnswer:\n  {final_state['answer']}\n")


if __name__ == "__main__":
    main()