import en from "./en.json";

// All user-facing strings live in en.json (zero-drift amendment 2: the
// STE gate reads that file). This interface pins its shape at compile
// time; a missing key fails `poe ui-lint`.
export interface Strings {
  appTitle: string;
  stageSetup: string;
  stageIngest: string;
  stageReview: string;
  stageScale: string;
  stageAsk: string;
  stageAnalyze: string;
  stageCodebook: string;
  stageRelease: string;
  setupTitle: string;
  setupBody: string;
  unreadableResponse: string;
  serverNotAnswering: string;
  remediationRunDoctor: string;
  unknownOperation: string;
  bannerKindInfo: string;
  bannerKindOk: string;
  bannerKindWarn: string;
  bannerKindError: string;
  errorTitle: string;
  blockedTitle: string;
  remediationLabel: string;
  retryLabel: string;
  unavailableTitle: string;
  unavailableBody: string;
  receiptRaw: string;
  receiptDone: string;
  receiptEmpty: string;
  receiptDegraded: string;
  receiptPartial: string;
  receiptAbstained: string;
  runnerRunLabel: string;
  runnerCancelLabel: string;
  runnerCheckStatus: string;
  runnerRunningElapsed: string;
  runnerUnknownOutcome: string;
  runnerParamsHint: string;
  runnerInvalidJson: string;
  runnerMissingKeys: string;
  confirmLongTitle: string;
  confirmLongBody: string;
  confirmConfirm: string;
  confirmCancel: string;
  citedAnswerTitle: string;
  citationsHeading: string;
  claimSupportLabel: string;
  badgeDegradeNote: string;
  verifyLabelVerified: string;
  verifyLabelPartial: string;
  verifyLabelNotVerified: string;
  docsColumnTitle: string;
  docsColumnAdded: string;
  docsColumnStatus: string;
  docsEmpty: string;
  topicsColumnLabel: string;
  topicsColumnSize: string;
  topicsColumnKeywords: string;
  topicsEmpty: string;
}

export const strings: Strings = en;
