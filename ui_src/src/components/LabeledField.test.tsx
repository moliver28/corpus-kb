import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useState } from "react";
import LabeledField, { useFirstErrorFocus } from "./LabeledField";

/** One field plus a submit that fails: the focus-first-error harness. */
function Harness() {
  const [error, setError] = useState<string | undefined>(undefined);
  const { rootRef, flag } = useFirstErrorFocus();
  return (
    <div ref={rootRef}>
      <LabeledField
        label="Server path"
        value="x"
        onChange={() => {}}
        error={error}
        machine
        name="path"
      />
      <button
        type="button"
        onClick={() => {
          setError("Enter a path first.");
          flag();
        }}
      >
        submit
      </button>
    </div>
  );
}

afterEach(cleanup);

describe("LabeledField", () => {
  it("binds the visible label to the input and carries the machine attributes", () => {
    render(<LabeledField label="Server path" value="" onChange={() => {}} machine name="path" />);
    const input = screen.getByPlaceholderText("Server path");
    const id = input.getAttribute("id");
    expect(id).toBeTruthy();
    expect(document.querySelector(`label[for="${id}"]`)?.textContent).toBe("Server path");
    expect(input.getAttribute("spellcheck")).toBe("false");
    expect(input.getAttribute("autocomplete")).toBe("off");
    expect(input.getAttribute("name")).toBe("path");
  });

  it("renders the error inline with role alert and flags the field", () => {
    render(<Harness />);
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "submit" }));
    const input = screen.getByPlaceholderText("Server path");
    expect(screen.getByRole("alert").textContent).toBe("Enter a path first.");
    expect(input.getAttribute("aria-invalid")).toBe("true");
    expect(input.getAttribute("aria-describedby")).toBe(
      screen.getByRole("alert").getAttribute("id"),
    );
  });

  it("moves focus to the first errored field on a failed submit", () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole("button", { name: "submit" }));
    expect(document.activeElement).toBe(screen.getByPlaceholderText("Server path"));
  });
});
