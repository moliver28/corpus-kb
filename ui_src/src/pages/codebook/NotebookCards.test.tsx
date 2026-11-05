import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../../test/testUtils";
import { EvidenceCard, OverlapCard, UncodedCard } from "./NotebookCards";

/**
 * The notebook receipt cards: one receipt per manifest op, and the
 * success envelope alone earns the Done receipt.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

describe("NotebookCards", () => {
  it("renders the evidence receipt as Done, never not-success", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/notebook/evidence",
        reply: respond(200, {
          status: "success",
          result: { rows: [{ code: "energy_policy", unit: "u1" }] },
        }),
      },
    ]);
    render(<EvidenceCard />, { wrapper: makeWrapper(false) });
    fireEvent.change(
      screen.getByLabelText(strings.codebookEvidenceCodeLabel, { exact: false }),
      { target: { value: "energy_policy" } },
    );
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    expect(screen.queryByText(strings.receiptNotSuccess)).toBeNull();
    const sent = calls.find((c) => c.url.includes("/api/notebook/evidence"));
    expect(sent?.body).toContain('"code":"energy_policy"');
  });

  it("renders the uncoded-units receipt as Done", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/notebook/uncoded",
        reply: respond(200, { status: "success", result: { rank: [] } }),
      },
    ]);
    render(<UncodedCard />, { wrapper: makeWrapper(false) });
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/notebook/uncoded"));
    expect(sent?.method).toBe("POST");
  });

  it("renders the code-overlap receipt as Done", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/notebook/overlap",
        reply: respond(200, { status: "success", result: { pairs: [] } }),
      },
    ]);
    render(<OverlapCard />, { wrapper: makeWrapper(false) });
    fireEvent.click(screen.getByRole("button", { name: strings.runnerRunLabel }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/notebook/overlap"));
    expect(sent?.method).toBe("POST");
  });
});
