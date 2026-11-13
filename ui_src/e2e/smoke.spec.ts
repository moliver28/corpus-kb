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

  test("the document drawer adds a tag and edits metadata through the manifest ops", async ({
    page,
  }) => {
    await page.goto("/app/#/review");
    const docs = page.locator(".ant-card").filter({ hasText: "Documents" });
    await expect(docs.getByRole("cell").first()).toBeVisible({ timeout: 60_000 });
    await docs.getByRole("button", { name: "Details" }).first().click();
    // The drawer is the document dialog; the reads land first and the
    // two editors render under their headings.
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByRole("heading", { name: "Tags" })).toBeVisible();
    await expect(drawer.getByRole("heading", { name: "Metadata" })).toBeVisible();
    // Write one tag through the tag-document op; the ok banner names
    // the save and the re-read shows the tag chip.
    const unique = `e2e-${Date.now()}`;
    await drawer.getByPlaceholder("Add a tag").fill(unique);
    await drawer.getByRole("button", { name: "Add", exact: true }).click();
    await expect(drawer.getByText("Tag added.")).toBeVisible({ timeout: 60_000 });
    await expect(drawer.getByText(unique)).toBeVisible();
    // Remove it again through the untag-document op behind its confirm
    // (the Popconfirm renders in a portal next to the dialog).
    await drawer.getByRole("button", { name: `Remove tag ${unique}` }).click();
    await page.getByRole("button", { name: "Remove", exact: true }).click();
    await expect(drawer.getByText("Tag removed.")).toBeVisible({ timeout: 60_000 });
    // Save one metadata key through the set-metadata op.
    await drawer.getByPlaceholder("Key").fill("e2e-check");
    await drawer.getByPlaceholder("Value").fill("wave2");
    await drawer.getByRole("button", { name: "Save", exact: true }).click();
    await expect(drawer.getByText("Metadata saved.")).toBeVisible({ timeout: 60_000 });
  });

  test("the drawer NLP panel runs a scoped analysis on one document", async ({ page }) => {
    // Phase-2 Wave 5 done-when: from ONE document, a novice launches a
    // scoped analysis and reads the result WITHOUT leaving the drawer.
    await page.goto("/app/#/review");
    const docs = page.locator(".ant-card").filter({ hasText: "Documents" });
    await expect(docs.getByRole("cell").first()).toBeVisible({ timeout: 60_000 });
    await docs.getByRole("button", { name: "Analyze" }).first().click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByText("Analyze this document")).toBeVisible();
    // The chunk window read pre-fills the scope (the demo corpus carries
    // chunked documents); keyphrase extraction runs ungated on the
    // throwaway host, so the receipt is the full success word (the demo
    // config disables the sentiment screen — its honest no-run note is
    // covered by the drawer unit tests). Both scope wordings are honest:
    // all-chunks, or the capped first-N window.
    await expect(
      drawer.getByText(/chunks of this document|The runs use the first/).first(),
    ).toBeVisible({ timeout: 60_000 });
    await drawer.getByRole("button", { name: "Keyphrase extraction" }).click();
    await drawer.getByRole("button", { name: "Run", exact: true }).click();
    // Every settled headline is honest: the receipt word for a run that
    // finished whole, or the not-success line when it did not. A GREEN
    // run proves more than "settled": the success word, plus at least one
    // rendered phrase row in the raw envelope (an empty keyphrases list
    // on the demo corpus is a real failure, never a pass).
    await expect(
      drawer
        .getByText(
          /^(Done|Nothing to show|Finished, with gaps|Partly done|No answer given|The server finished without success\. Read its note:)$/,
        )
        .first(),
    ).toBeVisible({ timeout: 120_000 });
    await expect(drawer.getByText("Done", { exact: true })).toBeVisible();
    const raw = drawer.locator(".ui-json pre");
    await drawer.locator(".ui-json summary").click();
    await expect(raw).toContainText('"phrase"');
  });

  // The workbench pair (aaa- prefix sorts first, so both entities sit
  // in every first window the Map reads).
  const unique = Date.now().toString();
  const first = `aaa-e2e-${unique}-a`;
  const second = `aaa-e2e-${unique}-b`;

  test("the graph workbench creates, finds, inspects, and links an entity", async ({
    page,
  }) => {
    // The picker re-read (P1-1) and the server-side read-your-writes
    // (P1-2) make every step answer in seconds; a hung step now costs
    // three minutes, not the suite-wide 15 (P3-5).
    test.setTimeout(180_000);
    await page.goto("/app/#/graph");
    const entities = page.locator(".ant-card").filter({ hasText: "Entities" });
    const createForm = page.locator(".ant-card").filter({ hasText: "Create an entity" });
    // CREATE: two entities through the form; each ok banner names the
    // save and the window re-reads (the write op is add-entity).
    for (const name of [first, second]) {
      await createForm.getByPlaceholder("Name").fill(name);
      await createForm.getByPlaceholder("Type").fill(`e2e-${unique}`);
      await createForm.getByRole("button", { name: "Create an entity" }).click();
      await expect(page.getByText("Entity created.")).toBeVisible({ timeout: 60_000 });
    }
    // FIND: the type filter narrows the window read to this run.
    await entities.getByPlaceholder("Only this type").fill(`e2e-${unique}`);
    await entities.getByPlaceholder("Only this type").press("Enter");
    await expect(entities.getByText(first)).toBeVisible({ timeout: 60_000 });
    // INSPECT: the drawer reads relations and neighbors for the row.
    await entities.getByRole("button", { name: "Details" }).first().click();
    const drawer = page.getByRole("dialog");
    await expect(
      drawer.getByText("No relations yet. Link this entity with the form below."),
    ).toBeVisible({ timeout: 60_000 });
    await drawer.getByRole("button", { name: "Close" }).click();
    // LINK: both endpoints picked from the entity picker, never typed.
    // AntD repeats the option text in a visually hidden aria mirror, so
    // click the option element a user sees, inside the dropdown that is
    // neither hidden nor still leaving (the Scale smoke's own pattern).
    const openPicker = page.locator(
      ".ant-select-dropdown:not(.ant-select-dropdown-hidden):not(.ant-slide-up-leave)",
    );
    const pickOption = async (name: string): Promise<void> => {
      await openPicker.locator(".ant-select-item-option").filter({ hasText: name }).first().click();
    };
    const relation = page.locator(".ant-card").filter({ hasText: "Link two entities" });
    await relation.getByRole("combobox").first().click();
    await pickOption(first);
    await relation.getByRole("combobox").nth(1).click();
    await pickOption(second);
    await relation.getByRole("button", { name: "Link two entities" }).click();
    await expect(page.getByText("Relation created.")).toBeVisible({ timeout: 60_000 });
    // The relation read answers in the drawer of the source entity: the
    // row names source, relation, and target (the title also carries the
    // source name, so the bare name would match twice).
    await entities.getByRole("button", { name: "Details" }).first().click();
    await expect(drawer.getByText(`${first} related_to ${second}`)).toBeVisible({
      timeout: 60_000,
    });
  });

  test("the map draws the seeded graph read-only", async ({ page }) => {
    // Phase-2 Wave 6 done-when: a novice opens the Map from the rail
    // and SEES the graph — the workbench test's two entities render as
    // nodes, their relation as an edge — with the canvas read-only and
    // the same data in the tables under it.
    await page.goto("/app/#/setup");
    await page.getByRole("menuitem", { name: "Map" }).click();
    await expect(page).toHaveURL(/#\/map/);
    const canvas = page.locator(".react-flow");
    await expect(canvas).toBeVisible({ timeout: 60_000 });
    // The workbench test's run-unique entities sit in the node window
    // (name-ordered first by the aaa- prefix).
    await expect(canvas.locator(".react-flow__node", { hasText: second })).toBeVisible({
      timeout: 60_000,
    });
    // Read-only: no handles on the pane, so nothing offers a connect.
    await expect(canvas.locator(".react-flow__handle")).toHaveCount(0);
    // The canvas never carries the only path to data: the relations
    // table names the workbench test's relation row.
    await expect(
      page.getByRole("cell", { name: `${first} related_to ${second}` }),
    ).toBeVisible();
  });
});

test.describe("accessibility (axe-core)", () => {
  // The whole shell: the ask page scans its focusable citation list,
  // setup/review/codebook their dense tables and forms.
  for (const stage of ["#/ingest", "#/analyze", "#/release", "#/scale", "#/ask", "#/setup", "#/review", "#/codebook", "#/graph", "#/map"]) {
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

  test("no critical or serious violations with the drawer NLP panel open", async ({ page }) => {
    // The Wave-5 drawer path: the dialog, its tabs, the picker, and the
    // run controls scan with the panel open (the canvas never carries
    // the only path to data — the drawer is a dialog over the table).
    await page.goto("/app/#/review");
    const docs = page.locator(".ant-card").filter({ hasText: "Documents" });
    await expect(docs.getByRole("cell").first()).toBeVisible({ timeout: 60_000 });
    await docs.getByRole("button", { name: "Analyze" }).first().click();
    const drawer = page.getByRole("dialog");
    await expect(drawer.getByText("Analyze this document")).toBeVisible();
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
});
