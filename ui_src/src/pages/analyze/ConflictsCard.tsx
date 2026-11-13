import { Button, Card, Select, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useState } from "react";
import { dateText } from "../../api/format";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings/analyze";
import ConflictsResolveForm from "./ConflictsResolveForm";
import { isConflictsEnvelope, isScanEnvelope, type ConflictRow } from "./conflictsShapes";

/**
 * The conflicts family in one card: the cascade scan, the flag list,
 * and the human resolve path. Conflict and resolved states carry the
 * madder error token (the rubric for what a person must settle); the
 * resolve write itself sits behind the gate in ConflictsResolveForm.
 */
export default function ConflictsCard() {
  const [verdictFilter, setVerdictFilter] = useState<string | null>(null);
  const [selected, setSelected] = useState<ConflictRow | null>(null);
  const scan = useOp("conflicts-scan");
  const list = useOp("conflicts-list");
  const { run, reset } = list;

  const load = useCallback((): void => {
    reset();
    const body = verdictFilter === null ? { limit: 50 } : { verdict: verdictFilter, limit: 50 };
    void run(body);
  }, [run, reset, verdictFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const columns: ColumnsType<ConflictRow> = [
    {
      title: strings.analyzeConflictsColumnPair,
      key: "pair",
      render: (_v, row) => (
        <span className="ui-row-id">
          {row.statement_a_id.slice(0, 8)} / {row.statement_b_id.slice(0, 8)}
        </span>
      ),
    },
    { title: strings.analyzeConflictsColumnAnchor, dataIndex: "anchor_name", key: "anchor_name" },
    {
      title: strings.analyzeConflictsColumnVerdict,
      dataIndex: "verdict",
      key: "verdict",
      render: (value: string) => (
        <Tag color={value === "conflict" ? "error" : undefined}>{value}</Tag>
      ),
    },
    {
      title: strings.analyzeConflictsColumnState,
      key: "state",
      render: (_v, row) =>
        row.resolved_verdict === null ? (
          <Tag>{strings.analyzeConflictsOpen}</Tag>
        ) : (
          <Tag color="error">
            {strings.analyzeConflictsResolved.replace("{verdict}", row.resolved_verdict)}
            {row.resolved_by === null
              ? ""
              : ` ${strings.analyzeConflictsByWord} ${row.resolved_by}`}
          </Tag>
        ),
    },
    {
      title: strings.analyzeConflictsColumnSeen,
      dataIndex: "created_at",
      key: "created_at",
      render: (value: string | null) => (
        <span className="ui-num">{value === null ? "" : dateText(value)}</span>
      ),
    },
    {
      title: strings.analyzeConflictsColumnActions,
      key: "actions",
      render: (_v, row) =>
        row.resolved_verdict === null ? (
          <Button size="small" danger onClick={() => setSelected(row)}>
            {strings.analyzeConflictsResolveButton}
          </Button>
        ) : null,
    },
  ];

  const result = list.result;
  const ok = result !== null && isConflictsEnvelope(result.data);
  return (
    <Card title={strings.analyzeConflictsCardTitle}>
      <Typography.Paragraph type="secondary" className="ui-flush">
        {strings.analyzeConflictsCardBody}
      </Typography.Paragraph>
      <div className="ui-actions">
        <Button
          type="primary"
          loading={scan.running}
          onClick={() => {
            void scan.run({});
          }}
        >
          {strings.analyzeConflictsScanButton}
        </Button>
        <ScanSummary scan={scan} />
      </div>
      {/* A failed scan always answers the user — the network arm too
          (the server-not-answering word), never a silent button. */}
      {scan.result !== null && !scan.result.ok && (
        <StatusBanner kind="error">{scan.result.message}</StatusBanner>
      )}
      <OpBody result={result} ok={ok} retry={load} opId="conflicts-list">
        {ok && result !== null && isConflictsEnvelope(result.data) ? (
          result.data.conflicts.length === 0 ? (
            <StatusBanner kind="info">{strings.analyzeConflictsEmpty}</StatusBanner>
          ) : (
            <div className="ui-stack">
              <div className="ui-actions">
                <Select
                  aria-label={strings.analyzeConflictsFilterLabel}
                  value={verdictFilter ?? "all"}
                  onChange={(v) => setVerdictFilter(v === "all" ? null : String(v))}
                  options={[
                    { value: "all", label: strings.analyzeConflictsFilterAll },
                    { value: "conflict", label: strings.analyzeConflictsFilterConflict },
                    { value: "uncertain", label: strings.analyzeConflictsFilterUncertain },
                  ]}
                />
              </div>
              <Table<ConflictRow>
                size="small"
                rowKey="conflict_id"
                pagination={false}
                dataSource={result.data.conflicts}
                columns={columns}
              />
            </div>
          )
        ) : null}
      </OpBody>
      {selected !== null && (
        <ConflictsResolveForm conflict={selected} refresh={load} onDone={() => setSelected(null)} />
      )}
    </Card>
  );
}

/** One scan summary line: pair counts plus one madder tag per flag. */
function ScanSummary({ scan }: { scan: ReturnType<typeof useOp> }) {
  const { result } = scan;
  if (result === null || !result.ok || !isScanEnvelope(result.data)) {
    return null;
  }
  const words = strings.analyzeConflictsScanSummary.replace(
    "{scanned}",
    String(result.data.scanned),
  );
  return (
    <span>
      {words}{" "}
      {result.data.flagged > 0 ? <Tag color="error">{result.data.flagged}</Tag> : null}
    </span>
  );
}
