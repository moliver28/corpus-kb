// The ask stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./ask.json";
import { strings as appTable, type AppStrings } from "./app";

export interface AskStrings {
  askAction: string;
  askBody: string;
  askColumnClaim: string;
  askColumnConfidence: string;
  askColumnScore: string;
  askColumnSource: string;
  askColumnText: string;
  askColumnVerdict: string;
  askCopyChunkId: string;
  askDegradedExcluded: string;
  askDegradedState: string;
  askEmptyState: string;
  askEvidenceAt: string;
  askEvidenceHeading: string;
  askEvidenceUntitled: string;
  askHistoryClear: string;
  askHistoryEmpty: string;
  askHistoryTitle: string;
  askNoteLabel: string;
  askNotebookAction: string;
  askNotebookProjectLabel: string;
  askNotebookProjectRequired: string;
  askNotebookQuestionLabel: string;
  askNotebookRetrievalOnly: string;
  askProjectsCapped: string;
  askProjectsEmpty: string;
  askProjectsFallback: string;
  askQueryLabel: string;
  askQueryRequired: string;
  askSearchAction: string;
  askSearchCapHint: string;
  askSearchChunkLabel: string;
  askSearchChunkRequired: string;
  askSearchEmpty: string;
  askSearchModeContext: string;
  askSearchModeSimilar: string;
  askSearchModeText: string;
  askSearchQueryLabel: string;
  askSearchQueryRequired: string;
  askStreamingNote: string;
  askTabNotebook: string;
  askTabSearch: string;
  askTabVerify: string;
  askTitle: string;
  askVerdictContradicted: string;
  askVerdictEntailed: string;
  askVerdictUnsupported: string;
  askVerifyAbstained: string;
  askVerifyAction: string;
  askVerifyAnswerLabel: string;
  askVerifyAnswerRequired: string;
  askVerifyBody: string;
  askVerifyChunksHint: string;
  askVerifyChunksLabel: string;
  askVerifyChunksRequired: string;
}

export const strings: AppStrings & AskStrings = {
  ...appTable,
  ...tableJson,
};
