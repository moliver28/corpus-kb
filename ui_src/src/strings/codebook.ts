// The codebook stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./codebook.json";
import { strings as appTable, type AppStrings } from "./app";

export interface CodebookStrings {
  codebookBatchNotWired: string;
  codebookBody: string;
  codebookCalibrateNotWired: string;
  codebookColumnLabel: string;
  codebookColumnUnits: string;
  codebookCycleAction: string;
  codebookCycleAwaiting: string;
  codebookCycleBody: string;
  codebookCycleCompleted: string;
  codebookCycleDenied: string;
  codebookCycleGate: string;
  codebookCycleHalted: string;
  codebookCycleStopped: string;
  codebookCycleTitle: string;
  codebookCycleUnavailable: string;
  codebookDimsHeading: string;
  codebookDimsNone: string;
  codebookEmbedNotWired: string;
  codebookEvidenceCodeLabel: string;
  codebookEvidenceRequired: string;
  codebookEvidenceTitle: string;
  codebookGovernanceActions: string;
  codebookGovernanceBody: string;
  codebookGovernanceStale: string;
  codebookGovernanceTitle: string;
  codebookInductiveBody: string;
  codebookInductiveTitle: string;
  codebookLevelExpert: string;
  codebookLevelNovice: string;
  codebookNotWiredTitle: string;
  codebookNotebookTitle: string;
  codebookOverlapBody: string;
  codebookOverlapTitle: string;
  codebookPoolNotWired: string;
  codebookPreFlightBody: string;
  codebookPreFlightTitle: string;
  codebookProjectsLine: string;
  codebookPromoteAction: string;
  codebookPromoteBody: string;
  codebookPromoteDefinitionLabel: string;
  codebookPromoteDefinitionRequired: string;
  codebookPromoteDone: string;
  codebookPromoteDuplicate: string;
  codebookPromoteModalTitle: string;
  codebookPromoteNameLabel: string;
  codebookPromoteNameRequired: string;
  codebookPromoteTitle: string;
  codebookProposalsNone: string;
  codebookReliabilityNotWired: string;
  codebookRunBody: string;
  codebookRunTitle: string;
  codebookSaturationNotWired: string;
  codebookStepsTitle: string;
  codebookTitle: string;
  codebookUncodedBody: string;
  codebookUncodedTitle: string;
  codebookVersionLabel: string;
  codebookVersionRequired: string;
  codebookVersionsHeading: string;
  codebookVersionsNone: string;
}

export const strings: AppStrings & CodebookStrings = {
  ...appTable,
  ...tableJson,
};
