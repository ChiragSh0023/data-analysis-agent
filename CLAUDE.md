# CLAUDE.md

Guidance for Claude Code when working in this repository.

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
| `csv_path` | which file to load — `run_code` needs it to build the preamble |
| `schema` | column names, dtypes, sample rows — the LLM's view of the data |
| `code` | the pandas snippet from the most recent attempt |
| `result` | stdout of the successful run |
| `error` | traceback from the failed run, or `None` |
| `unanswerable` | why the question can't be answered from these columns, or absent |
| `api_error` | the model could not be reached — quota, outage, network |
| `answer` | final plain-English response |
| `attempts` | how many times `write_code` has run, compared against the cap |

Design note: `error` and `result` are separate fields rather than one `outcome` field, so the
routing function can branch on a simple `is None` check instead of parsing a payload.

`api_error` is separate from `error` for a reason that costs real money if you get it wrong.
`error` means *the generated code crashed*, which is worth retrying. `api_error` means *the model
was unreachable*, which is not — folding them together would spend all three attempts hammering a
quota wall that will not move for another day.

`csv_path` is threaded through the state rather than kept as a module constant, so that `run_code`
holds no module-level configuration of its own — consistent with the convention below.

### Nodes

- `write_code` — prompts Gemini Flash with the question + schema, and, on a retry, the previous
  code and its traceback. Returns `code` and an incremented `attempts` — or `unanswerable` if the
  model replies `CANNOT_ANSWER: <reason>`, which is how it declines a question the columns can't
  support instead of inventing a plausible wrong answer.
- `run_code` — executes the snippet, captures stdout and exceptions. Returns `result` **or**
  `error`. This node never raises; a crash inside user code is data, not a failure.

Neither LLM node raises either. Both call `llm.invoke_model`, which returns `(text, api_error)` so
an unreachable model becomes something the graph can route on. Before that existed, a transient 503
or a 429 unwound the whole run with a hundred lines of library traceback — the node running
*untrusted generated code* was being more careful than the ones making ordinary network calls.
`invoke_model` adds no retry of its own: the google-genai client already retries internally, and a
second layer would turn a 503 into minutes of silent waiting while doing nothing for a 429.
- `explain` — turns `result` into a sentence answering the original question.
- `give_up` — terminal node when the attempt cap is hit. Writes an honest failure report into
  `answer`, so callers have one field to print either way, and leaves `error` set so they can still
  tell the two apart without parsing text. Makes **no LLM call**: there is no prompt that turns
  three failed attempts into a right answer, and asking for one buys a confident sentence built on
  nothing.

### Edges

Two conditional edges, not one, because they answer questions asked at different moments in the
flow. Both routers live in `graph.py`.

`route_after_write` — did the model produce code, or refuse the question?

- `unanswerable` set → `END` (skip `run_code` entirely; there is no code to run)
- otherwise → `run_code`

`route_after_run` — did the snippet work, and if not, is another attempt allowed?

- no `error` → `explain`
- `error` and `attempts < MAX_ATTEMPTS` → back to `write_code` — **the cycle**
- `error` and cap reached → `give_up` → `END`

`route_after_explain` — did the wording call reach the model?

- no `api_error` → `END`
- `api_error` → `give_up` → `END`

`explain` was terminal until API failures were handled. It needs a router only because it can fail
for a reason unrelated to the analysis: the number is computed and correct, and the model simply
could not be reached to put it in a sentence. `give_up` prints the raw output in that case rather
than discarding a run that had already done its work.

Both `write_code` and `explain` can set `api_error`, and both route to `give_up`, which is the
single exit for every failure. That is why `main.py` can print `answer` without knowing which of
the four ways to fail actually happened.

Each router returns a *decision* string (`"retry"`, `"exhausted"`) which `add_conditional_edges`
maps to a node through an explicit dict. Keeping the decision and the destination apart means a
node can be renamed without touching routing logic, and every possible destination is visible in
one place instead of buried in return statements.

**The attempt cap is mandatory.** A cyclic graph with an LLM that keeps failing is an infinite
loop that bills real money. Currently `MAX_ATTEMPTS = 3` in `graph.py`.

The cap shipped with the cycle rather than waiting for step 6 as originally planned. Landing a
retry loop with no bound, even for one commit, is not a thing to do.

### On the word "sandboxed"

Be honest about this in code and in conversation. What `run_code` does is **not a sandbox** —
generated code could read files, hit the network, or delete things. It is only acceptable here
because the code comes from a model the author is prompting, on the author's own machine, against
their own CSV.

Ship it in stages, and say which stage you're in:

1. **Now (built):** `subprocess.run([sys.executable, "-c", source], capture_output=True,
   timeout=10)`. A separate process buys exactly two things — a wall-clock timeout that can
   actually kill a hung snippet, and isolation of crashes from this program's interpreter. It buys
   nothing against hostile code, which runs with full user permissions either way.
2. **Later, if this ever handles untrusted input:** OS-level resource limits, a locked-down user,
   or a container. This is a real rewrite of `run_code`, not a flag.

Never describe stage 1 as secure.

(An earlier draft of this doc specified in-process `exec()` with a restricted globals dict for
stage 1. Subprocess was chosen instead: a restricted globals dict is trivially escapable and, more
importantly, `exec()` has no way to stop an infinite loop.)

### Why Gemini Flash

Cheap and fast, which matters a lot when the architecture retries on failure — a self-correcting
loop on an expensive model gets costly fast. Writing a five-line pandas snippet against a known
schema is well within a small model's ability. The model is configured in exactly one place —
`MODEL_NAME` in `llm.py` — so swapping it is a one-line change.

Currently pinned to **`gemini-3.7-flash`**, not the floating `gemini-flash-latest` alias: an alias
would change the model under you, so a prompt that worked yesterday could behave differently today
with nothing in the code to explain it.

Two failure modes seen on this API, worth telling apart before you start debugging your own code:

- **Retired model.** `gemini-2.5-flash` still appears in `models.list()` but returns 404 on a new
  key. A clean, immediate error.
- **Very slow model.** `gemini-3.7-flash` has gone through spells of taking **2–4 minutes** for a
  trivial prompt, with no error and nothing in the logs. It looks exactly like a hang and isn't —
  the call does return. Measured: 127s and 262s for a prompt whose whole content was "reply with
  exactly: ok".

That last one is worth planning around, because the retry cycle multiplies it: a question that
exhausts all three attempts plus `explain` is four sequential calls, so 8–17 minutes for a single
failing question at those speeds. Two consequences:

- Debug routing with stubs, never live calls. This is the same conclusion the quota limit forces,
  for an unrelated reason.
- If calls crawl, time one against another Flash variant before suspecting your own code. If the
  other answers in seconds, the problem was never yours.

If you do switch models temporarily, check whether the replacement honours `temperature`. Some
Flash variants use **fixed sampling defaults** and ignore it silently with only a `UserWarning` —
which would leave `get_llm` looking like it controls sampling while doing nothing. `3.7-flash`
does honour it (verified: no warning at either 0.0 or 0.3).

Note that `gemini-2.5-flash` is retired — the API still lists it, but calling it on a new key
returns 404. When a model ID starts failing, list the live ones:

```python
from google import genai
genai.Client(api_key=...).models.list()
```

## Layout

```
main.py            # entry point: schema summary, seeds state, invokes graph, prints result
graph.py           # StateGraph definition — nodes, edges, compile
state.py           # the TypedDict
llm.py             # the single place the model is configured
prompts.py         # prompt templates, kept out of node logic
nodes/
  write_code.py
  run_code.py      # the execution tool
  explain.py
  give_up.py       # terminal node for the exhausted branch
data/
  sales.csv        # clean sample: region, quarter, sales, units — 12 rows
  messy_sales.csv  # deliberately dirty sample — see below
```

Prompts live in their own module because they will be edited far more often than the graph
wiring, and mixing the two makes both harder to read. `llm.py` exists because two nodes need a
model, and building one in each would make a model swap a two-file edit whose halves can silently
drift apart.

## Build order

Each step should run end-to-end before starting the next one.

1. ~~Load a CSV, print the schema summary. No LLM, no graph.~~ **Done.**
2. ~~`run_code` alone: hand it a hardcoded pandas string, get output back.~~ **Done** — all four
   paths exercised: success, `KeyError`, silent-but-clean, and the 10s timeout kill.
3. ~~A one-node LangGraph that just echoes state.~~ **Done** with three stub nodes.
4. ~~`write_code` with the real LLM → `run_code`.~~ **Done**, and `explain` came along with it
   rather than waiting — the slice was specified as producing a sentence, not a number.
5. ~~Add the conditional edge and the retry cycle.~~ **Done** — two routers, the cycle, `attempts`,
   and `MAX_ATTEMPTS`. The attempt cap shipped here rather than in step 6: landing a retry loop
   with no bound, even for one commit, is not a thing to do. The guard clauses that had piled up in
   `run_code` and `explain` were deleted, not left behind — a check that can never fire still costs
   the next reader their time.
6. ~~`give_up` as a real terminal node.~~ **Done.** Reporting moved out of `main.py` and into the
   node, which made `main.py` shorter rather than longer: it now prints `answer` on every path that
   produces one, and no longer branches on `error` at all.

**The skeleton is complete.** Every node, edge, and outcome in this document exists and is
exercised. Candidates for what comes next, none of them started and none urgent:

- Take the question from `sys.argv` instead of a module constant. Two lines, deferred only because
  the loop had to be trustworthy first — it now is.
- A bigger, messier CSV. Every failure mode so far was provoked by hand on 12 clean rows; nulls,
  mixed dtypes, and a date column that isn't a date are where wrong-but-valid code actually lives.
- Structured output for `write_code`, replacing the `CANNOT_ANSWER:` string protocol and the fence
  stripping with a typed reply the model cannot get wrong.
- A checkpointer, so a run can be resumed or inspected after the fact.

### Verifying the cycle without spending quota

The retry paths are awkward to test live: you need code that reliably fails, and the free tier is
small (see Setup). Stub **`llm.get_llm`** with a fake object whose `.invoke()` returns something
carrying a `.text` attribute, and replace `nodes.run_code.execute` with a function that fails on
demand. That exercises the real graph and the real routers with no network at all, and it turns
"does the retry prompt actually contain the traceback?" into something you can assert rather than
hope for.

**Patch `llm.get_llm`, not `nodes.write_code.get_llm`.** The nodes no longer import `get_llm` —
they call `llm.invoke_model`, which looks `get_llm` up in its own module at call time. Patching the
old names still "succeeds": it sets an unused attribute on the node module, the real client is
built anyway, and your offline test quietly makes live calls against your quota. One patch point
now covers both nodes.

Since the same fake serves `write_code` and `explain`, have it branch on the prompt (`"Answer the
user's question" in prompt`) if you care which one is being exercised. Otherwise `explain`
cheerfully "answers" with a snippet of pandas and a passing test proves nothing.

## Setup

System `python3` is **3.9.6**, which is too old for current LangGraph. Use the 3.13 interpreter
that's already installed:

```bash
/opt/anaconda3/bin/python3.13 -m venv .venv
source .venv/bin/activate
pip install langgraph langchain-google-genai pandas python-dotenv
```

Installed versions as built: `langgraph` 1.2.11, `langchain-google-genai` 4.3.4, `pandas` 3.0.5,
`python-dotenv` 1.2.2. Don't install `langchain-core` explicitly — it arrives as a dependency, and
pinning it yourself risks a version that fights the one LangChain wants.

The API key goes in `.env` as `GOOGLE_API_KEY`. `.env` is already gitignored — **never commit a
key, and never print one in logs or error output.**

### Quota

The free tier allows **20 requests per day, per model** (`generate_content_free_tier_requests`).
This bites harder than it looks, because the retry cycle multiplies calls: one question costs up to
`MAX_ATTEMPTS` calls to `write_code` plus one to `explain` — four requests for a single failing
question, so roughly five bad questions exhausts a day.

Consequences worth planning around:

- Test routing logic with stubs, not live calls (see the build-order note above).
- The quota is per model, so switching `MODEL_NAME` to another Flash variant gets a fresh bucket.
- **A new API key in the same project does not help.** The bucket is
  `GenerateRequestsPerDayPerProjectPerModel` — per *project* per model, so a reissued key inherits
  the same exhausted counter. Verified twice over: with a fresh key in the old project,
  `gemini-3.5-flash` answered while `gemini-3.6-flash` still 429'd in the same minute; a key from a
  newly created project then worked immediately on `gemini-3.6-flash`. Fresh quota needs a new
  project, a different model, or billing.
- A 429 with `RESOURCE_EXHAUSTED` means the daily cap, not a rate spike — waiting a few seconds
  won't help, despite what the message's `retryDelay` suggests.
- Transient `503 UNAVAILABLE` is a different thing entirely: that one really is temporary.

Run with:

```bash
.venv/bin/python main.py                                            # defaults
.venv/bin/python main.py "which region had the biggest drop in Q3?"
.venv/bin/python main.py "what is the average sales?" data/messy_sales.csv
```

Two optional positional arguments, question then CSV path, read straight from `sys.argv`. No
`argparse`: two positionals with no flags don't need a parser. Add one when the first `--flag`
shows up, not before.

## The messy CSV

`data/messy_sales.csv` exists to break things on purpose. Every failure mode found before it was
provoked by hand against 12 clean rows, which proves nothing about real data. Each column is dirty
in a *different* way, so one file exercises several failure classes without being uniformly
unusable:

| Column | What's wrong | What it should provoke |
| --- | --- | --- |
| `units` | contains `N/A` and `unknown` | pandas reads the column as `str`; `.mean()` raises `TypeError` — a **loud** failure that should trigger the retry cycle |
| `region` | 11 spellings of 4 regions (`north`, `NORTH`, `" North "`, `"East "`) | a naive `groupby` silently returns 11 groups — **wrong-but-valid**, the risk this architecture cannot eliminate |
| `sales` | 2 empty cells | stays `float64`; `.mean()` silently skips the nulls, so the answer is right but the denominator isn't what you'd assume |
| `order_date` | `2024-01-15`, `15/03/2024`, `"March 2, 2024"`, `not recorded`, empty | any date arithmetic needs `pd.to_datetime(..., errors="coerce")` and a decision about what to do with `NaT` |

The `region` row was expected to be the dangerous one. A loud failure is cheap — the retry loop
handles it, and you see the traceback either way. A silent wrong answer looks exactly like a right
one, and the only defence is that `main.py` always prints the code.

### What the messy CSV actually showed

Two live questions, both answered correctly, and neither failure mode landed where predicted.

**The retry cycle works against a real model.** "What is the average number of units sold?" took
two attempts: the first hit `TypeError: Cannot perform reduction 'mean' with string dtype`, and
given that traceback the model rewrote it as
`pd.to_numeric(df['units'], errors='coerce').mean()`. This is the first time the cycle has been
verified end-to-end without a stubbed failure.

**The `region` trap did not catch it.** Asked for totals per region, the model wrote
`df['region'].str.strip().str.title()` before grouping — unprompted, first attempt, four regions.
Nothing in the prompt mentions normalisation; three sample rows showing `North` and `north` were
apparently enough.

**The real residual risk is narrower than "wrong answer", and neither retry nor a cap touches it.**
Both answers silently dropped rows and read as though they hadn't:

- the units average covers 17 of 20 rows (3 coerced to `NaN`)
- the regional totals exclude 2 null-sales rows, one in East and one in West

Every number is arithmetically right. What's missing is the population it was computed over, and
the plain-English sentence is where that context disappears — `explain` is handed a bare number
with no idea what was discarded upstream. Printing the code is the only reason this is visible at
all.

### Fixing it: why the counts come from pandas, not from `explain`

The tempting fix is to tell `EXPLAIN_PROMPT` to state what was excluded. That is the wrong node.
`explain` receives only the question, the code, and the printed output — it has no idea how many
rows were dropped. Asking it to report a number it cannot see is an invitation to invent a
plausible one, which trades a silent omission for a confident fabrication.

So the counts are gathered where the arithmetic already happens. Two prompt edits, working as a
pair:

- `CODE_PROMPT` gains a rule: if the snippet leaves rows out, print how many it covered and how
  many it dropped, computed in pandas, each clearly labelled. If nothing was dropped, print
  nothing extra.
- `EXPLAIN_PROMPT` gains the matching rule: report those counts if they appear in the output, and
  say nothing at all about row counts if they don't — never estimate, never infer from the code.

This is the project's central design decision applied one level down: pandas produces the facts,
the model only words them. Note the failure mode this deliberately accepts — the model might not
*notice* that its code drops rows, in which case nothing is printed and the answer is silently
incomplete exactly as before. The fix narrows the gap; it does not close it.

Both edits are **behaviour changes**, made together and on purpose.

**Verified live, all three cases, first attempt each:**

| Question | Disclosed | Independently checked |
| --- | --- | --- |
| average units (messy) | covered 17, left out 3 | 17 / 3 ✓ |
| totals per region (messy) | covered 18, left out 2 | 18 / 2 ✓ |
| average sales (clean) | *nothing extra* | nothing was dropped ✓ |

The third row matters as much as the first two: a fix that makes the model bolt a caveat onto every
answer would be its own kind of noise. It stayed quiet when there was nothing to declare.

Side effect worth watching: generated snippets are now 4–6 lines instead of 1–2, which sits in
tension with the "keep it short, one or two lines" rule in the same prompt. Nothing has broken, but
if code quality degrades, those two rules are the first place to look.

## Conventions

- Every node takes the state dict and returns a **partial** dict of only the fields it changed.
  LangGraph merges it. Don't return the whole state — it hides what a node actually does.
- Nodes hold no module-level mutable state. Everything flows through the state object.
- The final answer always shows the code that produced it. See the "wrong-but-valid" risk above.
- Prompt changes are behavior changes. Mention them explicitly rather than folding them into an
  unrelated edit.
- Read model replies with `response.text`, not `response.content`. In langchain-core 1.x `.content`
  is a list of typed blocks; `.text` flattens it to a plain string. Calling a string method on
  `.content` raises `AttributeError: 'list' object has no attribute ...`.
