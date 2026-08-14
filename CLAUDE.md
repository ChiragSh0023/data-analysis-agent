# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Status

**The repo is currently empty** (only `.gitignore` is committed). Everything below is the
*target* design, not a description of existing code. As files get built, update this doc so it
describes reality — and delete this Status section once the skeleton exists.

## What this project is

An agentic data-analysis assistant. The user asks a plain-English question about a CSV
("which region had the biggest drop in Q3?"), and the system answers it by *writing and running
pandas code*, not by reading the data itself.

The loop:

1. Load the CSV and build a small **schema summary** (column names, dtypes, a few sample rows).
2. An LLM (**Gemini Flash**) writes a short pandas snippet that answers the question.
3. The snippet runs in a **sandboxed execution tool**.
4. The system reads the result.
   - Ran cleanly → the LLM turns the raw output into a plain-English answer.
   - Raised an error → the traceback is fed back to the LLM, which **rewrites the code and
     retries**, up to a fixed attempt cap.

Orchestration is **LangGraph**. The retry behavior is a real cycle in the graph, not a `while`
loop buried in a function.

## Working agreement (important)

The author is a **beginner**. This shapes how you should work here, and it is not optional
politeness — it is the main requirement of the project.

- **Small increments.** One concept per step. Build the graph with a single node before adding
  a second. Get a hardcoded pandas snippet running before letting the LLM write one. Never
  deliver five new files at once when three steps of one file each would teach the same thing.
- **Explain every design decision.** When you pick a library, a data shape, a control-flow
  pattern, or a default value, say *why that one* and *what the alternative would have cost*.
  A decision the author can't defend to someone else is a decision that hasn't landed.
- **Explain the code you write**, in prose, at the level of "what this function is for and why
  it exists" — not line-by-line comment noise in the source.
- **Prefer boring and legible over clever.** Explicit dict keys over dynamic attribute magic.
  A named function over a lambda in a graph edge. This codebase is for learning; readability
  outranks concision.
- **Say when something is a real tradeoff** vs. when it's just convention. Beginners can't tell
  those apart yet, and guessing wrong wastes their time.
- **Don't skip ahead.** If a step depends on a concept not yet introduced (checkpointers,
  streaming, structured output), name it as "coming later" rather than quietly using it.

## Architecture

### Why a graph at all

A plain `while` loop could do retries. LangGraph is worth the extra concept here for three
reasons, in order of how much they matter:

1. **The retry is a cycle with state.** Each attempt needs the question, the schema, the last
   code, the last error, and the attempt count. LangGraph makes that state one explicit object
   passed between nodes instead of a pile of local variables.
2. **The control flow is visible.** "Where does the system go after an error?" is answered by
   one conditional-edge function, not by tracing nested `if`s.
3. **It grows without rewriting.** Adding a planning step, human approval, or a checkpointer
   later means adding a node — not restructuring the loop.

### Why generate code instead of feeding the LLM the data

This is the central design decision of the project.

- **Token limits.** A 50k-row CSV does not fit in a prompt. A schema summary does.
- **Arithmetic.** LLMs are unreliable at summing a column. `pandas` is not.
- **Auditability.** The output is a piece of code the author can read and check. "Trust me" is
  not an answer to "how did you get that number?"
- **Cost.** One small prompt per attempt, regardless of file size.

The cost of this choice: the LLM can write *wrong-but-valid* code (right syntax, wrong column,
wrong aggregation). Silent wrong answers are the main risk of this architecture, and the reason
the final answer should always surface the code that produced it.

### State

A single `TypedDict` threaded through every node. Keep it flat and boring:

| Field | Purpose |
| --- | --- |
| `question` | the user's plain-English ask |
| `schema` | column names, dtypes, sample rows — the LLM's view of the data |
| `code` | the pandas snippet from the most recent attempt |
| `result` | stdout / repr of the successful run |
| `error` | traceback from the failed run, or `None` |
| `attempts` | integer, compared against the cap |
| `answer` | final plain-English response |

Design note: `error` and `result` are separate fields rather than one `outcome` field, so the
routing function can branch on a simple `is None` check instead of parsing a payload.

### Nodes

- `write_code` — prompts Gemini Flash with the question + schema, and, on a retry, the previous
  code and its traceback. Returns `code`.
- `run_code` — executes the snippet, captures stdout and exceptions. Returns `result` **or**
  `error`. This node never raises; a crash inside user code is data, not a failure.
- `explain` — turns `result` into a sentence answering the original question.
- `give_up` — terminal node when the attempt cap is hit. Reports the last error honestly rather
  than inventing an answer.

### Edges

`write_code → run_code → route`, where `route` is a conditional edge:

- no `error` → `explain` → `END`
- `error` and `attempts < MAX_ATTEMPTS` → back to `write_code`
- `error` and cap reached → `give_up` → `END`

**The attempt cap is mandatory.** A cyclic graph with an LLM that keeps failing is an infinite
loop that bills real money. Start at `MAX_ATTEMPTS = 3`.

### On the word "sandboxed"

Be honest about this in code and in conversation. `exec()` in the same process is **not a
sandbox** — generated code could read files, hit the network, or delete things. It is only
acceptable here because the code comes from a model the author is prompting, on the author's own
machine, against their own CSV.

Ship it in stages, and say which stage you're in:

1. **Now:** `exec()` with a restricted globals dict, captured stdout, and a wall-clock timeout.
   Cheap, understandable, and enough to learn the agent loop.
2. **Later, if this ever handles untrusted input:** a subprocess with resource limits, or a
   container. This is a real rewrite of `run_code`, not a flag.

Never describe stage 1 as secure.

### Why Gemini Flash

Cheap and fast, which matters a lot when the architecture retries on failure — a self-correcting
loop on an expensive model gets costly fast. Writing a five-line pandas snippet against a known
schema is well within a small model's ability. The model is configured in exactly one place so
swapping it is a one-line change.

## Planned layout

```
main.py            # entry point: CLI question in, answer out
graph.py           # StateGraph definition — nodes, edges, compile
state.py           # the TypedDict
nodes/
  write_code.py
  run_code.py      # the execution tool
  explain.py
prompts.py         # prompt templates, kept out of node logic
data/              # sample CSVs to test against
```

Prompts live in their own module because they will be edited far more often than the graph
wiring, and mixing the two makes both harder to read.

## Build order

Each step should run end-to-end before starting the next one.

1. Load a CSV, print the schema summary. No LLM, no graph.
2. `run_code` alone: hand it a hardcoded pandas string, get output back. Prove the tool works.
3. A one-node LangGraph that just echoes state. Learn the graph API on something trivial.
4. `write_code` with the real LLM → `run_code`. Straight line, no retries. Accept that it breaks.
5. Add the conditional edge and the retry cycle. This is the point where it becomes agentic.
6. Add `give_up`, the attempt cap, and the `explain` node.

## Setup

System `python3` is **3.9.6**, which is too old for current LangGraph. Use the 3.13 interpreter
that's already installed:

```bash
/opt/anaconda3/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install langgraph langchain-google-genai pandas python-dotenv
```

The API key goes in `.env` as `GOOGLE_API_KEY`. `.env` is already gitignored — **never commit a
key, and never print one in logs or error output.**

Run with:

```bash
python main.py "which region had the biggest drop in Q3?"
```

## Conventions

- Every node takes the state dict and returns a **partial** dict of only the fields it changed.
  LangGraph merges it. Don't return the whole state — it hides what a node actually does.
- Nodes hold no module-level mutable state. Everything flows through the state object.
- The final answer always shows the code that produced it. See the "wrong-but-valid" risk above.
- Prompt changes are behavior changes. Mention them explicitly rather than folding them into an
  unrelated edit.
