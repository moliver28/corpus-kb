import { strings as analyzeTable, type AnalyzeStrings } from "./analyze";
import { strings as appTable, type AppStrings } from "./app";
import { strings as askTable, type AskStrings } from "./ask";
import { strings as codebookTable, type CodebookStrings } from "./codebook";
import { strings as graphTable, type GraphStrings } from "./graph";
import { strings as ingestTable, type IngestStrings } from "./ingest";
import { strings as mapTable, type MapStrings } from "./map";
import { strings as releaseTable, type ReleaseStrings } from "./release";
import { strings as reviewTable, type ReviewStrings } from "./review";
import { strings as scaleTable, type ScaleStrings } from "./scale";
import { strings as setupTable, type SetupStrings } from "./setup";

// The FULL string union: TESTS ONLY. Runtime code imports its own
// stage module (strings/<stage>), so the entry chunk carries only the
// shell's words (Phase-2 Wave 6 string split). Adding keys: land them
// in the stage's .json and .ts interface together; a key two or more
// stages reference belongs in app.
export interface Strings
  extends AppStrings,
    SetupStrings,
    IngestStrings,
    MapStrings,
    ReviewStrings,
    ScaleStrings,
    AskStrings,
    AnalyzeStrings,
    GraphStrings,
    CodebookStrings,
    ReleaseStrings {}

export const strings: Strings = {
  ...appTable,
  ...setupTable,
  ...ingestTable,
  ...mapTable,
  ...reviewTable,
  ...scaleTable,
  ...askTable,
  ...analyzeTable,
  ...graphTable,
  ...codebookTable,
  ...releaseTable,
};
