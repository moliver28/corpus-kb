import { expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { test } from "@playwright/test";

// The SPA smoke (program Task 9): a REAL corpus-kb server on a
// throwaway database serves /app/ with the demo corpus preloaded.
// Steps mirror the accepted walkthrough (docs/UI-DEMO.md): sample
// ingest through the UI, an ask that accepts degraded honestly, the
// method advisor, the promote gate copy, and a structured release
// refusal. axe-core scans the shell pages for critical/serious
// accessibility violations.

test.describe("SPA smoke on a live server", () => {
  test("sample ingest runs through the UI pipeline", async ({ page }) => {
    await page.goto("/app/#/ingest");
    await expect(page.getByText("Load data into the corpus")).toBeVisible();
    await page.getByRole("button", { name: "Sample data" }).click();
    await page.getByRole("button", { name: "Load sample knowledge" }).click();
    await page.getByRole("button", { name: "Run it" }).click();
    // The demo load is idempotent; a fresh database embeds five files.
    await expect(page.getByText("Done")).toBeVisible({ timeout: 240_000 });
  });

  test("ask answers with citations and an honest badge (degraded accepted)", async ({ page }) => {
    await page.goto("/app/#/ask");
    await expect(page.getByText("Ask your corpus").first()).toBeVisible();
    await page.getByPlaceholder("Question").fill("How does ingestion work?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    // Citations plus one of the three honest badge words — verified,
    // partial, or not verified are ALL accepted outcomes.
    await expect(page.getByText("Cited sentences")).toBeVisible({ timeout: 240_000 });
    await expect(page.getByText(/^(verified|partial|not verified)$/).first()).toBeVisible();
    await expect(page.getByText("Claim support:").first()).toBeVisible();
  });

  test("the method advisor ranks and keeps its claims guidance", async ({ page }) => {
    await page.goto("/app/#/analyze");
    await expect(page.getByText("Method advisor")).toBeVisible();
    await page.getByPlaceholder("Research task").fill("Group documents into topics");
    await page.getByRole("button", { name: "Rank methods" }).click();
    await expect(page.getByText("Why it ranks here")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("Run this method").first()).toBeVisible();
  });

  test("the promote gate keeps promotion human-only", async ({ page }) => {
    await page.goto("/app/#/codebook");
    await expect(page.getByText("Promote proposed codes").first()).toBeVisible();
    await expect(page.getByText("Only a person promotes.")).toBeVisible();
  });

  test("an unknown release assesses honestly (gates not evaluable)", async ({ page }) => {
    await page.goto("/app/#/release");
    const assess = page.locator(".ant-card").filter({ hasText: "Assess gates" });
    await expect(assess).toBeVisible();
    await assess.getByPlaceholder("Codebook version id").fill("00000000-0000-0000-0000-0000000000ff");
    await assess.getByRole("button", { name: "Run", exact: true }).click();
    // An unknown version is honest, never a crash: every gate reports
    // not_evaluable with its reason.
    await expect(assess.getByText("Gate results")).toBeVisible({ timeout: 60_000 });
    await expect(
      assess.getByText("no codebook document recorded (inputs.codebook is empty)").first(),
    ).toBeVisible();
  });
});

test.describe("accessibility (axe-core)", () => {
  for (const stage of ["#/ingest", "#/analyze", "#/release"]) {
    test(`no critical or serious violations on ${stage}`, async ({ page }) => {
      await page.goto(`/app/${stage}`);
      await page.waitForTimeout(500);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa"])
        .analyze();
      const blocking = results.violations.filter(
        (v) => v.impact === "critical" || v.impact === "serious",
      );
      if (blocking.length > 0) {
        console.error(
          "axe violations:",
          JSON.stringify(
            blocking.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })),
            null,
            2,
          ),
        );
      }
      expect(blocking).toEqual([]);
    });
  }
});
