import { Tag } from "antd";
import { useCallback, useEffect, type ReactNode } from "react";
import OpBody from "./OpBody";
import { useOp } from "../hooks/useOp";
import { strings } from "../strings/app";
import {
  isDocumentStatsEnvelope,
  isStatsEnvelope,
  type SourceTypeRow,
} from "./statsShapes";

/** The count keys in the handler's own order, with their plain words. */
const COUNT_WORDS: ReadonlyArray<[string, string]> = [
  ["documents", strings.statsWordDocuments],
  ["chunks", strings.statsWordChunks],
  ["chunks_vectors", strings.statsWordVectors],
  ["entities", strings.statsWordEntities],
  ["relations", strings.statsWordRelations],
  ["tags", strings.statsWordTags],
  ["total_events", strings.statsWordEvents],
];

/**
 * The corpus stats strip: the two Wave 3 stats ops as badge rows, fed
 * into the Setup and Review health areas. No card chrome of its own —
 * one line of count tags plus one line of per-source-type tags. A read
 * that fails or drifts shows its honest state, never a fake zero.
 */
export default function StatsStrip() {
  const stats = useOp("stats");
  const docStats = useOp("document-stats");
  const { run: runStats, reset: resetStats } = stats;
  const { run: runDocs, reset: resetDocs } = docStats;

  const loadStats = useCallback((): void => {
    resetStats();
    void runStats();
  }, [resetStats, runStats]);
  const loadDocs = useCallback((): void => {
    resetDocs();
    void runDocs();
  }, [resetDocs, runDocs]);

  useEffect(() => {
    loadStats();
    loadDocs();
  }, [loadStats, loadDocs]);

  const statsResult = stats.result;
  const statsOk = statsResult !== null && isStatsEnvelope(statsResult.data);
  const docsResult = docStats.result;
  const docsOk = docsResult !== null && isDocumentStatsEnvelope(docsResult.data);

  const rows: Array<[string, string]> = [];
  if (statsOk && statsResult !== null && isStatsEnvelope(statsResult.data)) {
    const counts = statsResult.data.stats;
    for (const [key, word] of COUNT_WORDS) {
      if (key in counts) {
        rows.push([word, String(counts[key])]);
      }
    }
  }

  return (
    <div className="ui-stack">
      <OpBody result={statsResult} ok={statsOk} retry={loadStats} opId="stats" skeletonRows={1}>
        {statsOk ? (
          <div className="ui-actions">
            {rows.map(([word, count]) => (
              <Tag key={word}>
                {word} <span className="ui-num">{count}</span>
              </Tag>
            ))}
          </div>
        ) : null}
      </OpBody>
      <OpBody
        result={docsResult}
        ok={docsOk}
        retry={loadDocs}
        opId="document-stats"
        skeletonRows={1}
      >
        {docsOk && docsResult !== null && isDocumentStatsEnvelope(docsResult.data)
          ? renderSourceRows(docsResult.data.by_source_type)
          : null}
      </OpBody>
    </div>
  );
}

/** One tag per source type; the row drops out when there are none. */
function renderSourceRows(rows: SourceTypeRow[]): ReactNode {
  if (rows.length === 0) {
    return null;
  }
  return (
    <div className="ui-actions">
      {rows.map((row) => (
        <Tag key={row.source_type}>
          {row.source_type} <span className="ui-num">{row.count}</span>
        </Tag>
      ))}
    </div>
  );
}
