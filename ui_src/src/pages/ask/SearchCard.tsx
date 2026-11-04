import { Button, Card, Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useState } from "react";
import type { OpResult } from "../../api/types";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import LabeledField, { useFirstErrorFocus } from "../../components/LabeledField";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { excludedCount, isSearchEnvelope, scoreText, type SearchRow } from "./shapes";

const COLUMNS: ColumnsType<SearchRow> = [
  { title: strings.askColumnSource, dataIndex: "source", key: "source" },
  { title: strings.askColumnText, dataIndex: "text", key: "text" },
  {
    title: strings.askColumnScore,
    key: "score",
    render: (_value: unknown, row: SearchRow) => scoreText(row.score),
  },
];

type Mode = "text" | "similar" | "context";

const MODES: readonly Mode[] = ["text", "similar", "context"];

const MODE_LABELS: Record<Mode, string> = {
  text: strings.askSearchModeText,
  similar: strings.askSearchModeSimilar,
  context: strings.askSearchModeContext,
};

const OP_IDS: Record<Mode, string> = {
  text: "search",
  similar: "search-similar",
  context: "search-context",
};

/** One mode's result body: the same table for all three search routes. */
function resultBody(
  result: OpResult<unknown> | null,
  opts: { degraded: boolean; opId: string; retry: () => void },
): ReactNode {
  if (result === null) return null;
  if (result.kind === "unavailable") {
    return <UnavailablePanel result={result} onRetry={opts.retry} />;
  }
  if (result.ok && isSearchEnvelope(result.data)) {
    const rows = result.data.result;
    const excluded = excludedCount(result.data.degraded_excluded);
    return (
      <div className="ui-stack">
        {rows.length === 0 && <StatusBanner kind="warn">{strings.askSearchEmpty}</StatusBanner>}
        {opts.degraded && excluded > 0 && (
          <StatusBanner kind="warn">
            {strings.askDegradedExcluded.replace("{count}", String(excluded))}
          </StatusBanner>
        )}
        <Table<SearchRow>
          size="small"
          rowKey="chunk_id"
          pagination={false}
          dataSource={rows}
          columns={COLUMNS}
        />
      </div>
    );
  }
  if (result.ok) {
    return (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  }
  return <ErrorPanel result={result} onRetry={opts.retry} opId={opts.opId} />;
}

/**
 * The search tab: hybrid lexical + vector search, find-similar by chunk
 * id, and context search — three manifest routes, one result table. The
 * mode toggle follows the stage switcher pattern (plain aria-pressed
 * buttons); each mode keeps its own last result and the primary search
 * alone carries the degraded-excluded count its envelope returns.
 */
export default function SearchCard() {
  const [mode, setMode] = useState<Mode>("text");
  const [query, setQuery] = useState("");
  const [chunkId, setChunkId] = useState("");
  const [error, setError] = useState("");
  const { rootRef, flag } = useFirstErrorFocus();
  const text = useOp("search");
  const similar = useOp("search-similar");
  const context = useOp("search-context");

  function pick(next: Mode): void {
    setMode(next);
    setError("");
  }

  function validate(value: string, message: string): boolean {
    if (value === "") {
      setError(message);
      flag();
      return false;
    }
    setError("");
    return true;
  }

  async function run(): Promise<void> {
    if (mode === "similar") {
      const id = chunkId.trim();
      if (!validate(id, strings.askSearchChunkRequired)) return;
      similar.reset();
      await similar.run({ chunk_id: id });
      return;
    }
    const words = query.trim();
    if (!validate(words, strings.askQueryRequired)) return;
    if (mode === "context") {
      context.reset();
      await context.run({ query: words });
      return;
    }
    text.reset();
    await text.run({ query: words });
  }

  function retry(): void {
    void run();
  }

  const active = mode === "text" ? text : mode === "similar" ? similar : context;

  return (
    <Card title={strings.askTabSearch}>
      <div className="ui-stack" ref={rootRef}>
        <div className="ui-actions">
          {MODES.map((key) => (
            <Button
              key={key}
              aria-pressed={mode === key}
              type={mode === key ? "primary" : "default"}
              onClick={() => pick(key)}
            >
              {MODE_LABELS[key]}
            </Button>
          ))}
        </div>
        {mode === "similar" ? (
          <LabeledField
            label={strings.askSearchChunkLabel}
            value={chunkId}
            onChange={setChunkId}
            error={error}
            machine
            name="chunk_id"
            widthClass="ui-narrow"
            onPressEnter={() => void run()}
          />
        ) : (
          <LabeledField
            label={strings.askSearchQueryLabel}
            value={query}
            onChange={setQuery}
            error={error}
            name="query"
            widthClass="ui-narrow"
            onPressEnter={() => void run()}
          />
        )}
        <div className="ui-actions">
          <Button type="primary" loading={active.running} onClick={() => void run()}>
            {strings.askSearchAction}
          </Button>
        </div>
        {resultBody(active.result, {
          degraded: mode === "text",
          opId: OP_IDS[mode],
          retry,
        })}
      </div>
    </Card>
  );
}
