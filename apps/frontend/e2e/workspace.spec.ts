import { expect, test } from "@playwright/test";
async function login(
  page: import("@playwright/test").Page,
  email = "analyst@ledgerguard.demo",
) {
  await page.goto("/");
  await page.getByLabel("Email address").fill(email);
  await page.getByLabel("Password", { exact: true }).fill("SandboxDemo!2026");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Your ledger, in focus." }),
  ).toBeVisible();
}
test("login, dashboard, filters, reports and responsive navigation", async ({
  page,
}) => {
  await login(page);
  await expect(page.getByText("12,000", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "../../screenshots/overview.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Transactions", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Search transactions" })
    .fill("TXN-000001");
  await expect(page.getByRole("button", { name: /TXN-000001/ })).toBeVisible();
  await page.getByRole("button", { name: /TXN-000001/ }).click();
  await expect(page.getByRole("dialog")).toContainText("high_value");
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page
    .getByRole("link", { name: "Risk intelligence", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Risk indicator trend", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Risk distribution", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../screenshots/risk.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Reports", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "PDF", exact: true }).first(),
  ).toBeVisible();
  const [file] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "PDF", exact: true }).first().click(),
  ]);
  expect(file.suggestedFilename()).toMatch(/\.pdf$/);
  await page.getByRole("link", { name: "AI workspace", exact: true }).click();
  await expect(
    page.getByRole("textbox", { name: "Analysis request" }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../screenshots/operations.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Toggle sidebar" }).click();
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Your ledger, in focus." }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../../screenshots/mobile.png",
    fullPage: true,
  });
});
test("analyst can start analysis and see persisted completion", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("link", { name: "AI workspace", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Analysis request" })
    .fill("Find transactions above £10,000 made in the last 24 hours.");
  await page
    .getByRole("button", { name: "Start analysis", exact: true })
    .click();
  await expect(
    page.getByText("Review complete.", { exact: false }),
  ).toBeVisible({ timeout: 90000 });
  await page.reload();
  await expect(
    page.getByText("Review complete.", { exact: false }),
  ).toBeVisible();
});

test("independent reviewer approves through the UI and the graph resumes", async ({
  page,
  browser,
}) => {
  await login(page, "ui-maker@ledgerguard.demo");
  await page.getByRole("link", { name: "AI workspace", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Analysis request" })
    .fill(
      "Reconcile today's duplicate ledger entries and prepare a correction for approval.",
    );
  await page
    .getByRole("button", { name: "Start analysis", exact: true })
    .click();
  await expect(
    page.getByText("Human review required", { exact: true }),
  ).toBeVisible({ timeout: 60000 });
  await page.getByRole("link", { name: "Approvals", exact: true }).click();
  await page.locator("tbody tr").first().getByRole("button").click();
  await expect(page.getByRole("dialog")).toContainText(
    "An authorized second user must review",
  );
  await expect(
    page.getByRole("button", { name: "Approve", exact: true }),
  ).toHaveCount(0);
  const context = await browser.newContext();
  const reviewer = await context.newPage();
  await login(reviewer, "ui-reviewer@ledgerguard.demo");
  await reviewer.getByRole("link", { name: "Approvals", exact: true }).click();
  await reviewer.locator("tbody tr").first().getByRole("button").click();
  await expect(reviewer.getByRole("dialog")).toContainText("UI-DUP-001");
  await reviewer.screenshot({
    path: "../../screenshots/approval-review.png",
    fullPage: true,
  });
  await reviewer.getByRole("button", { name: "Approve", exact: true }).click();
  await reviewer
    .getByRole("textbox", { name: "Decision reason" })
    .fill("Reviewed original, duplicate and single bank settlement.");
  await reviewer
    .getByRole("button", { name: "Confirm decision", exact: true })
    .click();
  await expect(reviewer.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("link", { name: "AI workspace", exact: true }).click();
  await expect(
    page.getByText("Review complete.", { exact: false }),
  ).toBeVisible({ timeout: 60000 });
  await context.close();
});
