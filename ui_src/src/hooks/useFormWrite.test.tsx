import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LabeledField from "../components/LabeledField";
import StatusBanner from "../components/StatusBanner";
import { strings } from "../strings";
import { calls, clearCalls, makeWrapper, respond, stubFetch } from "../test/testUtils";
import { useFormWrite } from "./useFormWrite";
import type { FormWrite } from "./useFormWrite";

/**
 * The one form-to-write seam: the body rides the op path, an ok answer
 * refreshes the row and shows the banner, a typed failure lands inline
 * under the field that owns it and takes focus. The probe form is the
 * drawer's add-tag shape (one field, one op, one refresh).
 */

// The latest settled seam state, reported outside render (the purity
// rule the other hook tests use).
let latest: FormWrite | null = null;
let lastSubmitOk: boolean | null = null;

function Harness({ onRefresh }: { onRefresh: () => void }) {
  const write = useFormWrite("tag-document", strings.reviewTagAdded, onRefresh);
  const [tag, setTag] = useState("");
  const { running, error, saved, rootRef, submit } = write;
  useEffect(() => {
    latest = write;
  });
  return (
    <div ref={rootRef}>
      <LabeledField
        label={strings.reviewTagAddLabel}
        value={tag}
        onChange={setTag}
        error={error ?? undefined}
      />
      <button
        onClick={() => {
          lastSubmitOk = null;
          void submit({ tag }, { pathParams: { doc_id: "d1" } }).then((ok) => {
            lastSubmitOk = ok;
          });
        }}
      >
        write
      </button>
      {saved !== null && <StatusBanner kind="ok">{saved}</StatusBanner>}
      <span hidden>{running ? "busy" : "idle"}</span>
    </div>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  clearCalls();
  latest = null;
  lastSubmitOk = null;
});

describe("useFormWrite", () => {
  it("sends the body, refreshes the row, and names the save on an ok write", async () => {
    const onRefresh = vi.fn();
    stubFetch([
      {
        method: "POST",
        path: "/api/documents/d1/tags",
        reply: respond(200, { status: "success", doc_id: "d1", tag: "energy" }),
      },
    ]);
    render(<Harness onRefresh={onRefresh} />, { wrapper: makeWrapper(false) });
    fireEvent.change(screen.getByLabelText(strings.reviewTagAddLabel), {
      target: { value: "energy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "write" }));
    await waitFor(() => expect(lastSubmitOk).toBe(true));
    const sent = calls.find((c) => c.method === "POST");
    expect(sent?.url).toContain("/api/documents/d1/tags");
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({ tag: "energy" });
    expect(onRefresh).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(strings.reviewTagAdded)).toBeTruthy();
    expect(latest?.error).toBeNull();
  });

  it("puts a typed failure inline and focuses the field that owns it", async () => {
    const onRefresh = vi.fn();
    stubFetch([
      {
        method: "POST",
        path: "/api/documents/d1/tags",
        reply: respond(404, {
          status: "error",
          error: "This document does not carry the tag 'energy'.",
          error_type: "not_found",
        }),
      },
    ]);
    render(<Harness onRefresh={onRefresh} />, { wrapper: makeWrapper(false) });
    fireEvent.change(screen.getByLabelText(strings.reviewTagAddLabel), {
      target: { value: "energy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "write" }));
    await waitFor(() => expect(lastSubmitOk).toBe(false));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(strings.reviewTagAdded)).toBeNull();
    expect(onRefresh).not.toHaveBeenCalled();
    // The focus contract: the inline error marks the field aria-invalid
    // and the first such control inside rootRef takes focus.
    await waitFor(() =>
      expect(document.activeElement?.getAttribute("aria-invalid")).toBe("true"),
    );
  });

  it("clear drops the banner and the inline error", async () => {
    stubFetch([
      {
        method: "POST",
        path: "/api/documents/d1/tags",
        reply: respond(200, { status: "success", doc_id: "d1", tag: "energy" }),
      },
    ]);
    render(<Harness onRefresh={() => undefined} />, { wrapper: makeWrapper(false) });
    fireEvent.change(screen.getByLabelText(strings.reviewTagAddLabel), {
      target: { value: "energy" },
    });
    fireEvent.click(screen.getByRole("button", { name: "write" }));
    expect(await screen.findByText(strings.reviewTagAdded)).toBeTruthy();
    latest?.clear();
    await waitFor(() => expect(screen.queryByText(strings.reviewTagAdded)).toBeNull());
    expect(latest?.saved).toBeNull();
    expect(latest?.error).toBeNull();
  });
});
