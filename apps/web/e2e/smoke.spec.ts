import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("landing page leads with outcome verification and honest metrics", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: /Action agents should prove the outcome/i }),
  ).toBeVisible();
  await expect(page.getByText("Evaluation pending", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /Run the disrupted-trip demo/i })).toBeVisible();
});

test("all required product routes render their primary surface", async ({ page }) => {
  const routes = [
    ["/demo", /Break the trip/i],
    ["/runs/mock-1301", /OUTCOME_UNKNOWN/i],
    ["/runs/mock-1301/approval", /Approve exactly this rebooking/i],
    ["/runs/mock-1301/replay", /Replay the evidence/i],
    ["/evals", /Every claim must survive the raw table/i],
    ["/methodology", /Trust is a boundary/i],
  ] as const;

  for (const [route, text] of routes) {
    await page.goto(route);
    await expect(page.getByText(text).first()).toBeVisible();
  }
});

test("mobile approval remains usable and honestly labeled", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/runs/mock-1301/approval");
  await expect(page.getByText(/UI shell only/i)).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve NS451/i })).toBeVisible();
});

test("critical routes have no detectable WCAG A/AA violations", async ({ page }) => {
  for (const route of ["/", "/demo", "/evals", "/methodology", "/runs/mock-1301/replay"]) {
    await page.goto(route);
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();
    expect(results.violations, `${route}: ${JSON.stringify(results.violations)}`).toEqual([]);
  }
});

test("approval actions are keyboard reachable", async ({ page }) => {
  await page.goto("/runs/mock-1301/approval");
  const approve = page.getByRole("button", { name: /Approve NS451/i });
  await approve.focus();
  await expect(approve).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: /Approval preview accepted/i })).toBeVisible();
});
