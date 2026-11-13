// Map stage vocabulary (Phase-2 Wave 6): the read-only graph map, its
// honest window caps, and the canvas ARIA labels.
import tableJson from "./map.json";
import { strings as appTable, type AppStrings } from "./app";

export interface MapStrings {
  mapAriaControls: string;
  mapAriaEdgeSelect: string;
  mapAriaFitView: string;
  mapAriaHandle: string;
  mapAriaInteractive: string;
  mapAriaMinimap: string;
  mapAriaNodeDisabled: string;
  mapAriaNodeMove: string;
  mapAriaNodeSelect: string;
  mapAriaZoomIn: string;
  mapAriaZoomOut: string;
  mapBody: string;
  mapCanvasHint: string;
  mapCappedEntities: string;
  mapCappedRelations: string;
  mapColumnId: string;
  mapColumnName: string;
  mapColumnType: string;
  mapEdgeColumnRelation: string;
  mapEdgeColumnSource: string;
  mapEdgeColumnTarget: string;
  mapEmpty: string;
  mapEntitiesTitle: string;
  mapOutsideNote: string;
  mapRelationsEmpty: string;
  mapRelationsTitle: string;
  mapTitle: string;
}

export const strings: AppStrings & MapStrings = {
  ...appTable,
  ...tableJson,
};
