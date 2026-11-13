import { Background, Controls, Handle, Position, ReactFlow, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Card, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMemo } from "react";
import OpBody from "../components/OpBody";
import StatusBanner from "../components/StatusBanner";
import { useServerWindow } from "../hooks/useServerWindow";
import { strings } from "../strings/map";
import type { EntityRow } from "./graph/shapes";
import { isEntitiesEnvelope } from "./graph/shapes";
import { layoutEntities, relationEdges } from "./map/layout";
import type { EntityNode } from "./map/layout";
import { isRelationsEnvelope } from "./map/shapes";
import type { RelationListRow } from "./map/shapes";

/** Rows per server window; one extra fetched row answers "has more". */
const PAGE_SIZE = 50;

/**
 * One entity node (Phase-2 Wave 6): the petrol card with the entity
 * name and its mono type. A display surface — the two invisible,
 * non-connectable handles exist only so xyflow can attach edges to the
 * node (it drops edges whose endpoint node has no handle); nothing on
 * the pane starts a write.
 */
function EntityNodeCard({ data }: NodeProps<EntityNode>) {
  return (
    <div className="ui-map-node">
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={false}
        className="ui-map-node__handle"
      />
      <p className="ui-map-node__name">{data.name}</p>
      <p className="ui-map-node__type ui-num">{data.entityType}</p>
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={false}
        className="ui-map-node__handle"
      />
    </div>
  );
}

const nodeTypes = { entity: EntityNodeCard };

/** The read-only canvas ARIA vocabulary, from the string tables. */
const ariaLabelConfig = {
  "node.a11yDescription.default": strings.mapAriaNodeSelect,
  "node.a11yDescription.keyboardDisabled": strings.mapAriaNodeDisabled,
  "node.a11yDescription.ariaLiveMessage": ({ x, y }: { direction: string; x: number; y: number }) =>
    strings.mapAriaNodeMove.replace("{x}", String(x)).replace("{y}", String(y)),
  "edge.a11yDescription.default": strings.mapAriaEdgeSelect,
  "controls.ariaLabel": strings.mapAriaControls,
  "controls.zoomIn.ariaLabel": strings.mapAriaZoomIn,
  "controls.zoomOut.ariaLabel": strings.mapAriaZoomOut,
  "controls.fitView.ariaLabel": strings.mapAriaFitView,
  "controls.interactive.ariaLabel": strings.mapAriaInteractive,
  "minimap.ariaLabel": strings.mapAriaMinimap,
  "handle.ariaLabel": strings.mapAriaHandle,
};

/**
 * The Map stage (Phase-2 Wave 6): the Desk's read-only graph view. Two
 * window reads from the one server-window seam — entities (nodes) and
 * relations (edges) — no poll loops, no writes. Read-only is total:
 * dragging and connecting are off, and deleteKeyCode is null because
 * xyflow's Backspace default would delete the selected drawn node.
 * The canvas never carries the only path to data: the same two
 * windows render as tables under it, so screen readers and axe scan
 * the plain tables.
 */
export default function Map() {
  const entityWin = useServerWindow<EntityRow, { status: string; result: EntityRow[] }>(
    "list-entities",
    PAGE_SIZE,
    isEntitiesEnvelope,
  );
  const relationWin =
    useServerWindow<RelationListRow, { status: string; result: RelationListRow[] }>(
      "list-relations",
      PAGE_SIZE,
      isRelationsEnvelope,
    );

  const visibleIds = useMemo(
    () => new Set(entityWin.rows.map((row) => row.entity_id)),
    [entityWin.rows],
  );
  const nodes = useMemo(
    () => layoutEntities(entityWin.rows, relationWin.rows),
    [entityWin.rows, relationWin.rows],
  );
  const { edges, outside } = useMemo(
    () => relationEdges(relationWin.rows, visibleIds),
    [relationWin.rows, visibleIds],
  );

  const entityColumns: ColumnsType<EntityRow> = [
    { title: strings.mapColumnName, dataIndex: "name", key: "name" },
    { title: strings.mapColumnType, dataIndex: "entity_type", key: "entity_type" },
    {
      title: strings.mapColumnId,
      dataIndex: "entity_id",
      key: "entity_id",
      render: (value: string) => <span className="ui-num">{value}</span>,
    },
  ];
  const relationColumns: ColumnsType<RelationListRow> = [
    { title: strings.mapEdgeColumnSource, dataIndex: "source_name", key: "source_name" },
    { title: strings.mapEdgeColumnRelation, dataIndex: "relation_type", key: "relation_type" },
    { title: strings.mapEdgeColumnTarget, dataIndex: "target_name", key: "target_name" },
  ];

  return (
    <div className="ui-stack">
      <Card title={strings.mapTitle}>
        <p>{strings.mapBody}</p>
      </Card>
      <Card title={strings.mapCanvasHint}>
        <OpBody
          result={entityWin.result}
          ok={entityWin.okEnvelope}
          retry={entityWin.refresh}
          opId="list-entities"
        >
          {entityWin.rows.length === 0 ? (
            <StatusBanner kind="info">{strings.mapEmpty}</StatusBanner>
          ) : (
            <>
              {entityWin.hasMore && (
                <StatusBanner kind="info">
                  {strings.mapCappedEntities.replace("{shown}", String(entityWin.rows.length))}
                </StatusBanner>
              )}
              <div className="ui-map-canvas">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  nodesDraggable={false}
                  nodesConnectable={false}
                  deleteKeyCode={null}
                  elementsSelectable
                  ariaLabelConfig={ariaLabelConfig}
                  fitView
                  minZoom={0.2}
                >
                  <Background />
                  <Controls showInteractive={false} />
                </ReactFlow>
              </div>
            </>
          )}
        </OpBody>
      </Card>
      <Card title={strings.mapEntitiesTitle}>
        <OpBody
          result={entityWin.result}
          ok={entityWin.okEnvelope}
          retry={entityWin.refresh}
          opId="list-entities"
        >
          {entityWin.rows.length === 0 ? (
            <Typography.Text type="secondary">{strings.mapEmpty}</Typography.Text>
          ) : (
            <Table<EntityRow>
              size="small"
              rowKey="entity_id"
              dataSource={entityWin.rows}
              columns={entityColumns}
              pagination={false}
            />
          )}
        </OpBody>
      </Card>
      <Card title={strings.mapRelationsTitle}>
        <OpBody
          result={relationWin.result}
          ok={relationWin.okEnvelope}
          retry={relationWin.refresh}
          opId="list-relations"
        >
          {relationWin.rows.length === 0 ? (
            <Typography.Text type="secondary">{strings.mapRelationsEmpty}</Typography.Text>
          ) : (
            <>
              {relationWin.hasMore && (
                <StatusBanner kind="info">
                  {strings.mapCappedRelations.replace("{shown}", String(relationWin.rows.length))}
                </StatusBanner>
              )}
              {outside > 0 && (
                <StatusBanner kind="info">
                  {strings.mapOutsideNote.replace("{count}", String(outside))}
                </StatusBanner>
              )}
              <Table<RelationListRow>
                size="small"
                rowKey="relation_id"
                dataSource={relationWin.rows}
                columns={relationColumns}
                pagination={false}
              />
            </>
          )}
        </OpBody>
      </Card>
    </div>
  );
}
