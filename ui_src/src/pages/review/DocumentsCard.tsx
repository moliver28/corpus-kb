import { Button, Card } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router";
import ErrorPanel from "../../components/ErrorPanel";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { useServerWindow } from "../../hooks/useServerWindow";
import { strings } from "../../strings";
import DocumentDetailDrawer from "./DocumentDetailDrawer";
import DocumentsTable from "./DocumentsTable";
import { isDocumentsEnvelope } from "./shapes";
import type { DocumentsEnvelope, DocumentRow } from "./shapes";

/** Rows per server window; one extra fetched row answers "has next". */
const PAGE_SIZE = 50;

/**
 * The corpus document list: the documents-list window table, the
 * per-document tag/metadata drawer, and the delete gate. The window
 * state, the limit+1 fetch, and the pager live in the shared
 * server-window seam (hooks/useServerWindow), not in this card.
 */
export default function DocumentsCard() {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<DocumentRow | null>(null);
  const [deleteRow, setDeleteRow] = useState<DocumentRow | null>(null);
  const [deleted, setDeleted] = useState(false);
  const win = useServerWindow<DocumentRow, DocumentsEnvelope>(
    "documents-list",
    PAGE_SIZE,
    isDocumentsEnvelope,
  );
  const remove = useOp("document-delete");
  const { run: runDelete, reset: resetDelete } = remove;

  async function removeRow(row: DocumentRow): Promise<void> {
    resetDelete();
    setDeleteRow(row);
    const res = await runDelete(undefined, undefined, { doc_id: row.doc_id });
    if (!res.ok) {
      return;
    }
    setDeleted(true);
    // The deleted row leaves its window; the seam steps back when it was
    // the window's only row, so the pager never rests on an empty page.
    win.stepBackAfterDelete(win.rows.length);
  }

  const body = (
    <OpBody result={win.result} ok={win.okEnvelope} retry={win.refresh} opId="documents-list">
      {win.rows.length === 0 && win.page > 1 ? (
        // The window emptied under us (deletes elsewhere); the first
        // page is the truth, one click away.
        <div className="ui-stack">
          <StatusBanner kind="info">{strings.reviewDocsEmpty}</StatusBanner>
          <div className="ui-actions">
            <Button onClick={() => win.goto(1)}>{strings.reviewDocsBackToFirst}</Button>
          </div>
        </div>
      ) : win.rows.length === 0 ? (
        <div className="ui-stack">
          <StatusBanner kind="info">{strings.reviewDocsEmpty}</StatusBanner>
          <div className="ui-actions">
            <Button type="primary" onClick={() => navigate("/ingest")}>
              {strings.reviewDocsIngest}
            </Button>
          </div>
        </div>
      ) : (
        <DocumentsTable
          rows={win.rows}
          page={win.page}
          pageSize={win.windowSize}
          hasMore={win.hasMore}
          onPage={win.goto}
          onDetails={setDetail}
          onDelete={(row) => void removeRow(row)}
        />
      )}
    </OpBody>
  );

  return (
    <Card title={strings.reviewDocsTitle}>
      <div className="ui-stack">
        {deleted && <StatusBanner kind="ok">{strings.reviewDocsDeleted}</StatusBanner>}
        {remove.result !== null && !remove.result.ok && deleteRow !== null && (
          <ErrorPanel
            result={remove.result}
            onRetry={() => void removeRow(deleteRow)}
            opId="document-delete"
          />
        )}
        {body}
      </div>
      <DocumentDetailDrawer row={detail} onClose={() => setDetail(null)} />
    </Card>
  );
}
