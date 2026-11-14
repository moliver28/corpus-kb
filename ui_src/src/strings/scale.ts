// The scale stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./scale.json";
import { strings as appTable, type AppStrings } from "./app";

export interface ScaleStrings {
  scaleBody: string;
  scaleBulkTitle: string;
  scaleCalibrateApply: string;
  scaleCalibrateApplyBody: string;
  scaleCalibrateApplyTitle: string;
  scaleCalibrateBody: string;
  scaleCalibrateChosen: string;
  scaleCalibrateColumnCurrent: string;
  scaleCalibrateColumnKey: string;
  scaleCalibrateColumnProposed: string;
  scaleCalibrateNoChanges: string;
  scaleCalibratePreview: string;
  scaleCalibrateProfileLabel: string;
  scaleCalibrateProfileRequired: string;
  scaleCalibrateTitle: string;
  scaleCalibrateWritten: string;
  scaleCalibrateWrittenUnknown: string;
  scaleDemoAction: string;
  scaleDemoBody: string;
  scaleDemoCompleted: string;
  scaleDemoConfirmBody: string;
  scaleDemoConfirmTitle: string;
  scaleDemoFailed: string;
  scaleDemoNarration: string;
  scaleDemoTitle: string;
  scaleLedgerBackoff: string;
  scaleLedgerBody: string;
  scaleLedgerPaused: string;
  scaleLedgerPolling: string;
  scaleLedgerTitle: string;
  scaleRunsAll: string;
  scaleRunsColumnProject: string;
  scaleRunsColumnSource: string;
  scaleRunsColumnStarted: string;
  scaleRunsColumnStatus: string;
  scaleRunsEmpty: string;
  scaleRunsFilterLabel: string;
  scaleRunsNone: string;
  scaleTitle: string;
}

export const strings: AppStrings & ScaleStrings = {
  ...appTable,
  ...tableJson,
};
