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
}

export const strings: Strings = en;
