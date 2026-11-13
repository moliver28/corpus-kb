// The release stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./release.json";
import { strings as appTable, type AppStrings } from "./app";

export interface ReleaseStrings {
  releaseAllPass: string;
  releaseAssessBody: string;
  releaseAssessTitle: string;
  releaseBody: string;
  releaseCompareTitle: string;
  releaseExecuteApproverLabel: string;
  releaseExecuteTitle: string;
  releaseGatesHeading: string;
  releaseNewLabel: string;
  releaseOldLabel: string;
  releaseReleaseLabel: string;
  releaseRequestCreatorLabel: string;
  releaseRequestTitle: string;
  releaseRequiredFailing: string;
  releaseReviewTitle: string;
  releaseStatusTitle: string;
  releaseTitle: string;
  releaseWaiversLabel: string;
}

export const strings: AppStrings & ReleaseStrings = {
  ...appTable,
  ...tableJson,
};
