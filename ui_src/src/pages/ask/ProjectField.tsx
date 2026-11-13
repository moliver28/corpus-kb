import { Select, Typography } from "antd";
import type { ReactNode } from "react";
import { useEffect, useId } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import LabeledField from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/ask";
import { isProjectsEnvelope } from "./shapes";

/** The read window; a full window says more projects may exist. */
const PROJECT_CAP = 50;

/**
 * The notebook project picker: one Select fed by the projects-list op.
 * Loading shows the picker's spinner; an empty ledger shows an honest
 * banner; a failed or unreadable read shows the error panel and keeps
 * the hand-typed id field alive, so the ask never dead-ends. Duplicate
 * names gain a short id suffix, so two same-named projects stay apart.
 */
export default function ProjectField({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (id: string) => void;
  error: string;
}) {
  const projects = useOp("projects-list");
  const { run } = projects;
  const id = useId();
  const errorId = `${id}-error`;
  const invalid = error !== "";

  // One GET on mount. useOp's run holds a stable identity, so the
  // effect never re-fires.
  useEffect(() => {
    void run(undefined, { limit: String(PROJECT_CAP) });
  }, [run]);

  function retry(): void {
    // The retry asks for the same window as the mount read, so the
    // capped note can never sit over a larger list.
    projects.reset();
    void run(undefined, { limit: String(PROJECT_CAP) });
  }

  /** The typed-id path, only when the list itself failed. */
  function typedFallback(): ReactNode {
    return (
      <LabeledField
        label={strings.askNotebookProjectLabel}
        value={value}
        onChange={onChange}
        error={error}
        required
        machine
        name="project_id"
        widthClass="ui-narrow--short"
      />
    );
  }

  const result = projects.result;
  const drifted = result !== null && result.ok && !isProjectsEnvelope(result.data);
  if (result !== null && (!result.ok || drifted)) {
    const panel = drifted ? (
      <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
    ) : result.kind === "unavailable" ? (
      <UnavailablePanel result={result} onRetry={retry} />
    ) : (
      <ErrorPanel result={result} onRetry={retry} opId="projects-list" />
    );
    return (
      <>
        {panel}
        <StatusBanner kind="info">{strings.askProjectsFallback}</StatusBanner>
        {typedFallback()}
      </>
    );
  }

  const rows = result !== null && isProjectsEnvelope(result.data) ? result.data.result : [];
  // Same names collide in a picker; the short id suffix tells them apart.
  const nameCounts = new Map<string, number>();
  for (const row of rows) {
    nameCounts.set(row.name, (nameCounts.get(row.name) ?? 0) + 1);
  }
  const options = rows.map((row) => ({
    value: row.project_id,
    label:
      (nameCounts.get(row.name) ?? 0) > 1
        ? `${row.name} (${row.project_id.slice(0, 8)})`
        : row.name,
  }));
  const capped = rows.length >= PROJECT_CAP;
  let control: ReactNode;
  if (result === null) {
    control = <Select id={id} className="ui-narrow--short" loading options={[]} />;
  } else if (rows.length === 0) {
    control = (
      <>
        <StatusBanner kind="info">{strings.askProjectsEmpty}</StatusBanner>
        <Select id={id} className="ui-narrow--short" disabled options={[]} />
      </>
    );
  } else {
    control = (
      <Select<string | null>
        id={id}
        className="ui-narrow--short"
        value={value === "" ? null : value}
        onChange={(next) => {
          if (next !== null) {
            onChange(next);
          }
        }}
        options={options}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? errorId : undefined}
        aria-required="true"
      />
    );
  }

  return (
    <div className="ui-field">
      <label className="ui-field-label" htmlFor={id}>
        {strings.askNotebookProjectLabel} *
      </label>
      {control}
      {capped && (
        <Typography.Text type="secondary" className="ui-flush">
          {strings.askProjectsCapped}
        </Typography.Text>
      )}
      {invalid && (
        <div className="ui-field-error" id={errorId} role="alert">
          {error}
        </div>
      )}
    </div>
  );
}
