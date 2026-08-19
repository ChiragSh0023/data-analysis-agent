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

Rules:
- Reply with Python code only. No explanation, no markdown fences, no ```python.
- Use print() to output the answer. Code that computes without printing produces
  nothing.
- Keep it short. One or two lines is usually enough.
- Only use columns that appear in the schema above.
- if the question cannot be answered from the columns listed, reply with exactly CANNOT_ANSWER: <short reason>
"""

EXPLAIN_PROMPT = """\
Answer the user's question in one plain sentence, using the computed result.

Question: {question}

Code that was run:
{code}

Its output:
{result}

Rules:
- Use the number exactly as given. Do not recompute it, round it, or second-guess
  it -- the code already did the arithmetic.
- One sentence. No preamble, no restating the code.
"""