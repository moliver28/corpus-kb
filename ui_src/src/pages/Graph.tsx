import { Card } from "antd";
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
 */
export default function Graph() {
  const win = useServerWindow<EntityRow, EntitiesEnvelope>(
    "list-entities",
    PAGE_SIZE,
    isEntitiesEnvelope,
  );
  return (
    <div className="ui-stack">
      <Card title={strings.graphTitle}>
        <p>{strings.graphBody}</p>
      </Card>
      <EntitiesCard win={win} />
      <CreateEntityCard refresh={win.refresh} />
      <CreateRelationCard refresh={win.refresh} />
    </div>
  );
}
