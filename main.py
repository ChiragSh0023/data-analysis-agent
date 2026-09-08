"""Command-line entry point: a question in, a plain-English answer out.

Run it with:

    .venv/bin/python main.py
    .venv/bin/python main.py "which region had the biggest drop in Q3?"
    .venv/bin/python main.py "what is the average sales?" data/messy_sales.csv

All this file does now is read two arguments, call `runner.run_analysis`, and
format the result for a terminal. The analysis itself lives in `runner.py` so
that the web layer can call it without inheriting any of this printing.
"""

import sys

from dotenv import load_dotenv

from runner import run_analysis

# Read straight from sys.argv rather than through argparse. Two positional
# arguments with no flags do not need a parser, and argparse would add a screen
# of setup to save nothing. If this ever grows options (--model, --max-attempts),
# switch then -- that is the point where hand-rolling starts costing more than it
# saves.
DEFAULT_CSV_PATH = "data/messy_sales.csv"
DEFAULT_QUESTION = "what is the average units sold?"


def main() -> None:
    print("------SCRIPT STARTING------")
    # Reads .env into the environment. The Gemini client picks GOOGLE_API_KEY up
    # from there by itself -- the key is never passed around in code, and never
    # printed.
    load_dotenv()

    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    csv_path = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CSV_PATH

    report = run_analysis(question, csv_path)

    print(f"\nQuestion:\n  {report['question']}")

    # An if/else rather than an early return, so that every path reaches the run
    # history at the bottom -- a declined question has a story worth seeing too.
    if report["unanswerable"]:
        print(f"\nThe question is not answerable because {report['unanswerable']}")
    else:
        # The generated code is printed on every run, success or failure. The risk
        # this architecture cannot eliminate is code that runs fine and returns a
        # confidently wrong number -- the right average of the wrong column. Seeing
        # what actually ran is the only defence at this stage.
        attempts = report["attempts"]
        retried = "" if attempts <= 1 else f" (last of {attempts} attempts)"
        print(f"\nCode the model wrote{retried}:")
        for line in (report["code"] or "").splitlines():
            print(f"  {line}")

        # Only the successful path has a result to show. On the give-up path there
        # is nothing here, and `answer` carries the failure report instead.
        if report["result"]:
            print(f"\nRaw result:\n  {report['result']}")

        # One field to print either way: `explain` writes a real answer here, and
        # `give_up` writes an honest failure. Deciding which happened is the graph's
        # job, not this function's.
        print(f"\nAnswer:\n  {report['answer']}")

    print("\nWhat happened, step by step:")
    for entry in report["history"]:
        print(f"  {entry['step']}. {entry['node']:<11} {entry['detail']}")

    print("\n------SCRIPT ENDED------")


if __name__ == "__main__":
    main()
