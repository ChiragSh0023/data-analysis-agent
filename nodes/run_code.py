"""Runs model-written pandas code and reports back what happened.

NOT A SANDBOX. The generated code runs in a separate process, but that process
has the same user permissions this program does -- it can read your files, reach
the network, and delete things. A subprocess buys two real protections and no
more: a wall-clock timeout that can actually kill a hung snippet, and isolation
of crashes from this program's own interpreter. It buys nothing against code
that is deliberately hostile.

That is acceptable here only because the code comes from a model you are
prompting, on your own machine, against your own CSV. Pointing this at untrusted
input means rewriting this file around a container or an OS-level resource jail.
"""

import subprocess
import sys
from typing import Optional, Tuple

from state import AnalysisState

TIMEOUT_SECONDS = 10

# This part is prepended in the start of every code that runs
PREAMBLE = """\
import pandas as pd
df = pd.read_csv({csv_path!r})
"""


def execute(code: str, csv_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Run `code` against the CSV and return (result, error) -- exactly one set.

    This function does not raise. Every way the snippet can fail is caught and
    turned into an error *string*, because a crash in generated code is a normal
    outcome of this system, not an exception in it. The retry loop that arrives
    in a later step depends on that: it needs to read the traceback and feed it
    back to the model, which it cannot do if the traceback unwound this process.
    """
    source = PREAMBLE.format(csv_path=csv_path) + code

    try:
        # sys.executable, not "python": this must be the same interpreter
        # running us, or the child won't have pandas installed. Resolving
        # "python" through PATH would eventually find the wrong one.
        completed = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, (
            f"The code did not finish within {TIMEOUT_SECONDS} seconds and was stopped. It may contain an infinite loop."
        )

    # Error -> return (None, error)
    if completed.returncode != 0:
        # stderr holds the real traceback. Handing it back verbatim is the
        # point -- a summarised error is useless to the model that has to fix it.
        return None, completed.stderr.strip()

    output = completed.stdout.strip()
    # Output empty
    if not output:
        # Ran cleanly and printed nothing. Usually the snippet ended in a bare
        # expression instead of print(...), which is silent outside a notebook.
        # Treating this as an error is the difference between a clear complaint
        # and asking the explain step to interpret an empty string.
        return None, "The code ran without errors but printed nothing."

    # Output non-empty
    return output, None


def run_code(state: AnalysisState) -> dict:
    """Graph node: execute the current snippet, record result or error."""
    result, error = execute(state["code"], state["csv_path"])
    return {"result": result, "error": error}