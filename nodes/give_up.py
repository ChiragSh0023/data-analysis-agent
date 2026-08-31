"""Terminal node for when the attempt cap runs out.

Two things this node deliberately does not do.

It does not call the model. There is no prompt that turns three failed attempts
into a correct answer, and asking for one spends a request to get a confident
sentence built on nothing. Failure is a fact to report, not a thing to narrate.

It does not summarise or soften the traceback. The last error goes out verbatim,
because the person reading it is the one who has to fix the prompt, the schema,
or the question -- and a tidied-up error is exactly the detail they need gone.
"""

from state import AnalysisState


def give_up(state: AnalysisState) -> dict:
    """Graph node: report the failure honestly and stop.

    Writes into `answer` like the success path does, so the caller has one field
    to print either way. `error` stays set alongside it, which is what lets a
    caller tell a real answer apart from this one without parsing the text.
    """
    if state.get("api_error"):
        return {"answer": _unreachable_report(state)}

    attempts = state.get("attempts", 0)
    error = state.get("error", "no error recorded")

    message = (
        f"I could not answer this. The model wrote code {attempts} times and every "
        f"attempt failed.\n\n  The last error was:\n"
    )
    message += "\n".join(f"    {line}" for line in error.splitlines())

    return {"answer": message}


def _unreachable_report(state: AnalysisState) -> str:
    """Report an API failure, and salvage anything already computed.

    Which of the two model calls failed changes what is worth saying. If
    `write_code` could not be reached, nothing ran and there is nothing to
    report. If `explain` could not be reached, the analysis actually succeeded
    and only the wording is missing -- so the raw output goes out as-is rather
    than being thrown away with the run.
    """
    message = f"I could not reach the model.\n\n  {state['api_error']}"

    if state.get("result"):
        message += (
            "\n\n  The code did run, so here is its raw output, unworded:\n"
        )
        message += "\n".join(f"    {line}" for line in state["result"].splitlines())

    return message
