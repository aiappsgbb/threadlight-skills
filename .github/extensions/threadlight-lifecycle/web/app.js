const token = new URLSearchParams(window.location.search).get("token") ?? "";
const content = document.querySelector("#content");
const status = document.querySelector("#status");
const updated = document.querySelector("#updated");
const refreshButton = document.querySelector("#refresh");
const startDialog = document.querySelector("#start-dialog");
const startForm = document.querySelector("#start-form");
const dialogBrief = document.querySelector("#dialog-brief");
const cancelBriefButton = document.querySelector("#cancel-brief");

const TECHNICAL_KEY = "threadlight-canvas:technical-open";
const EXAMPLE_BRIEF =
  "Example: When a customer asks to return an order, check it against our return policy, approve or deny with the policy clause cited, and send high-value or suspicious cases to a supervisor.";

const STATE_ICONS = Object.freeze({
  proven: "proven",
  done: "done",
  ready: "ready",
  prepared: "prepared",
  current: "current",
  "in-progress": "current",
  waiting: "waiting",
  todo: "todo",
  skipped: "skipped",
  failed: "failed",
  stale: "stale",
  attention: "stale",
  unknown: "unknown",
  none: "todo",
});

const TONE_ICONS = Object.freeze({
  blocked: "failed",
  attention: "stale",
  ready: "ready",
  progress: "current",
  empty: "todo",
});

const state = {
  model: null,
  technicalOpen: readTechnicalOpen(),
  feedback: null,
};

function readTechnicalOpen() {
  try {
    return window.sessionStorage.getItem(TECHNICAL_KEY) === "open";
  } catch {
    return false;
  }
}

function writeTechnicalOpen(open) {
  state.technicalOpen = open;
  try {
    window.sessionStorage.setItem(TECHNICAL_KEY, open ? "open" : "closed");
  } catch {
    // Storage can be unavailable in hardened webviews; keep in-memory state.
  }
}

function endpoint(path) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("token", token);
  return url.toString();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => {
    switch (character) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      default:
        return "&#39;";
    }
  });
}

function icon(name, extraClass = "") {
  const safe = STATE_ICONS[name] ?? name;
  return `<svg class="icon ${extraClass}" aria-hidden="true" focusable="false"><use href="#i-${escapeHtml(safe)}"></use></svg>`;
}

function stateTag(stateName, label) {
  return `<span class="state s-${escapeHtml(stateName)}">${icon(stateName)}${escapeHtml(label)}</span>`;
}

function parseTime(value) {
  const parsed = value ? new Date(value) : null;
  return parsed && !Number.isNaN(parsed.getTime()) ? parsed : null;
}

function clockTime(value) {
  const parsed = parseTime(value);
  return parsed
    ? parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
}

function age(value) {
  const parsed = parseTime(value);
  if (!parsed) {
    return "";
  }
  const minutes = Math.round((Date.now() - parsed.getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function intentButton(intent, label, className, slot) {
  // Feedback belongs to this exact request, so it disappears when the next
  // step changes instead of lingering beside a different action.
  const key = `${slot}|${JSON.stringify(intent)}`;
  const feedback = state.feedback?.key === key ? state.feedback : null;
  const note = feedback
    ? `<span class="sent${feedback.error ? " error" : ""}" role="status">${escapeHtml(feedback.text)}</span>`
    : `<span class="sent" role="status"></span>`;
  return `<span class="action-row"><button class="${className}" type="button" data-key="${escapeHtml(key)}" data-intent="${escapeHtml(JSON.stringify(intent))}">${escapeHtml(label)}</button>${note}</span>`;
}

// ---------------------------------------------------------------------------
// Sections
// ---------------------------------------------------------------------------

function renderEmpty(view) {
  const feedback = state.feedback?.key === "start" ? state.feedback : null;
  return `<section class="empty now" aria-labelledby="pilot-name">
    <h1 id="pilot-name">Describe your process</h1>
    <p class="lede">Threadlight turns a business process into a working agent pilot on Microsoft Foundry, then carries it toward production with selected runtime controls and the evidence for each. Start with the process in your own words.</p>
    <form id="empty-form">
      <label for="pilot-brief">Process brief</label>
      <textarea id="pilot-brief" name="brief" required maxlength="4000" placeholder="${escapeHtml(EXAMPLE_BRIEF)}"></textarea>
      <span class="action-row">
        <button class="primary" type="submit">${escapeHtml(view.now.action.label)}</button>
        <span class="sent${feedback?.error ? " error" : ""}" role="status">${escapeHtml(feedback?.text ?? "")}</span>
      </span>
    </form>
    <p class="muted">Chat shows what will happen and asks you to confirm before anything is created.</p>
  </section>`;
}

function renderNow(view, errors) {
  const now = view.now;
  const blocker = now.blocker
    ? `<p class="blocker"><strong>In the way:</strong> ${escapeHtml(now.blocker)}</p>`
    : "";
  let planNote = "";
  if (view.plan.status === "current" && view.plan.decidedAt) {
    planNote = `<p class="plan-note">Next step from Threadlight's plan, computed at ${escapeHtml(clockTime(view.plan.decidedAt))}.</p>`;
  } else if (view.plan.status === "stale" && view.plan.decidedAt) {
    planNote = `<p class="plan-note">The plan below is from ${escapeHtml(clockTime(view.plan.decidedAt))} and may be out of date.</p>`;
  }
  const notice =
    errors.length > 0
      ? `<p class="notice">${icon("stale")}<span>${errors.length === 1 ? "One evidence file" : `${errors.length} evidence files`} could not be trusted. See technical details.</span></p>`
      : "";
  return `<section class="now tone-${escapeHtml(now.tone)}" aria-labelledby="pilot-name">
    <h1 id="pilot-name">${escapeHtml(view.pilot.name)}</h1>
    <p class="now-state s-${escapeHtml(now.tone === "progress" ? "current" : now.tone)}">${icon(TONE_ICONS[now.tone] ?? "current")}<span>${escapeHtml(now.headline)}</span></p>
    ${blocker}
    ${intentButton(now.action.intent, now.action.label, "primary", "now")}
    ${planNote}
    ${notice}
  </section>`;
}

function renderProcess(process, description) {
  const lede = description ? `<p class="lede">${escapeHtml(description)}</p>` : "";
  if (!process) {
    return `<section class="process-section" aria-labelledby="process-title">
      <h2 id="process-title" class="section-title">The process</h2>
      <p class="muted">The process isn't described yet. It appears here once the design is written.</p>
    </section>`;
  }
  const steps = process.steps.length
    ? `<div><h3 class="section-title">Steps</h3><ol class="steps">${process.steps
        .map(
          (step) =>
            `<li>${escapeHtml(step.title)}${step.actor ? ` <span class="actor">· ${escapeHtml(step.actor)}</span>` : ""}</li>`,
        )
        .join("")}</ol></div>`
    : "";
  const rules = process.rules.length
    ? `<div><h3 class="section-title">Business rules</h3><ul class="rules">${process.rules
        .map((rule) => {
          const proofs = rule.proofs.length
            ? rule.proofs
                .map(
                  (proof) =>
                    `<li class="proof" title="${escapeHtml(proof.detail ?? "")}">${icon("prepared")}${escapeHtml(proof.label)}</li>`,
                )
                .join("")
            : `<li class="proof unproven">${icon("todo")}${escapeHtml(rule.unproven)}</li>`;
          return `<li class="rule"><span><span class="rule-id">${escapeHtml(rule.id)}</span>${escapeHtml(rule.title)}</span><ul class="proofs" aria-label="Proof for ${escapeHtml(rule.id)}">${proofs}</ul></li>`;
        })
        .join("")}</ul></div>`
    : "";
  const decisions = process.decisions.length
    ? `<div><h3 class="section-title">Human decisions</h3><ul class="decisions">${process.decisions
        .map(
          (decision) =>
            `<li><span>${escapeHtml(decision.title)}</span>${decision.actor ? `<span class="who">${escapeHtml(decision.actor)}</span>` : ""}</li>`,
        )
        .join("")}</ul></div>`
    : "";
  const kpis = process.kpis.length
    ? `<div><h3 class="section-title">How success is measured</h3><ul class="kpis">${process.kpis
        .map(
          (kpi) =>
            `<li><span>${escapeHtml(kpi.name.replace(/_/g, " "))}</span><span class="target">${escapeHtml(kpi.target || "no target set")}</span></li>`,
        )
        .join("")}</ul></div>`
    : "";
  return `<section class="process-section" aria-labelledby="process-title">
    <h2 id="process-title" class="section-title">The process</h2>
    <div class="process">${lede}${steps}${rules}${decisions}${kpis}</div>
  </section>`;
}

function renderGate(gate) {
  const label = gate.state === "waiting" ? "Waiting" : gate.stateLabel;
  const detail =
    gate.current && gate.detail ? `<span class="gate-detail">${escapeHtml(gate.detail)}</span>` : "";
  return `<li class="gate${gate.current ? " is-current" : ""}"><span>${escapeHtml(gate.title)}</span>${stateTag(gate.current && gate.state !== "failed" ? "current" : gate.state, gate.current && gate.state !== "failed" ? `You are here · ${label}` : label)}${detail}</li>`;
}

function renderProofPath(proofPath) {
  const intro = !proofPath.planned
    ? `<p class="muted section-title">The steps appear once Threadlight's plan is computed.</p>`
    : proofPath.stale
      ? `<p class="muted section-title">Shown from the last plan; refresh it to be sure.</p>`
      : "";
  // The root blocker is named once; later milestones just say they wait.
  let waitingNamed = false;
  const items = proofPath.milestones
    .map((milestone) => {
      const collapsed = ["waiting", "todo", "unknown", "skipped"].includes(milestone.state);
      const gates =
        !collapsed && milestone.gates.length
          ? `<ol class="gates">${milestone.gates.map(renderGate).join("")}</ol>`
          : "";
      const waitingDetail =
        milestone.detail ??
        (milestone.state === "waiting"
          ? milestone.gates.find((gate) => gate.detail)?.detail ?? null
          : null);
      const showDetail = waitingDetail && !gates && !(milestone.state === "waiting" && waitingNamed);
      if (milestone.state === "waiting" && waitingDetail) waitingNamed = true;
      const detail = showDetail ? `<p class="detail">${escapeHtml(waitingDetail)}</p>` : "";
      const iconName = milestone.state === "current" ? "current" : milestone.state;
      return `<li class="milestone s-${escapeHtml(milestone.state)}"${milestone.current ? ' aria-current="step"' : ""}>
        ${icon(iconName)}
        <div>
          <div class="milestone-head"><span class="milestone-title">${escapeHtml(milestone.title)}</span>${stateTag(milestone.state, milestone.stateLabel)}</div>
          ${detail}${gates}
        </div>
      </li>`;
    })
    .join("");
  return `<section class="proof-section" aria-labelledby="path-title">
    <h2 id="path-title" class="section-title">Proof path</h2>
    ${intro}
    <ol class="path-list">${items}</ol>
  </section>`;
}

function renderGoLive(goLive) {
  const readiness = goLive.readiness;
  const cost = goLive.cost;
  const readinessAge = readiness.checkedAt ? `Checked ${age(readiness.checkedAt)}` : "";
  const costAge = cost.checkedAt ? `Estimated ${age(cost.checkedAt)}` : "";
  return `<section class="golive-section" aria-labelledby="golive-title">
    <h2 id="golive-title" class="section-title">Go-live and value</h2>
    <dl class="facts">
      <div class="fact"><dt>Production readiness</dt><dd>
        <span class="value s-${escapeHtml(readiness.state)}">${icon(readiness.state)}<span>${escapeHtml(readiness.label)}</span></span>
        ${readiness.detail ? `<span class="sub">${escapeHtml(readiness.detail)}</span>` : ""}
        ${readinessAge ? `<span class="sub">${escapeHtml(readinessAge)}</span>` : ""}
      </dd></div>
      <div class="fact"><dt>Running cost</dt><dd>
        <span class="value">${escapeHtml(cost.label)}</span>
        ${cost.detail ? `<span class="sub">${escapeHtml(cost.detail)}</span>` : ""}
        ${costAge ? `<span class="sub">${escapeHtml(costAge)}</span>` : ""}
      </dd></div>
    </dl>
  </section>`;
}

const SKILL_STATUS_STATE = Object.freeze({
  complete: "done",
  prepared: "prepared",
  running: "in-progress",
  ready: "todo",
  blocked: "waiting",
  "not-started": "todo",
  "not-applicable": "skipped",
  failed: "failed",
  stale: "stale",
});

function skillState(status) {
  return SKILL_STATUS_STATE[status] ?? "unknown";
}

function renderTechnical(view, errors) {
  const technical = view.technical;
  const stages = view.proofPath.milestones.flatMap((milestone) => milestone.gates);
  const stageRows = stages.length
    ? `<div><h3 class="section-title">Planner stages</h3><ul class="rows">${stages
        .map((gate) => {
          const t = gate.technical;
          const files = [
            ...t.artifactsSeen.map((path) => `seen ${path}`),
            ...t.artifactsMissing.map((path) => `missing ${path}`),
          ];
          return `<li><span class="row-head"><code>${escapeHtml(gate.stage)}</code><span class="row-meta">${escapeHtml(t.decision ?? "")}${t.skillId ? ` · ${escapeHtml(t.skillId)}` : ""}</span></span>${t.reason ? `<span>${escapeHtml(t.reason)}</span>` : ""}${files.length ? `<span class="row-meta path">${escapeHtml(files.join(" · "))}</span>` : ""}</li>`;
        })
        .join("")}</ul></div>`
    : "";
  const evidence = technical.evidence.length
    ? `<div><h3 class="section-title">Evidence files</h3><ul class="rows">${technical.evidence
        .map((item, index) => {
          const s = skillState(item.status);
          const verdict = item.evidenceState ? ` · ${item.evidenceState}` : "";
          const meta = [item.live ? "live check" : "offline", age(item.modifiedAt)]
            .filter(Boolean)
            .join(" · ");
          return `<li><span class="row-head"><span class="path">${escapeHtml(item.path)}</span>${stateTag(s, `${item.status}${verdict}`)}</span><span class="row-head"><span class="row-meta">${escapeHtml(meta)}</span>${intentButton({ type: "inspect_evidence", phase: item.milestone, evidenceId: item.path }, "Inspect in chat", "quiet", `ev-${index}`)}</span></li>`;
        })
        .join("")}</ul></div>`
    : `<p class="muted">No evidence files found yet.</p>`;
  const handoffs = technical.handoffs.length
    ? `<div><h3 class="section-title">Optional checks</h3><ul class="rows">${technical.handoffs
        .map(
          (handoff, index) =>
            `<li><span class="row-head"><code>${escapeHtml(handoff.skillId)}</code>${intentButton({ type: "invoke_skill", skillId: handoff.skillId }, "Ask in chat", "quiet", `ho-${index}`)}</span>${handoff.manifest ? `<span class="row-meta path">${escapeHtml(handoff.manifest)}</span>` : ""}</li>`,
        )
        .join("")}</ul></div>`
    : "";
  const skills = `<div><h3 class="section-title">Skills</h3><ul class="rows">${technical.skills
    .map(
      (skill) =>
        `<li><span class="row-head"><code>${escapeHtml(skill.id)}</code>${stateTag(skillState(skill.status), skill.status)}</span><span class="row-meta">${escapeHtml(skill.label)}${skill.stage ? ` · stage ${escapeHtml(skill.stage)}` : ""}</span></li>`,
    )
    .join("")}</ul></div>`;
  const errorList = errors.length
    ? `<div><h3 class="section-title">Evidence problems</h3><ul class="rows errors">${errors
        .map(
          (error) =>
            `<li>${error.path ? `<span class="path">${escapeHtml(error.path)}</span>` : ""}<span>${escapeHtml(error.message ?? error.code ?? "Unknown problem")}</span></li>`,
        )
        .join("")}</ul></div>`
    : "";
  return `<details class="technical" id="technical"${state.technicalOpen ? " open" : ""}>
    <summary>Technical details</summary>
    <div class="technical-body">${errorList}${stageRows}${evidence}${handoffs}${skills}
      <span class="action-row start-another"><button id="start-another" class="secondary" type="button">Start a different pilot</button><span class="sent" role="status"></span></span>
    </div>
  </details>`;
}

// ---------------------------------------------------------------------------
// Render + events
// ---------------------------------------------------------------------------

function render() {
  const model = state.model;
  const view = model?.view;
  if (!view) {
    return;
  }
  const errors = Array.isArray(model.errors) ? model.errors : [];
  updated.textContent = view.updatedAt ? `Updated ${clockTime(view.updatedAt)}` : "";
  content.innerHTML = view.empty
    ? renderEmpty(view)
    : [
        renderNow(view, errors),
        renderProcess(view.process, view.pilot.description),
        renderProofPath(view.proofPath),
        renderGoLive(view.goLive),
        renderTechnical(view, errors),
      ].join("");
  content.hidden = false;
  status.textContent = "";
}

async function postIntent(intent) {
  const response = await fetch(endpoint("/api/intent"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(intent),
  });
  if (!response.ok) {
    let message = `Could not send to chat (${response.status}).`;
    try {
      const error = await response.json();
      message = error?.error ?? message;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
}

async function sendFromButton(button) {
  const key = button.dataset.key;
  const note = button.parentElement?.querySelector(".sent");
  button.disabled = true;
  try {
    await postIntent(JSON.parse(button.dataset.intent));
    state.feedback = { key, text: "Sent to chat. Confirm it there.", error: false };
  } catch (error) {
    state.feedback = { key, text: error.message, error: true };
  } finally {
    button.disabled = false;
  }
  if (note) {
    note.textContent = state.feedback.text;
    note.classList.toggle("error", state.feedback.error);
  }
}

content.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || !content.contains(button)) {
    return;
  }
  if (button.dataset.intent) {
    sendFromButton(button);
  } else if (button.id === "start-another") {
    startDialog.showModal();
    dialogBrief.focus();
  }
});

content.addEventListener("toggle", (event) => {
  if (event.target.id === "technical") {
    writeTechnicalOpen(event.target.open);
  }
}, true);

content.addEventListener("submit", async (event) => {
  if (event.target.id !== "empty-form") {
    return;
  }
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector("button[type=submit]");
  const note = form.querySelector(".sent");
  button.disabled = true;
  try {
    await postIntent({ type: "start_pilot", brief: form.elements.brief.value });
    state.feedback = { key: "start", text: "Sent to chat. Confirm it there.", error: false };
  } catch (error) {
    state.feedback = { key: "start", text: error.message, error: true };
  } finally {
    button.disabled = false;
  }
  note.textContent = state.feedback.text;
  note.classList.toggle("error", state.feedback.error);
});

startForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await postIntent({ type: "start_pilot", brief: dialogBrief.value });
    startDialog.close();
    startForm.reset();
    const note = content.querySelector("#start-another")?.nextElementSibling;
    if (note) note.textContent = "New pilot brief sent to chat. Confirm it there.";
  } catch (error) {
    dialogBrief.setCustomValidity(error.message);
    dialogBrief.reportValidity();
    dialogBrief.setCustomValidity("");
  }
});

cancelBriefButton.addEventListener("click", () => {
  startDialog.close();
});

async function loadModel() {
  const response = await fetch(endpoint("/api/model"));
  if (!response.ok) {
    let message = `The workspace could not be read (${response.status}).`;
    try {
      const error = await response.json();
      message = error?.error ?? message;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  const model = await response.json();
  if (!model?.view) {
    throw new Error("The workspace view is missing; reload the Canvas.");
  }
  state.model = model;
  render();
}

function showLoadError(error) {
  status.textContent = error instanceof Error ? error.message : String(error);
}

refreshButton.addEventListener("click", () => {
  loadModel().catch(showLoadError);
});

const events = new EventSource(endpoint("/api/events"));
events.addEventListener("workspace-changed", () => {
  loadModel().catch(showLoadError);
});
events.addEventListener("error", () => {
  status.textContent = "Live updates paused. Use the reload button to refresh.";
});
events.addEventListener("open", () => {
  if (status.textContent.startsWith("Live updates paused")) {
    status.textContent = "";
  }
});

loadModel().catch(showLoadError);
