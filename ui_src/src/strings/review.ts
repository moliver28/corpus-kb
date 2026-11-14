// The review stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./review.json";
import { strings as appTable, type AppStrings } from "./app";

export interface ReviewStrings {
  reviewAccept: string;
  reviewAnalyzeBody: string;
  reviewAnalyzeLabelsLabel: string;
  reviewAnalyzeLabelsRequired: string;
  reviewAnalyzeNoChunks: string;
  reviewAnalyzePickLabel: string;
  reviewAnalyzeScopeAll: string;
  reviewAnalyzeScopeCapped: string;
  reviewAnalyzeTab: string;
  reviewAnalyzeTitle: string;
  reviewBody: string;
  reviewColumnActions: string;
  reviewColumnCode: string;
  reviewColumnConfidence: string;
  reviewColumnState: string;
  reviewColumnUnit: string;
  reviewDecisionDone: string;
  reviewDetailsTab: string;
  reviewDocsAnalyze: string;
  reviewDocsBackToFirst: string;
  reviewDocsColumnActions: string;
  reviewDocsColumnAdded: string;
  reviewDocsColumnChunks: string;
  reviewDocsColumnSource: string;
  reviewDocsColumnType: string;
  reviewDocsDelete: string;
  reviewDocsDeleteBody: string;
  reviewDocsDeleteTitle: string;
  reviewDocsDeleted: string;
  reviewDocsDetails: string;
  reviewDocsEmpty: string;
  reviewDocsIngest: string;
  reviewDocsTitle: string;
  reviewMetadataEmpty: string;
  reviewMetadataKeyLabel: string;
  reviewMetadataSaveButton: string;
  reviewMetadataSaved: string;
  reviewMetadataTitle: string;
  reviewMetadataValueLabel: string;
  reviewNoteLabel: string;
  reviewNoteRequired: string;
  reviewOverride: string;
  reviewOverrideBody: string;
  reviewOverrideSubmit: string;
  reviewOverrideTitle: string;
  reviewQueueBody: string;
  reviewQueueEmpty: string;
  reviewQueueNext: string;
  reviewQueueTitle: string;
  reviewReviewerHint: string;
  reviewReviewerLabel: string;
  reviewReviewerRequired: string;
  reviewStateAuto: string;
  reviewStateConfirmed: string;
  reviewStateNeedsReview: string;
  reviewStateOverridden: string;
  reviewStateUnknown: string;
  reviewTagAddButton: string;
  reviewTagAddLabel: string;
  reviewTagAdded: string;
  reviewTagRemoveButton: string;
  reviewTagRemoveConfirm: string;
  reviewTagRemoveTitle: string;
  reviewTagRemoved: string;
  reviewTagsEmpty: string;
  reviewTagsTitle: string;
  reviewTitle: string;
  reviewTopicsTitle: string;
}

export const strings: AppStrings & ReviewStrings = {
  ...appTable,
  ...tableJson,
};
