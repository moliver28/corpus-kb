import { Descriptions, Drawer, Typography } from "antd";
import { useCallback, useEffect } from "react";
import OpBody from "../../components/OpBody";
import { useRowRead } from "../../hooks/useRowRead";
import { strings } from "../../strings/graph";
import { isNeighborsEnvelope, isRelationsEnvelope } from "./shapes";
import type { EntityRow } from "./shapes";

function metadataWords(row: EntityRow) {
  const entries = Object.entries(row.metadata);
  if (entries.length === 0) {
    return <Typography.Text type="secondary">{strings.graphDrawerEmptyMeta}</Typography.Text>;
  }
  return (
    <Descriptions size="small" column={1}>
      {entries.map(([key, value]) => (
        <Descriptions.Item key={key} label={key}>
          <span className="ui-num">{String(value)}</span>
        </Descriptions.Item>
      ))}
    </Descriptions>
  );
}

/**
 * One entity's drawer (Phase-2 Wave 4): the entity's metadata, its
 * relations (the get-entity-relations op, by path slot), and its 1-hop
 * neighbors (the graph-neighbors op, a POST read through the row-tagged
 * read hook). The newest entity wins — a slow answer for an entity the
 * user already left never renders.
 */
export default function EntityDrawer({
  row,
  onClose,
}: {
  row: EntityRow | null;
  onClose: () => void;
}) {
  const { read: readRelations, state: relationsState } = useRowRead("get-entity-relations");
  const { read: readNeighbors, state: neighborsState } = useRowRead("graph-neighbors");

  // One read pair per opened entity: relations by path slot, neighbors
  // by POST body. The read identities are stable, so the effect fires
  // once per row change.
  useEffect(() => {
    if (row === null) {
      return;
    }
    readRelations(row.entity_id, { pathParams: { entity_id: row.entity_id } });
    readNeighbors(row.entity_id, {
      body: { entity_id: row.entity_id, hops: 1 },
    });
  }, [row, readRelations, readNeighbors]);

  const refresh = useCallback(() => {
    if (row !== null) {
      readRelations(row.entity_id, { pathParams: { entity_id: row.entity_id } });
      readNeighbors(row.entity_id, {
        body: { entity_id: row.entity_id, hops: 1 },
      });
    }
  }, [row, readRelations, readNeighbors]);

  // Only a read addressed to the open entity may render.
  const relationsShown =
    relationsState !== null && row !== null && relationsState.rowId === row.entity_id
      ? relationsState.result
      : null;
  const neighborsShown =
    neighborsState !== null && row !== null && neighborsState.rowId === row.entity_id
      ? neighborsState.result
      : null;
  const relationRows =
    relationsShown !== null && isRelationsEnvelope(relationsShown.data)
      ? relationsShown.data.result
      : null;
  const neighborRows =
    neighborsShown !== null && isNeighborsEnvelope(neighborsShown.data)
      ? neighborsShown.data.result
      : null;
  const open = row !== null;
  return (
    <Drawer title={open ? row.name : ""} open={open} onClose={onClose} destroyOnHidden>
      <div className="ui-stack">
        <Typography.Title level={5} className="ui-flush">
          {strings.graphDrawerMetadataTitle}
        </Typography.Title>
        {open && metadataWords(row)}
        <Typography.Title level={5} className="ui-flush">
          {strings.graphDrawerRelationsTitle}
        </Typography.Title>
        <OpBody
          result={relationsShown}
          ok={relationRows !== null}
          skeletonRows={2}
          opId="get-entity-relations"
          retry={refresh}
        >
          {relationRows === null ? null : (
            <div className="ui-stack">
              {relationRows.length === 0 && (
                <Typography.Text type="secondary">{strings.graphRelationsEmpty}</Typography.Text>
              )}
              {relationRows.map((item) => (
                <p key={item.relation_id} className="ui-flush">
                  {strings.graphRelationRow
                    .replace("{source}", item.source_name)
                    .replace("{relation}", item.relation_type)
                    .replace("{target}", item.target_name)}
                </p>
              ))}
            </div>
          )}
        </OpBody>
        <Typography.Title level={5} className="ui-flush">
          {strings.graphDrawerNeighborsTitle}
        </Typography.Title>
        <OpBody
          result={neighborsShown}
          ok={neighborRows !== null}
          skeletonRows={2}
          opId="graph-neighbors"
          retry={refresh}
        >
          {neighborRows === null ? null : (
            <div className="ui-stack">
              {neighborRows.length === 0 && (
                <Typography.Text type="secondary">{strings.graphNeighborsEmpty}</Typography.Text>
              )}
              {neighborRows.map((item) => (
                <p key={item.entity_id} className="ui-flush">
                  {item.name}{" "}
                  <Typography.Text type="secondary">
                    ({item.entity_type}, {item.depth})
                  </Typography.Text>
                </p>
              ))}
            </div>
          )}
        </OpBody>
      </div>
    </Drawer>
  );
}
