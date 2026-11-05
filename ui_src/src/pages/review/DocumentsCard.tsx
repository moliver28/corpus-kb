import { Button, Card } from "antd";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router";
import ErrorPanel from "../../components/ErrorPanel";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import DocumentDetailDrawer from "./DocumentDetailDrawer";
import DocumentsTable from "./DocumentsTable";
import { isDocumentsEnvelope } from "./shapes";
import type { DocumentRow } from "./shapes";

/** Rows per server window; one extra fetched row answers "has next". */
const PAGE_SIZE = 50;

/**
 * The corpus document list: the documents-list window table, the
 * per-document tag/metadata drawer, and the delete gate. The list read
 * answers limit/offset windows (no total), so the pager size is the
 * window size and one extra row marks the next page.
 */
export default function DocumentsCard() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState<DocumentRow | null>(null);
  const [deleteRow, setDeleteRow] = useState<DocumentRow | null>(null);
  const [deleted, setDeleted] = useState(false);
  const list = useOp("documents-list");
  const remove = useOp("document-delete");
  const { run: runList } = list;
  const { run: runDelete, reset: resetDelete } = remove;

  const loadPage = useCallback(
    (next: number): void => {
      void runList(undefined, {
        limit: String(PAGE_SIZE + 1),
        offset: String((next - 1) * PAGE_SIZE),
      });
    },
    [runList],
  );

  useEffect(() => {
    loadPage(page);
  }, [loadPage, page]);

  function refresh(): void {
    list.reset();
    loadPage(page);
  }

  async function removeRow(row: DocumentRow): Promise<void> {
    resetDelete();
    setDeleteRow(row);
    const res = await runDelete(undefined, undefined, { doc_id: row.doc_id });
    if (!res.ok) {
      return;
    }
    setDeleted(true);
    // The deleted row leaves its window; step back when it was the
    // window's only row so the pager never rests on an empty page.
    if (shown.length === 1 && page > 1) {
      const prev = page - 1;
      setPage(prev);
      loadPage(prev);
      return;
    }
    refresh();
  }

  const result = list.result;
  const guarded =
    result !== null && isDocumentsEnvelope(result.data) ? result.data : null;
  const okEnvelope = guarded !== null;
  const rows = guarded?.result ?? [];
  const shown = rows.slice(0, PAGE_SIZE);
  const hasMore = rows.length > PAGE_SIZE;
  const body = (
    <OpBody result={result} ok={okEnvelope} retry={refresh} opId="documents-list">
      {rows.length === 0 && page > 1 ? (
        // The window emptied under us (deletes elsewhere); the first
        // page is the truth, one click away.
        <div className="ui-stack">
          <StatusBanner kind="info">{strings.reviewDocsEmpty}</StatusBanner>
          <div className="ui-actions">
            <Button onClick={() => setPage(1)}>{strings.reviewDocsBackToFirst}</Button>
          </div>
        </div>
      ) : rows.length === 0 ? (
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
          rows={shown}
          page={page}
          pageSize={PAGE_SIZE}
          hasMore={hasMore}
          onPage={setPage}
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
