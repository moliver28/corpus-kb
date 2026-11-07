import { Select, Typography } from "antd";
import { useEffect, useId } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isEntitiesEnvelope } from "./shapes";

/** The picker window; a full window says more entities may exist. */
const ENTITY_CAP = 50;

/**
 * The relation form's entity picker: one Select fed by the list-entities
 * op. The endpoints are picked, never typed — a relation between wrong
 * ids is worse than a blocked form. A failed or unreadable read shows
 * the honest panel and a retry; the form stays blocked until the list
 * answers (no hand-typed UUID fallback, per the workbench rule).
 */
export default function EntityPicker({
  label,
  value,
  onChange,
  error,
  excludeId,
  reloadKey = 0,
}: {
  label: string;
  value: string;
  onChange: (id: string) => void;
  error: string;
  /** One entity the picker never offers (the other picked endpoint). */
  excludeId?: string;
  /** Bump to re-read the list after a write lands elsewhere. */
  reloadKey?: number;
}) {
  const entities = useOp("list-entities");
  const { run } = entities;
  const id = useId();
  const errorId = `${id}-error`;
  const invalid = error !== "";

  // One GET on mount, one more per reload bump (a fresh write makes a
  // new entity pickable); useOp's run holds a stable identity.
  useEffect(() => {
    void run(undefined, { limit: String(ENTITY_CAP), offset: "0" });
  }, [run, reloadKey]);

  function retry(): void {
    entities.reset();
    void run(undefined, { limit: String(ENTITY_CAP), offset: "0" });
  }

  const result = entities.result;
  const drifted = result !== null && result.ok && !isEntitiesEnvelope(result.data);
  if (result !== null && (!result.ok || drifted)) {
    const panel =
      drifted ? (
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
      ) : result.kind === "unavailable" ? (
        <UnavailablePanel result={result} onRetry={retry} />
      ) : (
        <ErrorPanel result={result} onRetry={retry} opId="list-entities" />
      );
    return panel;
  }

  const rows = result !== null && isEntitiesEnvelope(result.data) ? result.data.result : [];
  const options = rows
    .filter((row) => row.entity_id !== excludeId)
    .map((row) => ({
      value: row.entity_id,
      label: `${row.name} (${row.entity_type})`,
    }));
  const capped = rows.length >= ENTITY_CAP;

  return (
    <div className="ui-field">
      <label className="ui-field-label" htmlFor={id}>
        {label} *
      </label>
      {result === null ? (
        <Select id={id} className="ui-narrow" loading options={[]} />
      ) : rows.length === 0 ? (
        <>
          <StatusBanner kind="info">{strings.graphPickerEmpty}</StatusBanner>
          <Select id={id} className="ui-narrow" disabled options={[]} />
        </>
      ) : (
        <Select<string | null>
          id={id}
          className="ui-narrow"
          showSearch
          optionFilterProp="label"
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
      )}
      {capped && (
        <Typography.Text type="secondary" className="ui-flush">
          {strings.graphPickerCapped}
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
