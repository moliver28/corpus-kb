import { Descriptions, Drawer, Typography } from "antd";
import { useCallback, useEffect } from "react";
import OpBody from "../../components/OpBody";
import { useRowRead } from "../../hooks/useRowRead";
import { strings } from "../../strings";
import { isMetadataEnvelope, isTagsEnvelope, metadataText } from "./shapes";
import type { MetadataRow, DocumentRow } from "./shapes";
import { MetadataEditor, TagEditor } from "./DrawerEditors";

function metadataWords(rows: MetadataRow[]) {
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
 * One document's tags and metadata, editable in place. The reads are
 * manifest ops (document-tags, document-metadata) through the
 * row-tagged read hook — the newest document wins, so a slow answer for
 * a document the user already left never renders. The writes are the
 * Phase-2 Wave 2 manifest ops (tag-document, untag-document,
 * set-metadata) through the form-to-write seam hook; every ok answer
 * re-reads this document's rows.
 */
export default function DocumentDetailDrawer({
  row,
  onClose,
}: {
  row: DocumentRow | null;
  onClose: () => void;
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
  return (
    <Drawer title={open ? row.source : ""} open={open} onClose={onClose} destroyOnHidden>
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
            <TagEditor docId={row.doc_id} rows={tagRows} refresh={refreshTags} />
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
    </Drawer>
  );
}
