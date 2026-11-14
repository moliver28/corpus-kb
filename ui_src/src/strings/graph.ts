// The graph stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./graph.json";
import { strings as appTable, type AppStrings } from "./app";

export interface GraphStrings {
  graphBody: string;
  graphColumnActions: string;
  graphColumnId: string;
  graphColumnName: string;
  graphColumnType: string;
  graphCreateBody: string;
  graphCreateSaved: string;
  graphCreateTitle: string;
  graphDetails: string;
  graphDrawerEmptyMeta: string;
  graphDrawerMetadataTitle: string;
  graphDrawerNeighborsTitle: string;
  graphDrawerRelationsTitle: string;
  graphEditSave: string;
  graphEditSaved: string;
  graphEditTitle: string;
  graphEntitiesEmpty: string;
  graphEntitiesTitle: string;
  graphEntityNameLabel: string;
  graphEntityTypeLabel: string;
  graphEntityDeleteBody: string;
  graphEntityDeleteTitle: string;
  graphEntityDeleted: string;
  graphFilterLabel: string;
  graphNameRequired: string;
  graphNeighborsEmpty: string;
  graphPickerCapped: string;
  graphPickerEmpty: string;
  graphPickerNoMatch: string;
  graphPickerSearchCapped: string;
  graphRelationBody: string;
  graphRelationDeleteBody: string;
  graphRelationDeleteTitle: string;
  graphRelationDeleted: string;
  graphRelationEditTitle: string;
  graphRelationRow: string;
  graphRelationSaved: string;
  graphRelationTitle: string;
  graphRelationTypeLabel: string;
  graphRelationUpdated: string;
  graphRelationTypeRequired: string;
  graphRelationsEmpty: string;
  graphSourceLabel: string;
  graphSourceRequired: string;
  graphTargetLabel: string;
  graphTargetRequired: string;
  graphTitle: string;
  graphWeightLabel: string;
  graphTypeRequired: string;
}

export const strings: AppStrings & GraphStrings = {
  ...appTable,
  ...tableJson,
};
