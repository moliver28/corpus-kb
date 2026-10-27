import { Button, Card, Input, Modal, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";

interface ProposalRow {
  proposed_id: number;
  suggested_label: string;
  n_members: number;
  status: string;
  block_reason: string | null;
}

interface ProposalsEnvelope {
  status: string;
  result: ProposalRow[];
}

function isProposalsEnvelope(value: unknown): value is ProposalsEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const rows = (value as Record<string, unknown>).result;
  return (
    Array.isArray(rows) &&
    rows.every(
      (row) =>
        typeof row === "object" &&
        row !== null &&
        typeof (row as Record<string, unknown>).proposed_id === "number",
    )
  );
}

/**
 * The promote gate (the ONE human promotion path): proposals in, one
 * typed name + definition per promotion, duplicate blocks shown with
 * the server's merge suggestion. The dialog stays shut until both
 * fields carry text.
 */
export default function PromoteCard() {
  const [promoting, setPromoting] = useState<ProposalRow | null>(null);
  const [name, setName] = useState("");
  const [definition, setDefinition] = useState("");
  const [warning, setWarning] = useState("");
  const [promoted, setPromoted] = useState(false);

  const proposals = useOp("codebook-proposals");
  const promote = useOp("codebook-promote");

  // One GET on mount through a ref (useOp's run is not stable).
  const runRef = useRef<() => Promise<unknown>>(proposals.run);
  useEffect(() => {
    void runRef.current();
  }, []);

  function refresh(): void {
    proposals.reset();
    void proposals.run();
  }

  async function confirm(): Promise<void> {
    if (promoting === null) return;
    if (name.trim() === "") {
      setWarning(strings.codebookPromoteNameRequired);
      return;
    }
    if (definition.trim() === "") {
      setWarning(strings.codebookPromoteDefinitionRequired);
      return;
    }
    promote.reset();
    const res = await promote.run({
      proposed_id: String(promoting.proposed_id),
      name: name.trim(),
      definition: definition.trim(),
    });
    // Any settled answer closes the dialog (a failure renders in the
    // card, never trapped behind the modal).
    setPromoting(null);
    if (res.ok) {
      // The false-success trap: duplicate_blocked is also res.ok, and
      // only a promoted receipt may claim the success line.
      const data = res.data as Record<string, unknown> | null;
      setPromoted(data !== null && data.status === "promoted");
      refresh();
    }
  }

  function open(row: ProposalRow): void {
    // Both fields are typed by the person; nothing is pre-seeded.
    setPromoting(row);
    setName("");
    setDefinition("");
    setWarning("");
  }

  const columns: ColumnsType<ProposalRow> = [
    { title: strings.codebookColumnLabel, dataIndex: "suggested_label", key: "label" },
    { title: strings.codebookColumnUnits, dataIndex: "n_members", key: "members", width: 70 },
    {
      title: strings.codebookPromoteAction,
      key: "actions",
      render: (_value: unknown, row: ProposalRow) => (
        <Button size="small" onClick={() => open(row)}>
          {strings.codebookPromoteAction}
        </Button>
      ),
    },
  ];

  let body: ReactNode = null;
  const result = proposals.result;
  if (result !== null && result.kind === "unavailable") {
    body = <UnavailablePanel result={result} onRetry={refresh} />;
  } else if (result !== null && result.ok && isProposalsEnvelope(result.data)) {
    const rows = result.data.result;
    body = (
      <div className="ui-stack">
        {promoted && <StatusBanner kind="ok">{strings.codebookPromoteDone}</StatusBanner>}
        {promote.result !== null &&
          promote.result.ok &&
          promote.result.data !== null &&
          typeof promote.result.data === "object" &&
          (promote.result.data as Record<string, unknown>).status === "duplicate_blocked" && (
            <div className="ui-stack">
              <StatusBanner kind="warn">{strings.codebookPromoteDuplicate}</StatusBanner>
              <JsonViewer value={promote.result.data} />
            </div>
          )}
        <Table<ProposalRow>
          size="small"
          rowKey="proposed_id"
          pagination={false}
          dataSource={rows}
          columns={columns}
          locale={{ emptyText: strings.codebookProposalsNone }}
        />
        {promote.result !== null && !promote.result.ok && (
          <ErrorPanel result={promote.result} onRetry={refresh} opId="codebook-promote" />
        )}
      </div>
    );
  } else if (result !== null && result.ok) {
    body = (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  } else if (result !== null) {
    body = <ErrorPanel result={result} onRetry={refresh} opId="codebook-proposals" />;
  }

  return (
    <Card title={strings.codebookPromoteTitle}>
      <Typography.Paragraph type="secondary">{strings.codebookPromoteBody}</Typography.Paragraph>
      {result === null && (
        <StatusBanner kind="info">{strings.codebookProposalsNone}</StatusBanner>
      )}
      {body}
      <Modal
        title={strings.codebookPromoteModalTitle}
        open={promoting !== null}
        onOk={() => void confirm()}
        onCancel={() => setPromoting(null)}
        okText={strings.codebookPromoteAction}
        cancelText={strings.confirmCancel}
      >
        <div className="ui-stack">
          <Typography.Text>{strings.codebookPromoteNameLabel}:</Typography.Text>
          <Input
            aria-label={strings.codebookPromoteNameLabel}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <Typography.Text>{strings.codebookPromoteDefinitionLabel}:</Typography.Text>
          <Input.TextArea
            aria-label={strings.codebookPromoteDefinitionLabel}
            value={definition}
            onChange={(event) => setDefinition(event.target.value)}
            rows={3}
          />
          {warning !== "" && <StatusBanner kind="warn">{warning}</StatusBanner>}
        </div>
      </Modal>
    </Card>
  );
}
