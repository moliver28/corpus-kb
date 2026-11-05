import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { strings } from "../../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../../test/testUtils";
import UploadCard from "./UploadCard";

/**
 * The upload card's own rows: the empty submit names the gap, the
 * picked file rides the multipart route to its receipt, the over-cap
 * file never leaves the browser, and a failed write lands on the error
 * panel. (The client rows for sendMultipart live in api/client.test.ts.)
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
});

/** Drop one fake file into the card's hidden file input. */
function pickFile(name: string, sizeOverride?: number): void {
  const file = new File(["content"], name, { type: "text/plain" });
  if (sizeOverride !== undefined) {
    Object.defineProperty(file, "size", { value: sizeOverride });
  }
  const input = document.querySelector('input[type="file"]');
  if (input === null) {
    throw new Error("no file input rendered");
  }
  Object.defineProperty(input, "files", { value: [file] });
  fireEvent.change(input);
}

describe("UploadCard", () => {
  it("answers an empty submit with the hint, never a dead button", async () => {
    stubFetch([]);
    render(<UploadCard />, { wrapper: makeWrapper(false) });
    const upload = await screen.findByRole("button", { name: strings.setupUploadAction });
    // Enabled like every other form; the empty submit names the gap.
    expect((upload as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(upload);
    expect(await screen.findByText(strings.setupUploadPickFirst)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/ingest/upload"))).toBe(false);
  });

  it("uploads the picked file and shows the ingest receipt", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/ingest/upload",
        reply: respond(200, { status: "success", result: { chunks: 4 } }),
      },
    ]);
    render(<UploadCard />, { wrapper: makeWrapper(false) });
    await screen.findByRole("button", { name: strings.setupUploadAction });
    pickFile("notes.txt");
    fireEvent.click(screen.getByRole("button", { name: strings.setupUploadAction }));
    expect(await screen.findByText(strings.receiptDone)).toBeTruthy();
    const sent = calls.find((c) => c.url.includes("/api/ingest/upload"));
    expect(sent?.method).toBe("POST");
    expect(sent?.body.length).toBeGreaterThan(0);
  });

  it("refuses an over-cap file with the exact hint and no call", async () => {
    stubFetch([]);
    render(<UploadCard />, { wrapper: makeWrapper(false) });
    await screen.findByRole("button", { name: strings.setupUploadAction });
    pickFile("big.bin", 51 * 1024 * 1024 + 1);
    fireEvent.click(screen.getByRole("button", { name: strings.setupUploadAction }));
    expect(
      await screen.findByText(strings.setupUploadTooLarge.replace("{mb}", "50")),
    ).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/api/ingest/upload"))).toBe(false);
  });

  it("lands a failed upload on the error panel with its message", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/ingest/upload",
        reply: respond(500, { status: "error", message: "The write failed." }),
      },
    ]);
    render(<UploadCard />, { wrapper: makeWrapper(false) });
    await screen.findByRole("button", { name: strings.setupUploadAction });
    pickFile("notes.txt");
    fireEvent.click(screen.getByRole("button", { name: strings.setupUploadAction }));
    expect(await screen.findByText(strings.errorTitle)).toBeTruthy();
    expect(await screen.findByText("The write failed.")).toBeTruthy();
  });
});
