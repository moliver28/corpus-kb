import { Descriptions, Drawer, Skeleton, Tag, Typography } from "antd";
import type { ReactNode } from "react";
import { useEffect } from "react";
import type { OpResult } from "../../api/types";
import ErrorPanel from "../../components/ErrorPanel";
import JsonViewer from "../../components/JsonViewer";
import StatusBanner from "../../components/StatusBanner";
import UnavailablePanel from "../../components/UnavailablePanel";
import { useOp } from "../../hooks/useOp";
import { strings } from "../../strings";
import {
  isMetadataEnvelope,
  isTagsEnvelope,
  metadataText,
  tagColor,
} from "./shapes";
import type { DocumentRow, MetadataRow, TagRow } from "./shapes";

/**
 * One read's honest body: load, network, unavailable, drift, then the
 * caller's rows. The dispatch mirrors the card pattern (ReviewQueueCard)
 * without a second error vocabulary.
 */
function readBody(
  result: OpResult<unknown> | null,
  ok: boolean,
  retry: () => void,
  opId: string,
  rows: ReactNode,
): ReactNode {
  if (result === null) {
    return <Skeleton active title={false} paragraph={{ rows: 2 }} />;
  }
  if (result.kind === "network") {
    return <ErrorPanel result={result} onRetry={retry} opId={opId} />;
  }
  if (result.kind === "unavailable") {
    return <UnavailablePanel result={result} onRetry={retry} />;
  }
  if (!ok) {
    return (
      <div>
        <StatusBanner kind="warn">{strings.unreadableResponse}</StatusBanner>
        <JsonViewer value={result.data} />
      </div>
    );
  }
  return rows;
}

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
 * are manifest ops (document-tags, document-metadata); the writes are
 * not yet manifest ops, so the drawer stays read-only by design.
 */
export default function DocumentDetailDrawer({
  row,
  onClose,
}: {
  row: DocumentRow | null;
  onClose: () => void;
}) {
  const tags = useOp("document-tags");
  const meta = useOp("document-metadata");
  const { run: runTags, reset: resetTags } = tags;
  const { run: runMeta, reset: resetMeta } = meta;

  // One read pair per opened document: tags by path slot, metadata by
  // query pair. The run identities are stable, so the effect fires once
  // per row change.
  useEffect(() => {
    if (row === null) {
      return;
    }
    resetTags();
    resetMeta();
    void runTags(undefined, undefined, { doc_id: row.doc_id });
    void runMeta(undefined, { doc_id: row.doc_id });
  }, [row, runTags, runMeta, resetTags, resetMeta]);

  const open = row !== null;
  return (
    <Drawer
      title={open ? row.source : ""}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      <div className="ui-stack">
        <Typography.Title level={5} className="ui-flush">
          {strings.reviewTagsTitle}
        </Typography.Title>
        {readBody(
          tags.result,
          tags.result !== null && isTagsEnvelope(tags.result.data),
          () => {
            if (row !== null) void runTags(undefined, undefined, { doc_id: row.doc_id });
          },
          "document-tags",
          tags.result !== null && isTagsEnvelope(tags.result.data)
            ? tagWords(tags.result.data.result)
            : null,
        )}
        <Typography.Title level={5} className="ui-flush">
          {strings.reviewMetadataTitle}
        </Typography.Title>
        {readBody(
          meta.result,
          meta.result !== null && isMetadataEnvelope(meta.result.data),
          () => {
            if (row !== null) void runMeta(undefined, { doc_id: row.doc_id });
          },
          "document-metadata",
          meta.result !== null && isMetadataEnvelope(meta.result.data)
            ? metadataWords(meta.result.data.result)
            : null,
        )}
      </div>
    </Drawer>
  );
}
