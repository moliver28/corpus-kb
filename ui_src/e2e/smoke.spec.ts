import { expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { test } from "@playwright/test";

// The SPA smoke (program Task 9): a REAL corpus-kb server on a
// throwaway database serves /app/ with the demo corpus preloaded.
// Steps mirror the accepted walkthrough (docs/research/UI-DEMO.md): sample
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
    // Two honest outcomes, both accepted: a cited answer (with one of the
    // three badge words), or the degraded note when the CPU runner cannot
    // generate inside the server's LLM budget. The ask page states the
    // degraded note as "Answer generation is unavailable." — never a crash,
    // never a silent wrong answer.
    const cited = page.getByText("Cited sentences");
    const degraded = page.getByText("Answer generation is unavailable");
    await expect(cited.or(degraded)).toBeVisible({ timeout: 840_000 });
    if (await cited.isVisible()) {
      await expect(page.getByText(/^(verified|partial|not verified)$/).first()).toBeVisible();
      await expect(page.getByText("Claim support:").first()).toBeVisible();
    }
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

  test("the run ledger offers the project filter and lists the runs", async ({ page }) => {
    await page.goto("/app/#/scale");
    // The sample ingest from the first test wrote ledger runs, so the
    // poll answers with history. The filter lists every project the
    // runs name (the demo corpus has none — all rows are "no project");
    // the narrowing logic itself is covered by the runFilter unit tests.
    const ledger = page.locator(".ant-card").filter({ hasText: "Run ledger" });
    const filter = ledger.locator(".ant-select");
    await expect(filter).toBeVisible({ timeout: 60_000 });
    await filter.click();
    // AntD renders a visually hidden aria mirror with role="option", so
    // assert the dropdown item a user sees, not the role element.
    const dropdown = page.locator(".ant-select-dropdown");
    await expect(dropdown).toBeVisible();
    await expect(
      dropdown.locator(".ant-select-item-option").filter({ hasText: "All projects" }),
    ).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(ledger.getByRole("columnheader", { name: "Project" })).toBeVisible();
    await expect(ledger.getByRole("cell").first()).toBeVisible();
  });
});

test.describe("accessibility (axe-core)", () => {
  for (const stage of ["#/ingest", "#/analyze", "#/release", "#/scale"]) {
    test(`no critical or serious violations on ${stage}`, async ({ page }) => {
      await page.goto(`/app/${stage}`);
      // Wait for the stage's first card, not a fixed sleep.
      await page.locator(".ant-card-head-title").first().waitFor();
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
