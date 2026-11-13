// The setup stage vocabulary. Phase-2 Wave 6 string
// split: this table rides this stage's lazy chunk; keys two or
// more stages reference ride the app (shell) table.
import tableJson from "./setup.json";
import { strings as appTable, type AppStrings } from "./app";

export interface SetupStrings {
  setupBody: string;
  setupColumnCheck: string;
  setupColumnDetail: string;
  setupColumnFix: string;
  setupColumnState: string;
  setupDoctorChecksHeading: string;
  setupDoctorChecksNone: string;
  setupDoctorDbDown: string;
  setupDoctorDbOk: string;
  setupDoctorOllamaDown: string;
  setupDoctorOllamaOk: string;
  setupDoctorProfile: string;
  setupDoctorRefresh: string;
  setupDoctorTitle: string;
  setupEmbeddingDimsLow: string;
  setupEmbeddingDimsUnknown: string;
  setupEmbeddingLine: string;
  setupRerankerBackendAuto: string;
  setupRerankerOff: string;
  setupRerankerOn: string;
  setupStateFail: string;
  setupStateInfo: string;
  setupStateNotChecked: string;
  setupStateOk: string;
  setupStateWarn: string;
  setupTitle: string;
}

export const strings: AppStrings & SetupStrings = {
  ...appTable,
  ...tableJson,
};
