import { Descriptions, Drawer, Tabs, Typography } from "antd";
import { useCallback, useEffect } from "react";
import OpBody from "../../components/OpBody";
import { useRowRead } from "../../hooks/useRowRead";
import { strings } from "../../strings";
import { isMetadataEnvelope, isTagsEnvelope, metadataText } from "./shapes";
import type { MetadataRow, DocumentRow } from "./shapes";
import { MetadataEditor, TagEditor } from "./DrawerEditors";
import DrawerAnalyze from "./DrawerAnalyze";

/** The drawer's two panels: the record's fields and its NLP runs. */
export type DrawerPanel = "details" | "analyze";

function metadataWords(rows: MetadataRow[]) {
  if (rows.length === 0) {
    return (
      <Typography.Text type="secondary">{strings.reviewMetadataEmpty}</Typography.Text>
    );
  }
  return (
    <Descriptions size="small" column={1}>
      {rows.map((row) => (
        <Descriptions.Item key={row.key} label={row.key}>
          {metadataText(row.value)}
        </Descriptions.Item>
      ))}
    </Descriptions>
  );
}

/**
 * One document's tags and metadata, editable in place, plus the NLP
 * panel (Phase-2 Wave 5): the six chunk-scoped analysis methods run
 * here, so a novice launches a scoped analysis and reads the answer
 * without leaving the drawer. The reads are manifest ops (document-tags,
 * document-metadata, chunks-list) through the row-tagged read hook —
 * the newest document wins, so a slow answer for a document the user
 * already left never renders. The writes are the Phase-2 Wave 2
 * manifest ops (tag-document, untag-document, set-metadata) through the
 * form-to-write seam hook; every ok answer re-reads this document's
 * rows. The active panel is the parent's state (the deep link opens the
 * drawer straight on the NLP panel), never local.
 */
export default function DocumentDetailDrawer({
  row,
  onClose,
  panel,
  onPanelChange,
}: {
  row: DocumentRow | null;
  onClose: () => void;
  panel: DrawerPanel;
  onPanelChange: (panel: DrawerPanel) => void;
}) {
  const { read: readTags, state: tagsState } = useRowRead("document-tags");
  const { read: readMeta, state: metaState } = useRowRead("document-metadata");

  // One read pair per opened document: tags by path slot, metadata by
  // query pair. The read identities are stable, so the effect fires
  // once per row change.
  useEffect(() => {
    if (row === null) {
      return;
    }
    readTags(row.doc_id, { pathParams: { doc_id: row.doc_id } });
    readMeta(row.doc_id, { query: { doc_id: row.doc_id } });
  }, [row, readTags, readMeta]);

  const refreshTags = useCallback(() => {
    if (row !== null) {
      readTags(row.doc_id, { pathParams: { doc_id: row.doc_id } });
    }
  }, [row, readTags]);

  const refreshMeta = useCallback(() => {
    if (row !== null) {
      readMeta(row.doc_id, { query: { doc_id: row.doc_id } });
    }
  }, [row, readMeta]);

  // Only a read addressed to the open document may render.
  const tagsShown =
    tagsState !== null && row !== null && tagsState.rowId === row.doc_id
      ? tagsState.result
      : null;
  const metaShown =
    metaState !== null && row !== null && metaState.rowId === row.doc_id
      ? metaState.result
      : null;
  const tagRows = tagsShown !== null && isTagsEnvelope(tagsShown.data) ? tagsShown.data.result : null;
  const metaRows =
    metaShown !== null && isMetadataEnvelope(metaShown.data) ? metaShown.data.result : null;
  const open = row !== null;
  const details = (
    <div className="ui-stack">
      <Typography.Title level={5} className="ui-flush">
        {strings.reviewTagsTitle}
      </Typography.Title>
      <OpBody
        result={tagsShown}
        ok={tagRows !== null}
        skeletonRows={2}
        opId="document-tags"
        retry={refreshTags}
      >
        {tagRows === null || row === null ? null : (
          <div className="ui-stack">
            {tagRows.length === 0 && (
              <Typography.Text type="secondary">{strings.reviewTagsEmpty}</Typography.Text>
            )}
            <TagEditor docId={row.doc_id} rows={tagRows} refresh={refreshTags} />
          </div>
        )}
      </OpBody>
      <Typography.Title level={5} className="ui-flush">
        {strings.reviewMetadataTitle}
      </Typography.Title>
      <OpBody
        result={metaShown}
        ok={metaRows !== null}
        skeletonRows={2}
        opId="document-metadata"
        retry={refreshMeta}
      >
        {metaRows === null || row === null ? null : (
          <div className="ui-stack">
            {metadataWords(metaRows)}
            <MetadataEditor docId={row.doc_id} refresh={refreshMeta} />
          </div>
        )}
      </OpBody>
    </div>
  );
  return (
    <Drawer title={open ? row.source : ""} open={open} onClose={onClose} destroyOnHidden>
      <Tabs
        activeKey={panel}
        onChange={(key) => onPanelChange(key === "analyze" ? "analyze" : "details")}
        items={[
          { key: "details", label: strings.reviewDetailsTab, children: details },
          {
            key: "analyze",
            label: strings.reviewAnalyzeTab,
            // key={doc_id}: a docId change under a mounted panel remounts
            // the pane, so no stale receipt from the previous document can
            // survive (the same rule as RunPane's key={opId}).
            children: row !== null ? <DrawerAnalyze key={row.doc_id} docId={row.doc_id} /> : null,
          },
        ]}
      />
    </Drawer>
  );
}
