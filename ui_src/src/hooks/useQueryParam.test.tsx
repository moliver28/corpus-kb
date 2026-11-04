import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter, useLocation } from "react-router";
import { useQueryParam } from "./useQueryParam";

// The one query helper: absent and unknown values read as null (the
// page keeps its default), a known value reads through, and writes go
// to the hash query in place. Writing null removes the key, and so
// does writing the fallback value — the URL carries only non-default
// state.

const KEYS = ["files", "queue"] as const;

let seen: string | null = null;
let search = "";

/** A probe that mirrors the hook's read and reports the location query. */
function Harness({ fallback }: { fallback?: string }) {
  const [value, setValue] = useQueryParam("tab", KEYS, fallback);
  const locationSearch = useLocation().search;
  useEffect(() => {
    seen = value;
    search = locationSearch;
  });
  return (
    <div>
      <button onClick={() => setValue("queue")}>pick</button>
      <button onClick={() => setValue("files")}>reset</button>
      <button onClick={() => setValue(null)}>clear</button>
    </div>
  );
}

function renderAt(entry: string, fallback?: string): void {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Harness fallback={fallback} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  seen = null;
  search = "";
});

describe("useQueryParam", () => {
  it("reads a known value and falls back to null on an unknown one", () => {
    renderAt("/ingest?tab=queue");
    expect(seen).toBe("queue");
    cleanup();
    renderAt("/ingest?tab=bogus");
    expect(seen).toBeNull();
    cleanup();
    renderAt("/ingest?other=1");
    expect(seen).toBeNull();
  });

  it("writes the value into the query and removes it on null", () => {
    renderAt("/ingest");
    fireEvent.click(screen.getByRole("button", { name: "pick" }));
    expect(search).toBe("?tab=queue");
    expect(seen).toBe("queue");
    fireEvent.click(screen.getByRole("button", { name: "clear" }));
    expect(search).toBe("");
    expect(seen).toBeNull();
  });

  it("treats the fallback value as no state and clears it", () => {
    renderAt("/ingest", "files");
    fireEvent.click(screen.getByRole("button", { name: "pick" }));
    expect(search).toBe("?tab=queue");
    fireEvent.click(screen.getByRole("button", { name: "reset" }));
    expect(search).toBe("");
    expect(seen).toBeNull();
  });
});
