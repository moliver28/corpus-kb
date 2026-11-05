import { Descriptions, Drawer, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { useEffect } from "react";
import OpBody from "../../components/OpBody";
import { useRowRead } from "../../hooks/useRowRead";
import { strings } from "../../strings";
import {
  isMetadataEnvelope,
  isTagsEnvelope,
  metadataText,
  tagColor,
} from "./shapes";
import type { DocumentRow, MetadataRow, TagRow } from "./shapes";

function tagWords(rows: TagRow[]): ReactNode {
  if (rows.length === 0) {
    return <Typography.Text type="secondary">{strings.reviewTagsEmpty}</Typography.Text>;
  }
  return (
    <div className="ui-actions">
      {rows.map((row) => (
        <Tag key={row.name} color={tagColor(row.color)}>
          {row.name}
        </Tag>
      ))}
    </div>
  );
}

function metadataWords(rows: MetadataRow[]): ReactNode {
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
 * The one document's tags and metadata rows, in a drawer. Both reads
 * are manifest ops (document-tags, document-metadata) through the
 * row-tagged read hook — the newest document wins, so a slow answer for
 * a document the user already left never renders. The writes are not
 * yet manifest ops, so the drawer stays read-only by design.
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
          retry={() => {
            if (row !== null) readTags(row.doc_id, { pathParams: { doc_id: row.doc_id } });
          }}
        >
          {tagRows === null ? null : tagWords(tagRows)}
        </OpBody>
        <Typography.Title level={5} className="ui-flush">
          {strings.reviewMetadataTitle}
        </Typography.Title>
        <OpBody
          result={metaShown}
          ok={metaRows !== null}
          skeletonRows={2}
          opId="document-metadata"
          retry={() => {
            if (row !== null) readMeta(row.doc_id, { query: { doc_id: row.doc_id } });
          }}
        >
          {metaRows === null ? null : metadataWords(metaRows)}
        </OpBody>
      </div>
    </Drawer>
  );
}
