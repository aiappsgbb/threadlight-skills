import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test.beforeEach(async ({ page }) => {
  await page.goto("/?token=canvas-test");
});

test("shows six outcome phase buttons while hiding technical skill names", async ({
  page,
}) => {
  const phases = [
    "Design",
    "Build / Deploy",
    "Discover",
    "Protect / Govern",
    "Improve",
    "Handoff",
  ];

  for (const phase of phases) {
    await expect(page.getByRole("button", { name: phase })).toBeVisible();
  }
  await expect(page.getByText("threadlight-design")).toHaveCount(0);
});

test("posts a start pilot brief to chat", async ({ page }) => {
  await page.getByRole("button", { name: "Start a pilot" }).click();
  await expect(page.getByRole("dialog", { name: "Start a pilot" })).toBeVisible();
  await page
    .getByLabel("Pilot brief")
    .fill("Pilot governed returns triage with evidence-led handoff.");

  const intentResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/intent") && response.status() === 202,
  );
  await page.getByRole("button", { name: "Send to chat" }).click();
  await intentResponse;

  await expect(page.getByRole("status")).toContainText(
    "Intent sent to chat for confirmation.",
  );
});

test("cancels an empty start pilot dialog without sending intent", async ({
  page,
}) => {
  const intentRequests = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/intent")) {
      intentRequests.push(request);
    }
  });

  await page.getByRole("button", { name: "Start a pilot" }).click();
  const dialog = page.getByRole("dialog", { name: "Start a pilot" });
  await expect(dialog).toBeVisible();

  await page.getByRole("button", { name: "Cancel" }).click();
  await page.waitForTimeout(100);

  await expect(dialog).toBeHidden();
  expect(intentRequests).toHaveLength(0);
});

test("reveals technical skill details only after explicit disclosure", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Design" }).click();
  await expect(page.getByText("threadlight-design")).toHaveCount(0);

  await page.getByRole("button", { name: "Show technical details" }).click();

  await expect(page.getByText("threadlight-design")).toBeVisible();
});

test("has no critical or serious axe violations", async ({ page }) => {
  const results = await new AxeBuilder({ page }).analyze();
  const severeViolations = results.violations.filter((violation) =>
    ["critical", "serious"].includes(violation.impact),
  );

  expect(severeViolations).toEqual([]);
});
