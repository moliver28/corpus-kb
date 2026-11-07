import { Card } from "antd";
import { useState } from "react";
import { strings } from "../strings";
import { useServerWindow } from "../hooks/useServerWindow";
import EntitiesCard from "./graph/EntitiesCard";
import { CreateEntityCard, CreateRelationCard } from "./graph/CreateCards";
import { isEntitiesEnvelope } from "./graph/shapes";
import type { EntitiesEnvelope, EntityRow } from "./graph/shapes";

/** Rows per server window; one extra fetched row answers "has next". */
const PAGE_SIZE = 50;

/**
 * The Graph stage (Phase-2 Wave 4): the Desk's graph domain. The page
 * owns the one server-window read; the entities table, the drawer, and
 * the two create forms all ride it — one pipeline per operation
 * (zero-drift rule 1), one window per stage.
 *
 * One write epoch for the whole stage: every ok write re-reads the
 * window AND the pickers, so a created entity is pickable the moment
 * the save banner shows (the Wave-4 review's P1-1).
 */
export default function Graph() {
  const win = useServerWindow<EntityRow, EntitiesEnvelope>(
    "list-entities",
    PAGE_SIZE,
    isEntitiesEnvelope,
  );
  const [writeEpoch, setWriteEpoch] = useState(0);
  function afterWrite(): void {
    win.refresh();
    setWriteEpoch((epoch) => epoch + 1);
  }
  return (
    <div className="ui-stack">
      <Card title={strings.graphTitle}>
        <p>{strings.graphBody}</p>
      </Card>
      <EntitiesCard win={win} />
      <CreateEntityCard refresh={afterWrite} />
      <CreateRelationCard refresh={afterWrite} reloadKey={writeEpoch} />
    </div>
  );
}
