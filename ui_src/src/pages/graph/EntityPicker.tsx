import { Select, Typography } from "antd";
import { useEffect, useId, useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isEntitiesEnvelope } from "./shapes";

/** The picker window; a full window says more entities may exist. */
const ENTITY_CAP = 50;
/** Wait between the last keystroke and the server search. */
const SEARCH_DEBOUNCE_MS = 250;

/**
 * The relation form's entity picker: one Select fed by the list-entities
 * first window, with the server search (search-graph) behind it for
 * anything past that window (the Wave-4 review's P2-3). The endpoints
 * are picked, never typed — a relation between wrong ids is worse than
 * a blocked form. A failed or unreadable read shows the honest panel
 * and a retry; the form stays blocked until the list answers (no
 * hand-typed UUID fallback, per the workbench rule).
 *
 * Every read carries one row past the cap, so the cap note is the
 * 51st-row sentinel, never a guess (P3-6). The search answer lands
 * last-wins, the same posture the server-window filter seam holds.
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
  const search = useOp("search-graph");
  const { run } = entities;
  const { run: runSearch, running: searching } = search;
  const id = useId();
  const errorId = `${id}-error`;
  const invalid = error !== "";
  // The typed search text; empty means the first-window list.
  const [query, setQuery] = useState("");

  // One GET on mount, one more per reload bump (a fresh write makes a
  // new entity pickable); useOp's run holds a stable identity.
  useEffect(() => {
    void run(undefined, { limit: String(ENTITY_CAP + 1), offset: "0" });
  }, [run, reloadKey]);

  // The server search rides the keystrokes, debounced; clearing the box
  // is the first-window list again.
  useEffect(() => {
    if (query === "") {
      return;
    }
    const timer = window.setTimeout(() => {
      void runSearch({ query, limit: String(ENTITY_CAP + 1) });
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query, runSearch]);

  function retry(): void {
    entities.reset();
    void run(undefined, { limit: String(ENTITY_CAP + 1), offset: "0" });
  }

  const result = entities.result;
  const drifted = result !== null && result.ok && !isEntitiesEnvelope(result.data);
  if (result !== null && (!result.ok || drifted)) {
    // The error panel keeps the field label (the Wave-4 review's P2-4):
    // the failed read never leaves an unlabeled control behind.
    return (
      <div className="ui-field">
        <label className="ui-field-label" htmlFor={id}>
          {label} *
        </label>
        {drifted ? (
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        ) : result.kind === "unavailable" ? (
          <UnavailablePanel result={result} onRetry={retry} />
        ) : (
          <ErrorPanel result={result} onRetry={retry} opId="list-entities" />
        )}
        {invalid && (
          <div className="ui-field-error" id={errorId} role="alert">
            {error}
          </div>
        )}
      </div>
    );
  }

  const windowRows =
    result !== null && isEntitiesEnvelope(result.data) ? result.data.result : [];
  const searchResult = search.result;
  const searchShown = query !== "";
  const searchDrifted =
    searchResult !== null && searchResult.ok && !isEntitiesEnvelope(searchResult.data);
  const searchOkRows =
    searchResult !== null && searchResult.ok && isEntitiesEnvelope(searchResult.data)
      ? searchResult.data.result
      : [];
  // Search mode shows the search answer (empty until one lands); a
  // failed search says so and falls back to the window rows.
  const searchProblem =
    searchShown && searchResult !== null && (!searchResult.ok || searchDrifted)
      ? searchResult.ok
        ? strings.unreadableResponse
        : searchResult.message !== ""
          ? searchResult.message
          : searchResult.status
      : null;
  const fullRows = searchShown && searchProblem === null ? searchOkRows : windowRows;
  const overCap = fullRows.length > ENTITY_CAP;
  const rows = fullRows.slice(0, ENTITY_CAP);
  const options = rows
    .filter((row) => row.entity_id !== excludeId)
    .map((row) => ({
      value: row.entity_id,
      label: `${row.name} (${row.entity_type})`,
    }));

  return (
    <div className="ui-field">
      <label className="ui-field-label" htmlFor={id}>
        {label} *
      </label>
      {searchProblem !== null && <StatusBanner kind="warn">{searchProblem}</StatusBanner>}
      {result === null ? (
        <Select id={id} className="ui-narrow" loading options={[]} />
      ) : !searchShown && rows.length === 0 ? (
        <>
          <StatusBanner kind="info">{strings.graphPickerEmpty}</StatusBanner>
          <Select id={id} className="ui-narrow" disabled options={[]} />
        </>
      ) : (
        <Select<string | null>
          id={id}
          className="ui-narrow"
          showSearch
          filterOption={searchShown ? false : undefined}
          optionFilterProp="label"
          loading={searchShown && searching}
          notFoundContent={searchShown ? strings.graphPickerNoMatch : undefined}
          value={value === "" ? null : value}
          onSearch={(text) => setQuery(text.trim())}
          onChange={(next) => {
            if (next !== null) {
              onChange(next);
              setQuery("");
            }
          }}
          options={options}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          aria-required="true"
        />
      )}
      {overCap && !searchShown && (
        <Typography.Text type="secondary" className="ui-flush">
          {strings.graphPickerCapped}
        </Typography.Text>
      )}
      {overCap && searchShown && (
        <Typography.Text type="secondary" className="ui-flush">
          {strings.graphPickerSearchCapped}
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
