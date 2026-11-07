import { Button, Card } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router";
import ErrorPanel from "../../components/ErrorPanel";
import OpBody from "../../components/OpBody";
import StatusBanner from "../../components/StatusBanner";
import { useOp } from "../../hooks/useOp";
import { useQueryParam } from "../../hooks/useQueryParam";
import { useServerWindow } from "../../hooks/useServerWindow";
import { strings } from "../../strings";
import DocumentDetailDrawer from "./DocumentDetailDrawer";
import type { DrawerPanel } from "./DocumentDetailDrawer";
import DocumentsTable from "./DocumentsTable";
import { isDocumentsEnvelope } from "./shapes";
import type { DocumentsEnvelope, DocumentRow } from "./shapes";

/** Rows per server window; one extra fetched row answers "has next". */
const PAGE_SIZE = 50;

/**
 * The corpus document list: the documents-list window table, the
 * per-document tag/metadata drawer (plus its NLP panel), and the delete
 * gate. The window state, the limit+1 fetch, and the pager live in the
 * shared server-window seam (hooks/useServerWindow), not in this card.
 *
 * The NLP deep link rides the hash query (`#/review?analyze=<doc_id>`,
 * the useQueryParam seam): the Analyze row action writes it, a reload
 * or a shared link restores it, and closing the drawer clears it — the
 * drawer state stays coherent. The drawer state is DERIVED while the
 * param names a row of the served window (no effect, no drift): a param
 * for a row outside the window reads as absent, so a stale id can never
 * open a wrong drawer.
 */
export default function DocumentsCard() {
  const navigate = useNavigate();
  const [manualDetail, setManualDetail] = useState<DocumentRow | null>(null);
  const [manualPanel, setManualPanel] = useState<DrawerPanel>("details");
  const [deleteRow, setDeleteRow] = useState<DocumentRow | null>(null);
  const [deleted, setDeleted] = useState(false);
  const win = useServerWindow<DocumentRow, DocumentsEnvelope>(
    "documents-list",
    PAGE_SIZE,
    isDocumentsEnvelope,
  );
  const remove = useOp("document-delete");
  const { run: runDelete, reset: resetDelete } = remove;
  // The deep-link param: allowed = this window's doc ids (an unknown id
  // reads as absent, so the unknown word can never crash the page).
  const [analyzeParam, setAnalyzeParam] = useQueryParam(
    "analyze",
    win.rows.map((row) => row.doc_id),
  );
  // While the param names a served row, the drawer answers to the URL:
  // that row, NLP panel. The Details action (and the tab switch back)
  // clears the param and hands control to the manual state.
  const paramRow =
    analyzeParam === null
      ? undefined
      : win.rows.find((candidate) => candidate.doc_id === analyzeParam);
  const detail = paramRow !== undefined ? paramRow : manualDetail;
  const panel: DrawerPanel = paramRow !== undefined ? "analyze" : manualPanel;

  function openDetails(row: DocumentRow): void {
    setAnalyzeParam(null);
    setManualDetail(row);
    setManualPanel("details");
  }

  function openAnalyze(row: DocumentRow): void {
    setManualDetail(row);
    setAnalyzeParam(row.doc_id);
  }

  function changePanel(next: DrawerPanel): void {
    setManualPanel(next);
    if (next === "details") {
      setAnalyzeParam(null);
    }
  }

  function closeDrawer(): void {
    setAnalyzeParam(null);
    setManualDetail(null);
    setManualPanel("details");
  }

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
          onDetails={openDetails}
          onAnalyze={openAnalyze}
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
      <DocumentDetailDrawer
        row={detail}
        onClose={closeDrawer}
        panel={panel}
        onPanelChange={changePanel}
      />
    </Card>
  );
}
