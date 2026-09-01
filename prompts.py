"""Prompt templates, kept away from the node logic that uses them.

These get rewritten far more often than the graph wiring does, and mixing the two
makes both harder to read. Editing anything in this file is a behaviour change to
the system, even though no logic changed -- worth saying out loud when you do it.

They are plain strings with {named} placeholders, filled in with .format(). No
template library, because two strings do not need one.
"""

CODE_PROMPT = """\
You are writing a short pandas snippet to answer a question about a table.

The table is already loaded into a DataFrame called `df`. Do not read any file
and do not import pandas -- both are done for you.

{schema}

Question: {question}
{retry_section}
Rules:
- Put the snippet in `code`. Bare Python only -- no markdown fences, no prose.
- Use print() to output the answer. Code that computes without printing produces
  nothing -- a bare expression is silent outside a notebook.
- Keep it short. The answer itself is usually one or two lines; the row counts
  below are the only reason to write more.
- Only use columns that appear in the schema above.
- If the question cannot be answered from the columns listed, set `can_answer` to
  false and put a short explanation in `reason`, leaving `code` empty. Do not
  invent a column to make the question answerable.
- If your code leaves any rows out -- nulls skipped, values coerced to NaN,
  filtering -- print how many rows the answer covers and how many were left out,
  each on its own line and clearly labelled. Compute those counts in pandas; do
  not estimate them. If nothing was left out, print nothing extra.
"""

# Spliced into CODE_PROMPT only on a retry; on the first attempt that slot is an
# empty string. This feedback is the whole mechanism of the retry cycle -- a
# second attempt that gets the same prompt as the first has no reason to produce
# anything different, so an uninformed retry is just a repeat that costs money.
RETRY_SECTION = """
Your previous attempt failed. Read the error and fix the cause -- do not send the
same code back.

Previous code:
{code}

The error it produced:
{error}
"""

EXPLAIN_PROMPT = """\
Answer the user's question in plain language, using the computed result.

Question: {question}

Code that was run:
{code}

Its output:
{result}

Rules:
- Use the number exactly as given. Do not recompute it, round it, or second-guess
  it -- the code already did the arithmetic.
- If the output reports how many rows were covered or left out, say so plainly.
  If it does not, say nothing about row counts at all -- never estimate, infer
  from the code, or guess a number that is not in the output above.
- One sentence, or two if there are excluded rows to mention. No preamble, no
  restating the code.
"""