import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const TOKEN = "?token=canvas-test";
const EMPTY = `http://127.0.0.1:4188/${TOKEN}`;
const MISSING_PLAN = `http://127.0.0.1:4189/${TOKEN}`;
const STALE_PLAN = `http://127.0.0.1:4190/${TOKEN}`;

async function intentRequests(page) {
  const sent = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/intent")) {
      sent.push(request.postDataJSON());
    }
  });
  return sent;
}

async function severeAxeViolations(page) {
  const results = await new AxeBuilder({ page }).analyze();
  return results.violations.filter((violation) =>
    ["critical", "serious"].includes(violation.impact),
  );
}

test.describe("pilot with a computed plan", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`/${TOKEN}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Returns Triage");
  });

  test("declares an inline favicon for self-contained loopback loads", async ({ page }) => {
    await expect(page.locator('link[rel="icon"]')).toHaveAttribute("href", /^data:/);
  });

  test("Now names one primary action and no start-a-pilot competitor", async ({ page }) => {
    const now = page.locator("section.now");
    await expect(now.locator("button.primary")).toHaveCount(1);
    await expect(now.locator("button.primary")).not.toHaveText(/start a pilot/i);
    await expect(now.locator(".blocker")).toContainText("In the way:");
    await expect(page.getByText(/Updated \d{1,2}:\d{2}/)).toBeVisible();
    await expect(page.getByText(/computed at \d{1,2}:\d{2}/)).toBeVisible();
  });

  test("existing design and offline work are not reported as just getting started", async ({
    page,
  }) => {
    // The example has a SPEC, governance contract and azure.yaml + infra: the
    // state must reflect them even when the planner's next step is a
    // workspace prerequisite check (or a hard stop with bare Python).
    await expect(page.locator("section.now .now-state")).not.toContainText("Getting started");
    const design = page.locator(".milestone").first();
    await expect(design.locator(".milestone-head")).not.toContainText("In progress");
    await expect(page.locator(".gate", { hasText: "Process design" })).not.toContainText("Waiting");
  });

  test("the proof path marks exactly one milestone as here", async ({ page }) => {
    const path = page.getByRole("list").filter({ has: page.locator(".milestone") }).first();
    await expect(path.locator('[aria-current="step"]')).toHaveCount(1);
    await expect(page.getByText(/You are here/)).toHaveCount(1);
    for (const title of ["Design the process", "Publish on Microsoft Foundry", "Decide on go-live"]) {
      await expect(path.locator(".milestone-title", { hasText: title })).toBeVisible();
    }
  });

  test("shows the process in the owner's words with rule proof", async ({ page }) => {
    await expect(page.getByRole("heading", { name: "The process" })).toBeVisible();
    await expect(page.getByText("Escalate high-value or high-risk cases")).toBeVisible();
    await expect(page.getByRole("list", { name: "Proof for BR-003" })).toContainText(
      "3 test scenarios",
    );
    await expect(page.getByText("Supervisor escalation review")).toBeVisible();
  });

  test("sends the primary action and keeps the state visible", async ({ page }) => {
    const sent = await intentRequests(page);
    const now = page.locator("section.now");
    const headline = await now.locator(".now-state").innerText();
    const response = page.waitForResponse(
      (candidate) => candidate.url().includes("/api/intent") && candidate.status() === 202,
    );
    await now.locator("button.primary").click();
    await response;

    await expect(now.locator(".action-row .sent").first()).toHaveText(
      "Sent to chat. Confirm it there.",
    );
    await expect(now.locator(".now-state")).toHaveText(headline);
    expect(sent).toHaveLength(1);
    expect(["advance_plan", "refresh_plan"]).toContain(sent[0].type);
  });

  test("technical details stay collapsed until asked and persist across reloads", async ({
    page,
  }) => {
    await expect(page.getByText("threadlight-design", { exact: true })).toBeHidden();
    await page.getByText("Technical details").click();
    await expect(page.getByText("threadlight-design", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Inspect in chat" }).first()).toBeVisible();

    await page.getByRole("button", { name: "Reload from the workspace" }).click();
    await expect(page.locator("details.technical")).toHaveAttribute("open", "");
  });

  test("starting a different pilot uses the dialog and can be cancelled", async ({ page }) => {
    const sent = await intentRequests(page);
    await page.getByText("Technical details").click();
    await page.getByRole("button", { name: "Start a different pilot" }).click();
    const dialog = page.getByRole("dialog", { name: "Start a different pilot" });
    await expect(dialog).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(sent).toHaveLength(0);
  });

  test("has no critical or serious axe violations", async ({ page }) => {
    await page.getByText("Technical details").click();
    expect(await severeAxeViolations(page)).toEqual([]);
  });

  test("does not overflow the side panel", async ({ page }) => {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
});

test.describe("empty workspace", () => {
  test("asks to describe the process and sends the brief to chat", async ({ page }) => {
    const sent = await intentRequests(page);
    await page.goto(EMPTY);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Describe your process");
    const brief = page.getByRole("textbox", { name: "Process brief" });
    await expect(brief).toHaveAttribute("placeholder", /^Example:/);
    await brief.fill("Triage supplier invoices against purchase orders.");
    await page.getByRole("button", { name: "Describe your process" }).click();
    await expect(page.getByText("Sent to chat. Confirm it there.")).toBeVisible();
    expect(sent).toEqual([
      { type: "start_pilot", brief: "Triage supplier invoices against purchase orders." },
    ]);
    expect(await severeAxeViolations(page)).toEqual([]);
  });
});

test.describe("plan not computed or out of date", () => {
  test("a missing plan offers Refresh the plan", async ({ page }) => {
    const sent = await intentRequests(page);
    await page.goto(MISSING_PLAN);
    await expect(page.locator("section.now")).toContainText("Status not computed yet");
    await page.getByRole("button", { name: "Refresh the plan" }).click();
    await expect(page.getByText("Sent to chat. Confirm it there.")).toBeVisible();
    expect(sent).toEqual([{ type: "refresh_plan" }]);
  });

  test("an out-of-date plan says so and still shows the last gates", async ({ page }) => {
    await page.goto(STALE_PLAN);
    await expect(page.locator("section.now .blocker")).toContainText(
      "Files changed after the plan was computed",
    );
    await expect(page.getByRole("button", { name: "Refresh the plan" })).toBeVisible();
    await expect(page.getByText("Shown from the last plan; refresh it to be sure.")).toBeVisible();
  });
});
