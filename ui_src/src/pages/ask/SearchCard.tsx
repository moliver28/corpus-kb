import { Button, Card, Input, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import { isSearchEnvelope, excludedCount, scoreText, type SearchRow } from "./shapes";

const COLUMNS: ColumnsType<SearchRow> = [
  { title: strings.askColumnSource, dataIndex: "source", key: "source" },
  { title: strings.askColumnText, dataIndex: "text", key: "text" },
  {
    title: strings.askColumnScore,
    key: "score",
    render: (_value: unknown, row: SearchRow) => scoreText(row.score),
  },
];

/**
 * The search tab: hybrid lexical + vector search through the one search
 * op, with the honest degraded-excluded count when an index is impaired.
 */
export default function SearchCard() {
  const [query, setQuery] = useState("");
  const [hint, setHint] = useState("");
  const search = useOp("search");

  async function run(): Promise<void> {
    if (query.trim() === "") {
      setHint(strings.askQueryRequired);
      return;
    }
    setHint("");
    search.reset();
    await search.run({ query: query.trim() });
  }

  let body: ReactNode = null;
  const result = search.result;
  if (result !== null) {
    if (result.kind === "unavailable") {
      body = <UnavailablePanel result={result} onRetry={() => void run()} />;
    } else if (result.ok && isSearchEnvelope(result.data)) {
      const env = result.data;
      const rows = env.result;
      body = (
        <div className="ui-stack">
          {rows.length === 0 && <StatusBanner kind="warn">{strings.askSearchEmpty}</StatusBanner>}
          {excludedCount(env.degraded_excluded) > 0 && (
            <StatusBanner kind="warn">
              {strings.askDegradedExcluded.replace(
                "{count}",
                String(excludedCount(env.degraded_excluded)),
              )}
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
    } else if (result.ok) {
      body = (
        <div>
          <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
          <JsonViewer value={result.data} />
        </div>
      );
    } else {
      body = <ErrorPanel result={result} onRetry={() => void run()} />;
    }
  }

  return (
    <Card title={strings.askTabSearch}>
      <Typography.Paragraph type="secondary">
        {strings.askSearchQueryLabel}:
      </Typography.Paragraph>
      <div className="ui-actions">
        <Input
          style={{ maxWidth: 480 }}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={strings.askSearchQueryLabel}
          onPressEnter={() => void run()}
        />
        <Button type="primary" loading={search.running} onClick={() => void run()}>
          {strings.askSearchAction}
        </Button>
      </div>
      {hint !== "" && <StatusBanner kind="warn">{hint}</StatusBanner>}
      {body}
    </Card>
  );
}
