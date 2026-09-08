"""Runs model-written pandas code and reports back what happened.

Two executors live here, and which one runs is a security decision, not a
preference.

**Docker (default).** The snippet runs inside a throwaway container with no
network, a read-only filesystem, capped memory and process count, dropped
capabilities, and nothing mounted except the one CSV. This is what makes it
defensible to let someone other than you type a question.

**Subprocess (opt-in, local only).** The original executor. It is NOT A SANDBOX:
the code runs with your own user's permissions and can read your files, reach the
network, and delete things. It buys exactly two things -- a wall-clock timeout,
and isolation of crashes from this interpreter -- and nothing at all against code
that is hostile. It is acceptable only on your own machine, against your own CSV,
with prompts you wrote.

Selecting the unsafe one takes a deliberately embarrassing environment variable,
because the failure mode of a quiet fallback is a public server silently running
strangers' code as you.
"""

import os
import shutil
import subprocess
import sys
from typing import Optional, Tuple

from state import AnalysisState

TIMEOUT_SECONDS = 10

DEFAULT_IMAGE = "agent-sandbox:latest"


def _image() -> str:
    return os.environ.get("SANDBOX_IMAGE", DEFAULT_IMAGE)


def _unsafe_local() -> bool:
    """Is the unsafe local executor explicitly enabled?

    Read at call time, not import time. `load_dotenv()` runs inside `main()` and
    `server.py`, which is *after* this module is imported -- so a module-level
    constant would be fixed before `.env` had been read, and setting the variable
    there would silently do nothing. That is a confusing bug to chase, and a
    dangerous one to get backwards.

    The value is not "1" or "true" on purpose: it should read like a confession
    in whatever file someone puts it in.
    """
    return os.environ.get("UNSAFE_LOCAL_EXECUTOR") == "yes-i-understand"

# The CSV is always mounted at the same path inside the container, so the code
# the model writes never depends on where the file lives on the host.
CONTAINER_CSV = "/data/input.csv"

PREAMBLE = """\
import pandas as pd
df = pd.read_csv({csv_path!r})
"""


def _docker_command(csv_path: str) -> list[str]:
    """Build the `docker run` invocation.

    Every flag here is load-bearing. A flag you cannot justify is a flag someone
    deletes later to make something work.
    """
    return [
        "docker", "run", "--rm", "-i",
        # The single most important flag. Even if generated code somehow reached
        # a secret, it has nowhere to send it. Everything below is defence in
        # depth behind this one line.
        "--network=none",
        # No writes to the image filesystem at all...
        "--read-only",
        # ...except scratch space, which cannot be used to stage an executable.
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        # Turns "the model wrote an accidental 10GB allocation" into a killed
        # container rather than a dead host. memory-swap equal to memory means it
        # cannot escape the cap by swapping.
        "--memory=256m", "--memory-swap=256m",
        "--cpus=0.5",
        # Stops a fork bomb, deliberate or accidental.
        "--pids-limit=64",
        "--security-opt=no-new-privileges",
        "--cap-drop=ALL",
        # nobody:nogroup. Combined with the USER line in the Dockerfile, root is
        # unreachable even if this flag were dropped.
        "--user", "65534:65534",
        # The only thing from the host that exists inside: this one file, read
        # only. `.env` is not merely unreadable -- it is not there.
        "-v", f"{os.path.abspath(csv_path)}:{CONTAINER_CSV}:ro",
        _image(),
        # Source arrives on stdin rather than argv so a long snippet cannot hit
        # the argument length limit, and so quoting is never our problem.
        "python", "-",
    ]


def _run(command: list[str], stdin: str) -> Tuple[Optional[str], Optional[str]]:
    """Run a command, turning every failure into an error string.

    Never raises. A crash in generated code is a normal outcome of this system,
    not an exception in it -- the retry cycle depends on being handed the
    traceback rather than being unwound by it.
    """
    try:
        completed = subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None, (
            f"The code did not finish within {TIMEOUT_SECONDS} seconds and was "
            f"stopped. It may contain an infinite loop."
        )
    except FileNotFoundError:
        # Docker missing. Say so plainly rather than falling back to the unsafe
        # executor, which is how a development shortcut becomes a live incident.
        return None, (
            "The sandbox could not start: `docker` was not found. Install Docker "
            "and build the image with `docker build -t agent-sandbox:latest sandbox/`."
        )

    if completed.returncode != 0:
        # stderr holds the real traceback, handed back verbatim -- a summarised
        # error is useless to the model that has to fix it. A container killed for
        # exceeding its memory cap exits non-zero with little to say, so that case
        # gets a sentence of its own.
        detail = completed.stderr.strip()
        if not detail:
            detail = (
                f"The code was killed (exit {completed.returncode}) without an error "
                f"message. It most likely exceeded the sandbox memory limit."
            )
        return None, detail

    output = completed.stdout.strip()
    if not output:
        # Ran cleanly and printed nothing. Usually the snippet ended in a bare
        # expression instead of print(...), which is silent outside a notebook.
        # Treating this as an error is the difference between a clear complaint
        # and asking `explain` to interpret an empty string.
        return None, "The code ran without errors but printed nothing."

    return output, None


def execute(code: str, csv_path: str) -> Tuple[Optional[str], Optional[str]]:
    """Run `code` against the CSV and return (result, error) -- exactly one set.

    The signature is unchanged from the pre-container version, which is the whole
    point of having had one: the graph, the routers and the retry cycle need no
    edits, because a container killed for exceeding memory looks to them exactly
    like a KeyError did.
    """
    if _unsafe_local():
        # The original executor, kept only for local development without Docker.
        source = PREAMBLE.format(csv_path=csv_path) + code
        return _run([sys.executable, "-"], source)

    source = PREAMBLE.format(csv_path=CONTAINER_CSV) + code
    return _run(_docker_command(csv_path), source)


def sandbox_status() -> dict:
    """What the executor is about to do, for a startup check to report.

    Worth having as its own function: "is this server running generated code in a
    container or not?" should be answerable without reading the environment by
    hand at three in the morning.
    """
    if _unsafe_local():
        return {"mode": "subprocess", "safe": False, "detail": "UNSAFE_LOCAL_EXECUTOR is set"}

    if shutil.which("docker") is None:
        return {"mode": "docker", "safe": False, "detail": "docker not found on PATH"}

    return {"mode": "docker", "safe": True, "detail": f"image {_image()}"}


def run_code(state: AnalysisState) -> dict:
    """Graph node: execute the current snippet, record result or error.

    No guard against the unanswerable case any more -- the router now sends that
    straight to the end, so this node only ever runs when there is code to run.
    """
    result, error = execute(state["code"], state["csv_path"])
    return {"result": result, "error": error}
