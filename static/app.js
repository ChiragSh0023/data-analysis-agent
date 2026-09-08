// Page behaviour: upload, ask, render. No analysis logic lives here -- the
// server returns a finished report and this file decides how it looks.

const $ = (id) => document.getElementById(id);

let csvId = null;
let timer = null;

/* ---------- status line ---------- */

/** Show elapsed seconds while waiting.
 *
 * Not decoration. This model regularly takes two to four minutes on a single
 * question, and a page that sits silent that long is indistinguishable from one
 * that has crashed. A ticking number is the whole difference between "working"
 * and "broken".
 */
function startTimer(label) {
  const began = Date.now();
  clearInterval(timer);
  const tick = () => {
    const secs = Math.floor((Date.now() - began) / 1000);
    $("status").textContent = `${label} ${secs}s`;
  };
  tick();
  timer = setInterval(tick, 1000);
  $("status").className = "hint working";
}

/* ---------- live progress ---------- */

let stepTimer = null;

function clearProgress() {
  clearInterval(stepTimer);
  stepTimer = null;
  $("progress").innerHTML = "";
}

/** Append a step and start counting seconds against it.
 *
 * Each step keeps its own elapsed time rather than sharing one clock, because
 * "which part is slow" is the question you actually have while waiting -- and on
 * this model it is almost always the two LLM calls, not the pandas.
 */
function addStep(text) {
  finishStep();

  const li = document.createElement("li");
  li.className = "current";

  const label = document.createElement("span");
  label.className = "what";
  label.textContent = text;

  const clock = document.createElement("span");
  clock.className = "elapsed";
  clock.textContent = "0s";

  li.append(label, clock);
  $("progress").append(li);

  const began = Date.now();
  clearInterval(stepTimer);
  stepTimer = setInterval(() => {
    clock.textContent = `${Math.floor((Date.now() - began) / 1000)}s`;
  }, 1000);
}

/** Mark the running step as finished, freezing its elapsed time. */
function finishStep() {
  clearInterval(stepTimer);
  stepTimer = null;
  const current = $("progress").querySelector("li.current");
  if (current) current.className = "done";
}

function stopTimer(message, bad = false) {
  clearInterval(timer);
  timer = null;
  $("status").textContent = message;
  $("status").className = bad ? "hint bad" : "hint";
}

/** FastAPI puts a readable reason in `detail`; show that, not a bare number. */
async function errorMessage(response) {
  try {
    const body = await response.json();
    return body.detail || `Request failed (${response.status}).`;
  } catch {
    return `Request failed (${response.status}).`;
  }
}

/* ---------- upload ---------- */

const drop = $("drop");

drop.addEventListener("click", () => $("file").click());
drop.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    $("file").click();
  }
});

["dragenter", "dragover"].forEach((evt) =>
  drop.addEventListener(evt, (e) => {
    e.preventDefault();
    drop.classList.add("over");
  })
);

["dragleave", "drop"].forEach((evt) =>
  drop.addEventListener(evt, (e) => {
    e.preventDefault();
    drop.classList.remove("over");
  })
);

drop.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) upload(file);
});

$("file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) upload(file);
});

async function upload(file) {
  const form = new FormData();
  form.append("file", file);

  startTimer("Uploading...");
  try {
    const response = await fetch("/api/upload", { method: "POST", body: form });
    if (!response.ok) {
      stopTimer(await errorMessage(response), true);
      return;
    }

    const data = await response.json();
    csvId = data.csv_id;

    $("label").textContent = data.label;
    $("columns").innerHTML = "";
    for (const col of data.columns) {
      const li = document.createElement("li");
      // textContent, not innerHTML: column names come from an uploaded file and
      // are not ours to trust as markup.
      const name = document.createElement("b");
      name.textContent = col.name;
      const dtype = document.createElement("span");
      dtype.textContent = ` ${col.dtype}`;
      li.append(name, dtype);
      $("columns").append(li);
    }

    $("loaded").hidden = false;
    $("ask").disabled = false;
    stopTimer(`${data.columns.length} columns loaded. Ask away.`);
    $("question").focus();
  } catch (err) {
    stopTimer(`Upload failed: ${err.message}`, true);
  }
}

/* ---------- ask ---------- */

$("ask").addEventListener("click", ask);
$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !$("ask").disabled) ask();
});

async function ask() {
  const question = $("question").value.trim();
  if (!question) {
    stopTimer("Type a question first.", true);
    return;
  }

  $("ask").disabled = true;
  $("step-result").hidden = true;
  clearProgress();
  stopTimer("");

  try {
    const response = await fetch("/api/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, csv_id: csvId }),
    });

    if (!response.ok) {
      stopTimer(await errorMessage(response), true);
      return;
    }

    // NDJSON: one JSON object per line. Chunks arrive on no particular boundary,
    // so the tail of a chunk is held back until its newline turns up.
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let report = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "progress") addStep(event.detail);
        else if (event.type === "result") report = event.report;
      }
    }

    finishStep();

    if (report) {
      render(report);
      stopTimer("Done.");
    } else {
      stopTimer("The run ended without producing a result.", true);
    }
  } catch (err) {
    finishStep();
    stopTimer(`Request failed: ${err.message}`, true);
  } finally {
    $("ask").disabled = false;
  }
}

/* ---------- rendering ---------- */

/** A block whose body can be folded away.
 *
 * Native <details> rather than a click handler and a class: it is keyboard
 * operable, announced correctly by screen readers, and survives having no JS at
 * all. Writing that by hand buys nothing except bugs.
 *
 * Used for the raw output only. The code block is deliberately never collapsible
 * -- putting it behind a click is how you stop reading it, and reading it is the
 * only thing that catches a right-looking number from the wrong column.
 */
function collapsible(label, node, open = false) {
  const details = document.createElement("details");
  details.className = "block";
  details.open = open;
  const summary = document.createElement("summary");
  summary.className = "block-label";
  summary.textContent = label;
  details.append(summary, node);
  return details;
}

function block(label, node) {
  const wrap = document.createElement("div");
  wrap.className = "block";
  const heading = document.createElement("p");
  heading.className = "block-label";
  heading.textContent = label;
  wrap.append(heading, node);
  return wrap;
}

function pre(text, className) {
  const el = document.createElement("pre");
  if (className) el.className = className;
  el.textContent = text;
  return el;
}

function render(report) {
  const out = $("output");
  out.innerHTML = "";

  if (report.unanswerable) {
    const p = document.createElement("p");
    p.className = "declined";
    p.textContent = report.unanswerable;
    out.append(block("Not answerable", p));
  } else {
    const answer = document.createElement("p");
    answer.className = "answer";
    answer.textContent = report.answer;
    out.append(block("Answer", answer));

    // The code is shown every time, success or failure. A right-looking number
    // from the wrong column is this system's one unfixable risk, and reading the
    // code is the only thing that catches it -- so it is never behind a toggle.
    const attempts = report.attempts > 1 ? ` (last of ${report.attempts} attempts)` : "";
    out.append(block(`Code the model wrote${attempts}`, pre(report.code || "", "code")));

    if (report.result) out.append(collapsible("Raw output", pre(report.result)));
  }

  const list = document.createElement("ul");
  list.className = "steps";
  for (const step of report.history) {
    const li = document.createElement("li");
    if (/failed|could not/.test(step.detail)) li.className = "bad";
    for (const [cls, text] of [
      ["n", String(step.step)],
      ["node", step.node],
      ["detail", step.detail],
    ]) {
      const span = document.createElement("span");
      span.className = cls;
      span.textContent = text;
      li.append(span);
    }
    list.append(li);
  }
  // Folded away when the run was uneventful, open when it was not. A retry or a
  // failure is the case you actually want to notice, so it does not start hidden.
  const eventful = report.history.some((h) => /failed|could not/.test(h.detail));
  out.append(collapsible("What happened", list, eventful));

  $("step-result").hidden = false;
  $("step-result").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- constellation background ---------- */

(function stars() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const canvas = $("stars");
  const ctx = canvas.getContext("2d");
  let dots = [];

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
    // Density scales with area so a wide monitor doesn't look empty and a phone
    // doesn't get a soup of dots.
    const count = Math.min(70, Math.round((canvas.width * canvas.height) / 26000));
    dots = Array.from({ length: count }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.15,
      vy: (Math.random() - 0.5) * 0.15,
    }));
  }

  function frame() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    for (const d of dots) {
      d.x += d.vx;
      d.y += d.vy;
      if (d.x < 0 || d.x > canvas.width) d.vx *= -1;
      if (d.y < 0 || d.y > canvas.height) d.vy *= -1;

      ctx.fillStyle = "rgba(232,163,61,0.35)";
      ctx.fillRect(d.x, d.y, 1.4, 1.4);
    }

    // Join near neighbours, fading the line out with distance.
    for (let i = 0; i < dots.length; i++) {
      for (let j = i + 1; j < dots.length; j++) {
        const dx = dots[i].x - dots[j].x;
        const dy = dots[i].y - dots[j].y;
        const dist = Math.hypot(dx, dy);
        if (dist > 130) continue;
        ctx.strokeStyle = `rgba(232,163,61,${0.10 * (1 - dist / 130)})`;
        ctx.beginPath();
        ctx.moveTo(dots[i].x, dots[i].y);
        ctx.lineTo(dots[j].x, dots[j].y);
        ctx.stroke();
      }
    }

    requestAnimationFrame(frame);
  }

  window.addEventListener("resize", resize);
  resize();
  frame();
})();
