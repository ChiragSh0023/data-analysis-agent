"""HTTP layer: upload a CSV, ask a question about it, get the run back as JSON.

This file holds no analysis logic at all. It validates input, resolves an upload
id to a path on disk, and calls `runner.run_analysis`. Everything it knows about
the agent is that one function.

Run it with:

    .venv/bin/uvicorn server:app --reload
"""

import json
import re
import time
import uuid
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nodes.run_code import sandbox_status
from runner import build_schema_summary, describe_columns, run_analysis, stream_analysis

load_dotenv()

# Uploads live outside the project tree's importable code on purpose: nothing
# here should ever be reachable as a module, and keeping them in one directory
# makes the retention sweep in a later step a single glob.
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# An upload id is a uuid4 hex string and nothing else. This is the only thing
# standing between a request and an arbitrary path on the filesystem: without it
# a csv_id of "../.env" would resolve to a real, very interesting file.
CSV_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
CHUNK_BYTES = 64 * 1024

# Uploads are someone else's data sitting on your disk. Keeping them forever is a
# liability with no upside, so they are swept at startup.
RETENTION_SECONDS = 6 * 60 * 60

app = FastAPI(title="Data analysis agent")


@app.on_event("startup")
def sweep_old_uploads() -> None:
    """Delete uploads past their retention age."""
    cutoff = time.time() - RETENTION_SECONDS
    for path in UPLOAD_DIR.glob("*.csv"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


@app.on_event("startup")
def announce_sandbox() -> None:
    """Say out loud how generated code is going to be executed.

    Printed at every boot because the answer changes with an environment
    variable, and "is this server containerising the code it runs?" is not a
    question anyone should have to answer by reading source at 3am.
    """
    status = sandbox_status()

    if status["safe"]:
        print(f"[SANDBOX OK] executor={status['mode']} ({status['detail']})")
        return

    # Two different unsafe states, and conflating them would be its own small
    # lie. A missing Docker refuses to run anything; the opt-in actually runs
    # generated code with this process's own permissions.
    if status["mode"] == "subprocess":
        print(f"[!! UNSAFE ] executor=subprocess ({status['detail']})")
        print(
            "[!! UNSAFE ] generated code runs with THIS user's permissions and is "
            "not isolated. Fine on your own machine with your own CSVs; never on a "
            "host anyone else can reach."
        )
    else:
        print(f"[ BLOCKED  ] executor=docker ({status['detail']})")
        print(
            "[ BLOCKED  ] no code will run until Docker is available: "
            "docker build -t agent-sandbox:latest sandbox/"
        )


class AskRequest(BaseModel):
    # A length cap because an unbounded question is an unbounded prompt, and the
    # prompt is what gets billed.
    question: str = Field(min_length=1, max_length=500)
    csv_id: str


def resolve_csv(csv_id: str) -> Path:
    """Turn an upload id into a path, or refuse.

    Two separate checks, and both are needed. The pattern match rejects anything
    that isn't a bare uuid hex string, which is what stops path traversal. The
    `exists` check then distinguishes "you sent nonsense" from "that upload is
    gone", which are different problems for the person on the other end.
    """
    if not CSV_ID_PATTERN.match(csv_id):
        raise HTTPException(status_code=400, detail="Malformed csv_id.")

    path = UPLOAD_DIR / f"{csv_id}.csv"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That upload no longer exists.")

    return path


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    """Accept one CSV and return an id for asking questions about it.

    The client's filename is used for exactly one thing: checking the extension
    and showing a label back to the user. It never touches the filesystem. A
    generated uuid is the real name, because a browser is free to send
    `../../.env` as a filename and some upload code will happily honour it.

    The size cap is enforced while streaming rather than from `Content-Length`,
    because a client sets that header and can simply lie. Counting bytes as they
    arrive is the only number that is actually true.
    """
    label = (file.filename or "").strip()
    if not label.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted.")

    csv_id = uuid.uuid4().hex
    path = UPLOAD_DIR / f"{csv_id}.csv"

    written = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                    )
                out.write(chunk)

        # Parse before accepting. A file pandas cannot read is better refused here
        # with a clear message than three steps later as a traceback from inside a
        # container, where nothing points back at the upload.
        try:
            pd.read_csv(path)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"That file could not be read as CSV: {exc}"
            ) from exc
    except Exception:
        # Never leave a partial or unreadable file behind for `resolve_csv` to find.
        path.unlink(missing_ok=True)
        raise

    return {
        "csv_id": csv_id,
        "label": Path(label).name,
        "columns": describe_columns(str(path)),
    }


@app.post("/api/ask")
def ask(request: AskRequest) -> dict:
    """Run one analysis against one previously uploaded CSV."""
    csv_path = resolve_csv(request.csv_id)
    return run_analysis(request.question, str(csv_path))


@app.post("/api/ask/stream")
def ask_stream(request: AskRequest) -> StreamingResponse:
    """Same analysis, but reporting each step as it happens.

    Newline-delimited JSON rather than server-sent events, because NDJSON needs
    no framing rules and no client library -- one JSON object per line, read with
    a plain fetch reader.

    POST rather than GET, even though EventSource would have been simpler on the
    client. Running an analysis is not idempotent and costs money on every call,
    and a GET is exactly the sort of thing a browser or proxy will happily
    prefetch on your behalf.
    """
    csv_path = resolve_csv(request.csv_id)

    def events():
        for event in stream_analysis(request.question, str(csv_path)):
            yield json.dumps(event) + "\n"

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        # Without this an intermediary can sit on the response until it is
        # complete, which would buffer away the entire point of streaming.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-store"},
    )


@app.get("/api/schema/{csv_id}")
def schema(csv_id: str) -> dict:
    """The columns of an upload, for the page to show before a question is asked."""
    csv_path = resolve_csv(csv_id)
    return {
        "columns": describe_columns(str(csv_path)),
        "summary": build_schema_summary(str(csv_path)),
    }


# Mounted last so it never shadows the /api routes above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
