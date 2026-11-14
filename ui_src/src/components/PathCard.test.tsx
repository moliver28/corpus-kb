import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../strings";
import { clearCalls, calls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import type { MinimalResponse } from "../test/testUtils";
import PathCard from "./PathCard";

/**
 * PathCard rows (spec T2): Enter in the path field can never start a
 * long op by itself — it opens the same confirm gate as the button.
 * Short ops keep their direct Enter run.
 */

const NEVER: MinimalResponse = {
  status: 200,
  text: () => new Promise<string>(() => {}),
};

afterEach(() => {
  cleanup();
  clearCalls();
  vi.unstubAllGlobals();
});

function renderUrlCard(): void {
  render(
    <PathCard
      title="Ingest a website"
      label={strings.ingestUrlLabel}
      requiredMessage={strings.ingestUrlRequired}
      pathKey="url"
      opId="ingest-url"
    />,
    { wrapper: makeWrapper(false) },
  );
}

describe("PathCard", () => {
  it("routes Enter through the confirm gate: no fetch before confirm", async () => {
    stubFetch([{ method: "POST", path: "/api/ingest/url", reply: NEVER }]);
    renderUrlCard();
    const input = screen.getByPlaceholderText(strings.ingestUrlLabel);
    fireEvent.change(input, { target: { value: "https://example.com" } });

    fireEvent.keyDown(input, { key: "Enter" });
    // The gate is open and no op started.
    expect(await screen.findByText(strings.confirmLongTitle)).toBeTruthy();
    expect(calls).toHaveLength(0);

    // Confirm is the only way to start the op.
    fireEvent.click(screen.getByRole("button", { name: strings.confirmConfirm }));
    await screen.findByText(/Running for \d+ seconds\./);
    expect(calls.some((c) => c.method === "POST" && c.url.includes("/api/ingest/url"))).toBe(
      true,
    );
  });

  it("Enter with an empty path shows the hint and never opens the gate", async () => {
    stubFetch([{ method: "POST", path: "/api/ingest/url", reply: NEVER }]);
    renderUrlCard();
    fireEvent.keyDown(screen.getByPlaceholderText(strings.ingestUrlLabel), { key: "Enter" });
    expect(screen.getByText(strings.ingestUrlRequired)).toBeTruthy();
    expect(screen.queryByText(strings.confirmLongTitle)).toBeNull();
    expect(calls).toHaveLength(0);
  });

  it("keeps the direct Enter run for a short op", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/research/ingest-transcript",
        reply: respond(200, { status: "success", message: "queued" }),
      },
    ]);
    render(
      <PathCard
        title="Transcript"
        label={strings.ingestTranscriptPathLabel}
        requiredMessage={strings.ingestTranscriptPathRequired}
        pathKey="path"
        opId="research-ingest-transcript"
      />,
      { wrapper: makeWrapper(false) },
    );
    const input = screen.getByPlaceholderText(strings.ingestTranscriptPathLabel);
    fireEvent.change(input, { target: { value: "/data/t.txt" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await screen.findByText(strings.receiptDone);
    expect(calls.some((c) => c.url.includes("/api/research/ingest-transcript"))).toBe(true);
    expect(screen.queryByText(strings.confirmLongTitle)).toBeNull();
  });
});
