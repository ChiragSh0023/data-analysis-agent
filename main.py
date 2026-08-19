"""Entry point: one hardcoded question in, one plain-English answer out.

Run it with:

    .venv/bin/python main.py
"""

import pandas as pd
from dotenv import load_dotenv

from graph import build_graph

CSV_PATH = "data/sales.csv"

# Hardcoded on purpose for this first slice. Promoting it to a command-line
# argument is a two-line change once the loop itself is trustworthy.
# QUESTION = "What is the average sales?"
QUESTION = "What is the average discount?"

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

    graph = build_graph()

    initial_state = {
        "question": QUESTION,
        "csv_path": CSV_PATH,
        "schema": build_schema_summary(CSV_PATH),
    }

    final_state = graph.invoke(initial_state)

    print(f"\nQuestion:\n  {final_state['question']}")

    if final_state.get("unanswerable"):
        print(f"The question is not answerable because {final_state.get("unanswerable")}")
        return

    # The generated code is printed on every run, success or failure. The risk
    # this architecture cannot eliminate is code that runs fine and returns a
    # confidently wrong number -- the right average of the wrong column. Seeing
    # what actually ran is the only defence at this stage.
    print("\nCode the model wrote:")
    for line in final_state["code"].splitlines():
        print(f"  {line}")
    
    if final_state.get("error"):
        print("\nThe code failed:")
        for line in final_state["error"].splitlines():
            print(f"  {line}")
        return

    print(f"\nRaw result:\n  {final_state['result']}")
    print(f"\nAnswer:\n  {final_state['answer']}\n")


if __name__ == "__main__":
    main()