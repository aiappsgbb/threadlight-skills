const state = { model: null, selectedPhase: "design", showTechnical: false };

const VIEW_TITLES = Object.freeze({
  design: "Pilot definition",
  run: "Pipeline run",
  assurance: "Evidence and assurance",
  handoff: "Production handoff",
  generic: "Lifecycle stage",
});

const token = new URLSearchParams(window.location.search).get("token") ?? "";
const nav = document.querySelector("#phase-nav");
const status = document.querySelector("#status");
const phaseSummary = document.querySelector("#phase-summary");
const phaseDetail = document.querySelector("#phase-detail");
const refreshButton = document.querySelector("#refresh");
const startButton = document.querySelector("#start-pilot");
const startDialog = document.querySelector("#start-dialog");
const startForm = document.querySelector("#start-form");
const cancelBriefButton = document.querySelector("#cancel-brief");
const pilotBrief = document.querySelector("#pilot-brief");

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
      case "'":
        return "&#39;";
      default:
        return character;
    }
  });
}

const escapeAttribute = escapeHtml;

function showError(error) {
  status.textContent = error instanceof Error ? error.message : String(error);
}

function statusTone(statusValue) {
  if (statusValue === "complete" || statusValue === "not-applicable") {
    return "complete";
  }
  if (statusValue === "failed") {
    return "failed";
  }
  return "attention";
}

function statusBadge(statusValue) {
  const safeStatus = escapeHtml(statusValue ?? "unknown");
  return `<span class="status-badge ${statusTone(statusValue)}">${safeStatus}</span>`;
}

function phaseButton(phase) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `phase-tab ${statusTone(phase.status)}`;
  button.textContent = phase.label;
  button.setAttribute(
    "aria-current",
    phase.id === state.selectedPhase ? "step" : "false",
  );
  button.addEventListener("click", () => {
    state.selectedPhase = phase.id;
    state.showTechnical = false;
    render();
  });
  return button;
}

function renderSkillDetails(phase) {
  if (!state.showTechnical) {
    return "";
  }

  const skills = Array.isArray(phase?.skills) ? phase.skills : [];
  if (skills.length === 0) {
    return '<p class="muted">No technical skills are mapped to this outcome.</p>';
  }

  const items = skills
    .map((skill) => {
      const id = skill?.definition?.id ?? "unknown-skill";
      return `<li><code>${escapeHtml(id)}</code>${statusBadge(skill?.status)}</li>`;
    })
    .join("");

  return `<ul class="skill-list">${items}</ul>`;
}

function specializedSummary(phase) {
  const skills = Array.isArray(phase?.skills) ? phase.skills : [];
  const evidence = Array.isArray(phase?.evidence) ? phase.evidence : [];
  const blockers = Array.isArray(phase?.blockers) ? phase.blockers : [];

  switch (phase?.view) {
    case "design": {
      const completeCount = skills.filter(
        (skill) => skill?.status === "complete",
      ).length;
      return `${completeCount} design outcomes complete; ${evidence.length} design outputs detected.`;
    }
    case "run":
      return blockers[0] ?? "Build/deploy is progressing toward verified evidence.";
    case "assurance": {
      const failedCount = skills.filter((skill) => skill?.status === "failed").length;
      if (failedCount > 0) {
        return `${failedCount} assurance checks need attention before continuing.`;
      }
      return "Quality, safety, cost, and governance evidence is ready to inspect.";
    }
    case "handoff": {
      const readiness =
        skills.find((skill) => skill?.definition?.id === "threadlight-production-ready")
          ?.status ?? "pending";
      const pipeline =
        skills.find((skill) => skill?.definition?.id === "threadlight-cicd")
          ?.status ?? "pending";
      const onboarding =
        skills.find((skill) => skill?.definition?.id === "threadlight-customize")
          ?.status ?? "pending";
      return `Readiness is ${readiness}; pipeline is ${pipeline}; onboarding is ${onboarding}.`;
    }
    default:
      return "Inspect this lifecycle stage safely from chat.";
  }
}

function renderEvidenceRows(phase) {
  const evidence = Array.isArray(phase?.evidence) ? phase.evidence : [];
  if (evidence.length === 0) {
    return '<p class="no-evidence">No evidence has been detected for this outcome yet.</p>';
  }

  return evidence
    .map((item) => {
      const path = item?.path ?? "unknown evidence";
      const intent = {
        type: "inspect_evidence",
        phase: phase.id,
        evidenceId: path,
      };
      return `<div class="evidence-row"><span>${escapeHtml(path)}</span><button class="secondary" type="button" data-intent="${escapeAttribute(JSON.stringify(intent))}">Inspect in chat</button></div>`;
    })
    .join("");
}

function renderBlockers(phase) {
  const blockers = Array.isArray(phase?.blockers) ? phase.blockers : [];
  if (blockers.length === 0) {
    return "";
  }

  const items = blockers.map((blocker) => `<li>${escapeHtml(blocker)}</li>`).join("");
  return `<section class="blockers" aria-labelledby="blockers-title"><h3 id="blockers-title">Blockers</h3><ul>${items}</ul></section>`;
}

function renderPhase(phase) {
  if (!phase) {
    phaseDetail.innerHTML =
      '<article class="surface"><h2>Lifecycle stage</h2><p class="muted">No lifecycle phase is available yet.</p></article>';
    return;
  }

  const viewTitle = VIEW_TITLES[phase.view] ?? VIEW_TITLES.generic;
  const summary = specializedSummary(phase);
  const nextAction = Array.isArray(phase.nextActions) ? phase.nextActions[0] : null;
  const action = nextAction
    ? `<button class="primary" type="button" data-intent="${escapeAttribute(JSON.stringify(nextAction))}">Continue this phase</button>`
    : "";
  const technicalLabel = state.showTechnical
    ? "Hide technical details"
    : "Show technical details";

  phaseDetail.innerHTML = `<article class="surface phase-card">
    <header class="card-header">
      <div>
        <p class="eyebrow">${escapeHtml(phase.label)}</p>
        <h2>${escapeHtml(viewTitle)}</h2>
        <p class="phase-copy">${escapeHtml(summary)}</p>
      </div>
      ${statusBadge(phase.status)}
    </header>
    <section aria-labelledby="evidence-title">
      <h3 id="evidence-title">Evidence</h3>
      <div class="evidence-list">${renderEvidenceRows(phase)}</div>
    </section>
    ${renderBlockers(phase)}
    <div class="actions">
      ${action}
      <button id="technical-toggle" class="secondary" type="button">${technicalLabel}</button>
    </div>
    <div class="technical-details">${renderSkillDetails(phase)}</div>
  </article>`;
}

async function postIntent(intent) {
  const response = await fetch(endpoint("/api/intent"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(intent),
  });
  if (!response.ok) {
    let message = `Intent failed: ${response.status}`;
    try {
      const error = await response.json();
      message = error?.error ?? message;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  status.textContent = "Intent sent to chat for confirmation.";
}

async function loadModel() {
  const response = await fetch(endpoint("/api/model"));
  if (!response.ok) {
    let message = `Workspace model failed: ${response.status}`;
    try {
      const error = await response.json();
      message = error?.error ?? message;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }

  const model = await response.json();
  if (!Array.isArray(model?.phases)) {
    throw new Error("Workspace model is missing phases.");
  }

  state.model = model;
  if (!model.phases.some((phase) => phase.id === state.selectedPhase)) {
    state.selectedPhase = model.phases[0]?.id ?? "design";
  }
  status.textContent = model.summary ?? "Workspace ready.";
  render();
}

function renderSummary() {
  const errors = Array.isArray(state.model?.errors) ? state.model.errors : [];
  if (errors.length === 0) {
    phaseSummary.innerHTML =
      '<p class="no-errors">No workspace projection errors.</p>';
    return;
  }

  const items = errors
    .map((error) => {
      const path = error?.path ? `${error.path}: ` : "";
      const code = error?.code ? `${error.code} — ` : "";
      return `<li>${escapeHtml(path)}${escapeHtml(code)}${escapeHtml(error?.message ?? "Unknown error")}</li>`;
    })
    .join("");
  phaseSummary.innerHTML = `<div class="error-panel" role="alert" aria-labelledby="errors-title"><h3 id="errors-title">Projection errors</h3><ul>${items}</ul></div>`;
}

function attachIntentHandlers() {
  for (const button of phaseDetail.querySelectorAll("[data-intent]")) {
    button.addEventListener("click", async () => {
      try {
        await postIntent(JSON.parse(button.dataset.intent));
      } catch (error) {
        showError(error);
      }
    });
  }

  phaseDetail.querySelector("#technical-toggle")?.addEventListener("click", () => {
    state.showTechnical = !state.showTechnical;
    render();
  });
}

function render() {
  const phases = Array.isArray(state.model?.phases) ? state.model.phases : [];
  nav.replaceChildren(...phases.map((phase) => phaseButton(phase)));
  renderSummary();
  renderPhase(phases.find((phase) => phase.id === state.selectedPhase));
  attachIntentHandlers();
}

startButton.addEventListener("click", () => {
  startDialog.showModal();
  pilotBrief.focus();
});

cancelBriefButton.addEventListener("click", () => {
  startDialog.close();
});

startForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await postIntent({ type: "start_pilot", brief: pilotBrief.value });
    startDialog.close();
    startForm.reset();
  } catch (error) {
    showError(error);
  }
});

refreshButton.addEventListener("click", () => {
  loadModel().catch(showError);
});

const events = new EventSource(endpoint("/api/events"));
events.addEventListener("workspace-changed", () => {
  loadModel().catch(showError);
});
events.addEventListener("error", () => {
  status.textContent = "Live refresh disconnected; use Refresh to retry.";
});

loadModel().catch(showError);
