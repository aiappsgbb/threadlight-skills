const params = new URLSearchParams(window.location.search);
const token = params.get("token") ?? "";
const status = document.querySelector("#status");
const handoffButton = document.querySelector("#prepare-handoff");

function withToken(path) {
  const url = new URL(path, window.location.origin);
  url.searchParams.set("token", token);
  return url.toString();
}

function setStatus(message) {
  status.textContent = message;
}

async function refresh() {
  const response = await fetch(withToken("/api/model"));
  if (!response.ok) {
    throw new Error(`Workspace model failed: ${response.status}`);
  }
  const model = await response.json();
  setStatus(model.summary ?? "Workspace ready");
}

handoffButton.addEventListener("click", async () => {
  try {
    const response = await fetch(withToken("/api/intent"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ type: "prepare_handoff" }),
    });
    if (!response.ok) {
      throw new Error(`Intent failed: ${response.status}`);
    }
    setStatus("Intent sent to chat for confirmation.");
  } catch (error) {
    setStatus(error.message);
  }
});

const events = new EventSource(withToken("/api/events"));
events.addEventListener("workspace-changed", () => {
  refresh().catch((error) => setStatus(error.message));
});

refresh().catch((error) => setStatus(error.message));
